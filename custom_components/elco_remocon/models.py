"""Domain objects, independent of the transport.

Entities and the coordinator only ever handle these — never raw JSON
dictionaries. This is what would allow swapping the underlying API without
touching the platforms.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .const import DHW_MODE_OFF


@dataclass(frozen=True, slots=True)
class Plant:
    """An installation on the account."""

    gateway_id: str
    name: str
    serial: str | None = None
    firmware: str | None = None
    system_type: int | None = None
    # Best indicator of generation: below 8, consumption reports and some
    # dataItems don't respond (PROTOCOL.md §7).
    api_version: int | None = None

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Plant:
        gateway_id = str(raw.get("gwId") or "").upper()
        return cls(
            gateway_id=gateway_id,
            name=str(raw.get("plantName") or "").strip() or gateway_id,
            serial=raw.get("gwSerial"),
            firmware=raw.get("gwFwVer"),
            system_type=raw.get("gwSysType"),
            api_version=raw.get("mqttApiVersion"),
        )


@dataclass(frozen=True, slots=True)
class ZoneInfo:
    """Zone description, from `plants/{gw}/features`."""

    number: int
    name: str
    hidden: bool = False

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> ZoneInfo:
        number = int(raw.get("num") or 1)
        return cls(
            number=number,
            name=str(raw.get("name") or "").strip() or f"Zone {number}",
            hidden=bool(raw.get("isHidden")),
        )


@dataclass(frozen=True, slots=True)
class Features:
    """Declared capabilities of the installation."""

    zones: tuple[ZoneInfo, ...] = ()
    heat_pump: bool = False
    has_metering: bool = False
    distinct_heat_cool_setpoints: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Features:
        return cls(
            zones=tuple(
                ZoneInfo.from_json(zone)
                for zone in raw.get("zones") or []
                if not zone.get("isHidden")
            )
            or (ZoneInfo(number=1, name="Zone 1"),),
            heat_pump=bool(raw.get("hpSys")),
            has_metering=bool(raw.get("hasMetering")),
            distinct_heat_cool_setpoints=bool(raw.get("distinctHeatCoolSetpoints")),
            raw=raw,
        )


@dataclass(frozen=True, slots=True)
class Bounded:
    """A numeric value with its bounds, as the API provides them."""

    value: float | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None

    @classmethod
    def from_json(cls, raw: Any) -> Bounded:
        if isinstance(raw, dict):
            return cls(
                value=raw.get("value"),
                min=raw.get("min"),
                max=raw.get("max"),
                step=raw.get("step"),
            )
        if isinstance(raw, (int, float)):
            return cls(value=float(raw))
        return cls()


@dataclass(frozen=True, slots=True)
class DataItem:
    """A measurement returned by `dataItems/get`."""

    name: str
    zone: int
    value: float | None
    unit: str | None = None
    decimals: int | None = None
    minimum: float | None = None
    maximum: float | None = None
    invalid: bool = False
    error: bool = False

    @property
    def available(self) -> bool:
        """True if the value is actually usable.

        `invalid` isn't enough: some counters return an overflowed 32-bit
        integer (for instance 4294966879 for −417) without raising any flag.
        The declared bounds filter those out.

        But the bounds are only honored if they actually delimit something:
        boolean items report `min == max == 0`, and a naive comparison would
        reject their only interesting state. Measured on `IsHeatingPumpOn`,
        which is 1 with bounds [0, 0].
        """
        if self.value is None or self.invalid or self.error:
            return False
        if self.minimum is None or self.maximum is None:
            return True
        if self.maximum <= self.minimum:
            return True
        return self.minimum <= self.value <= self.maximum

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> DataItem:
        return cls(
            name=str(raw.get("id") or ""),
            zone=int(raw.get("zone") or 0),
            value=raw.get("value"),
            unit=raw.get("unit"),
            decimals=raw.get("decimals"),
            minimum=raw.get("min"),
            maximum=raw.get("max"),
            invalid=bool(raw.get("invalid")),
            error=bool(raw.get("error")),
        )


@dataclass
class ZoneState:
    """Current state of a zone."""

    number: int
    mode: int | None = None
    heating_on: bool = False
    cooling_on: bool = False
    heat_or_cool_request: bool = False
    has_room_sensor: bool = False
    room_temp: float | None = None
    ch_comfort: Bounded = field(default_factory=Bounded)
    ch_reduced: Bounded = field(default_factory=Bounded)
    cool_comfort: Bounded = field(default_factory=Bounded)
    cool_reduced: Bounded = field(default_factory=Bounded)

    @property
    def active_comfort(self) -> Bounded:
        """The comfort setpoint that matches the current mode.

        Heating and cooling have SEPARATE setpoints, with different bounds.
        Reading or writing the wrong one is this protocol's most insidious
        defect: in summer, adjusting the temperature silently acts on the
        heating parameter, with nothing to signal it.
        """
        return self.cool_comfort if self.cooling_on else self.ch_comfort

    @property
    def active_reduced(self) -> Bounded:
        return self.cool_reduced if self.cooling_on else self.ch_reduced

    @classmethod
    def from_json(cls, number: int, raw: dict[str, Any]) -> ZoneState:
        mode = raw.get("mode")
        return cls(
            number=number,
            mode=mode.get("value") if isinstance(mode, dict) else mode,
            heating_on=bool(raw.get("heatingOn")),
            cooling_on=bool(raw.get("coolingOn")),
            heat_or_cool_request=bool(raw.get("heatOrCoolReq")),
            has_room_sensor=bool(raw.get("hasRoomSens")),
            room_temp=raw.get("roomTemp"),
            ch_comfort=Bounded.from_json(raw.get("chComfTemp")),
            ch_reduced=Bounded.from_json(raw.get("chRedTemp")),
            cool_comfort=Bounded.from_json(raw.get("coolComfTemp")),
            cool_reduced=Bounded.from_json(raw.get("coolRedTemp")),
        )


@dataclass
class PlantState:
    """Current state of an installation, as consumed by entities."""

    gateway_id: str
    outside_temp: float | None = None
    has_outside_probe: bool = False
    dhw_temp: float | None = None
    has_dhw_probe: bool = False
    dhw_comfort: Bounded = field(default_factory=Bounded)
    dhw_reduced: Bounded = field(default_factory=Bounded)
    dhw_mode: int | None = None
    dhw_enabled: bool = False
    # WARNING: flag at the INSTALLATION level. True as soon as the pump is
    # running, for heating, cooling, OR DHW. Crossing it with `dhw_mode` to
    # infer "DHW is heating" is WRONG — that's the bug that motivated this
    # rewrite (PROTOCOL.md §4).
    heat_pump_on: bool = False
    flame: bool = False
    zones: dict[int, ZoneState] = field(default_factory=dict)
    items: dict[str, DataItem] = field(default_factory=dict)
    # Inferred from the tank's slope, absent a field that states it directly
    # (see `coordinator.ElcoCoordinator._is_dhw_producing`).
    dhw_heating: bool = False

    @property
    def dhw_off(self) -> bool:
        return self.dhw_mode == DHW_MODE_OFF

    def item(self, name: str) -> DataItem | None:
        item = self.items.get(name)
        return item if item is not None and item.available else None

    def item_value(self, name: str) -> float | None:
        item = self.item(name)
        return item.value if item else None

    @classmethod
    def from_json(cls, gateway_id: str, raw: dict[str, Any]) -> PlantState:
        dhw_mode = raw.get("dhwMode")
        state = cls(
            gateway_id=gateway_id,
            outside_temp=raw.get("outTemp"),
            has_outside_probe=bool(raw.get("hasOutTemp")),
            dhw_temp=raw.get("dhwTemp"),
            has_dhw_probe=bool(raw.get("hasDhwTemp")),
            dhw_comfort=Bounded.from_json(raw.get("dhwComfTemp")),
            dhw_reduced=Bounded.from_json(raw.get("dhwReduTemp")),
            dhw_mode=dhw_mode.get("value") if isinstance(dhw_mode, dict) else dhw_mode,
            dhw_enabled=bool(raw.get("dhwEnabled")),
            heat_pump_on=bool(raw.get("hpOn")),
            flame=bool(raw.get("flame")),
        )
        for key, zone_raw in (raw.get("zones") or {}).items():
            try:
                number = int(key)
            except (TypeError, ValueError):
                continue
            state.zones[number] = ZoneState.from_json(number, zone_raw)
        return state
