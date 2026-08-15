"""Objets de domaine, indépendants du transport.

Les entités et le coordinator ne manipulent que ceci — jamais des dictionnaires
JSON bruts. C'est ce qui permettrait de changer d'API sous-jacente sans toucher
aux plateformes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .const import DHW_MODE_OFF


@dataclass(frozen=True, slots=True)
class Plant:
    """Une installation du compte."""

    gateway_id: str
    name: str
    serial: str | None = None
    firmware: str | None = None
    system_type: int | None = None
    # Meilleur indicateur de génération : sous 8, les rapports de consommation
    # et une partie des dataItems ne répondent pas (PROTOCOL.md §7).
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
    """Description d'une zone, issue de `plants/{gw}/features`."""

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
    """Capacités déclarées de l'installation."""

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
    """Une valeur numérique avec ses bornes, telles que l'API les fournit."""

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
    """Une mesure renvoyée par `dataItems/get`."""

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
        """Vrai si la valeur est réellement exploitable.

        `invalid` ne suffit pas : certains compteurs renvoient un entier 32
        bits en débordement (par exemple 4294966879 pour −417) sans lever le
        moindre drapeau. Les bornes déclarées les écartent.

        Mais les bornes ne sont retenues que si elles délimitent réellement
        quelque chose : les items booléens annoncent `min == max == 0`, et une
        comparaison naïve rejetterait leur seul état intéressant. Mesuré sur
        `IsHeatingPumpOn`, qui vaut 1 avec des bornes [0, 0].
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
    """État courant d'une zone."""

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
        """La consigne de confort qui correspond au mode courant.

        Chauffage et refroidissement ont des consignes SÉPARÉES, avec des
        bornes différentes. Lire ou écrire la mauvaise est le défaut le plus
        pernicieux de ce protocole : en été, régler la température agit sur le
        paramètre de chauffage sans que rien ne le signale.
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
    """État courant d'une installation, tel que les entités le consomment."""

    gateway_id: str
    outside_temp: float | None = None
    has_outside_probe: bool = False
    dhw_temp: float | None = None
    has_dhw_probe: bool = False
    dhw_comfort: Bounded = field(default_factory=Bounded)
    dhw_reduced: Bounded = field(default_factory=Bounded)
    dhw_mode: int | None = None
    dhw_enabled: bool = False
    # ATTENTION : drapeau au niveau de l'INSTALLATION. Vrai dès que la pompe
    # tourne, pour le chauffage, le refroidissement OU l'ECS. Le croiser avec
    # `dhw_mode` pour en déduire « l'ECS chauffe » est FAUX — c'est le bug qui
    # a motivé cette réécriture (PROTOCOL.md §4).
    heat_pump_on: bool = False
    flame: bool = False
    zones: dict[int, ZoneState] = field(default_factory=dict)
    items: dict[str, DataItem] = field(default_factory=dict)
    # Déduit de la pente du ballon, faute d'un champ le disant (voir
    # `coordinator.ElcoCoordinator._infer_dhw_heating`).
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
