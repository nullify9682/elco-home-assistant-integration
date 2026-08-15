"""Refresh cycle and state logic."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ElcoApiError, ElcoAuthError, ElcoClient, build_state
from .const import (
    DOMAIN,
    OP_COOLING,
    OP_HEATING,
    OP_IDLE,
    OP_OFF,
    OPTIMISTIC_REFRESH_DELAYS,
    OPTIMISTIC_TTL,
    POLLED_PLANT_ITEMS,
    POLLED_ZONE_ITEMS,
    ZONE_MODE_OFF,
)
from .models import Bounded, Features, Plant, PlantState, ZoneState

_LOGGER = logging.getLogger(__name__)

# Names as the server returns them in `dataItems` — the request is made by
# number, the response is indexed by name.
ITEM_CH_FLOW_TEMP_NAME = "ChFlowTemp"
ITEM_IS_HEATING_PUMP_ON_NAME = "IsHeatingPumpOn"
ITEM_CH_FLOW_SETPOINT_NAME = "ChFlowSetpointTemp"
ITEM_HEATING_CIRCUIT_PRESSURE_NAME = "HeatingCircuitPressure"


@dataclass(slots=True)
class _Optimistic:
    """A value displayed before the server confirms it."""

    value: float
    expires: float


class ElcoCoordinator(DataUpdateCoordinator[PlantState]):
    """Refreshes an installation and decides what it's actually doing.

    The API never states outright whether the machine is heating the house
    or the tank. It only provides signals that must be read carefully —
    two of which are easy to misinterpret:

    - `hpOn` is at the INSTALLATION level: true as soon as the pump is
      running, for heating, cooling, or hot water.
    - `heatingOn` / `coolingOn` describe the machine's HYDRAULIC mode, not
      the zone's activity. Measured on 2026-08-15: `heatingOn` was true at
      30 °C outside, because the machine had left cooling mode to charge
      the tank.

    The only signal that says what the circuit actually wants is
    `heatOrCoolReq`.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: ElcoClient,
        plant: Plant,
        features: Features,
        scan_interval: timedelta,
        summer_switchover: float,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {plant.name}",
            update_interval=scan_interval,
        )
        self.client = client
        self.plant = plant
        self.features = features
        self.summer_switchover = summer_switchover
        self._optimistic: dict[str, _Optimistic] = {}

    # -- cycle --------------------------------------------------------------------

    async def _async_update_data(self) -> PlantState:
        gw = self.plant.gateway_id
        try:
            raw = await self.client.async_get_plant_data(gw, zone=self._first_zone)
            state = build_state(gw, raw)

            items = [(item, 0) for item in POLLED_PLANT_ITEMS]
            for zone in self.features.zones:
                items += [(item, zone.number) for item in POLLED_ZONE_ITEMS]
            state.items = await self.client.async_get_data_items(
                gw, self.features.raw, items
            )
            # The server silently drops measurements the gateway doesn't
            # support, and a value it does return can still be discarded by
            # its own bounds. Logging all three lists is the only way to
            # understand why an entity fails to appear.
            _LOGGER.debug(
                "dataItems requested=%s received=%s usable=%s",
                [item_id for item_id, _ in items],
                sorted(state.items),
                sorted(name for name, item in state.items.items() if item.available),
            )
        except ElcoAuthError as err:
            # Triggers Home Assistant's re-authentication flow instead of
            # retrying indefinitely with stale credentials.
            raise ConfigEntryAuthFailed(str(err)) from err
        except ElcoApiError as err:
            raise UpdateFailed(str(err)) from err

        state.dhw_heating = self._is_dhw_producing(state)
        self._apply_optimistic(state)
        return state

    @property
    def _first_zone(self) -> int:
        return self.features.zones[0].number if self.features.zones else 1

    # -- interpretation -------------------------------------------------------

    @staticmethod
    def _is_dhw_producing(state: PlantState) -> bool:
        """Is the machine running for the tank?

        The machine is running (`hpOn`) while NO zone is requesting anything
        (`heatOrCoolReq`): it can only be working for hot water. No threshold,
        no season, no inertia — this is true from the very first reading of
        the cycle.

        Inferring this from the tank temperature's slope was tried and
        abandoned: it took 30 minutes to react, because the tank starts by
        COOLING DOWN when charging begins.
        """
        if state.dhw_off or not state.heat_pump_on:
            return False
        return not any(zone.heat_or_cool_request for zone in state.zones.values())

    def _is_heating_season(self, state: PlantState) -> bool:
        """Above the switchover point, the house cannot be heated.

        Guard against the hydraulic mode: the machine switches to heating to
        produce hot water and then stays there, which would otherwise
        display "heating" in the middle of August.
        """
        if state.outside_temp is None:
            return True
        return state.outside_temp <= self.summer_switchover

    def zone_action(self, zone: ZoneState, state: PlantState | None = None) -> str:
        """What a zone is actually doing, in `HVACAction` vocabulary."""
        state = state or self.data
        if state is None or zone.mode == ZONE_MODE_OFF:
            return OP_OFF
        if not zone.heat_or_cool_request:
            return OP_IDLE
        if zone.cooling_on:
            return OP_COOLING
        if zone.heating_on and self._is_heating_season(state):
            return OP_HEATING
        return OP_IDLE

    def zone_hvac_mode(self, zone: ZoneState, state: PlantState | None = None) -> str:
        """The mode shown by the thermostat.

        `auto` when the machine is running without being able to assert the
        mode — that's more honest than announcing "heating" at 30 °C outside.
        """
        state = state or self.data
        if state is None or zone.mode == ZONE_MODE_OFF:
            return "off"
        if zone.cooling_on:
            return "cool"
        if zone.heating_on and self._is_heating_season(state):
            return "heat"
        return "auto"

    def operation(self, state: PlantState | None = None) -> str:
        """State of the CIRCUIT, and only the circuit.

        Reads `idle` while the tank is being charged: `dhw_operation` carries
        that information instead. The two sensors describe two independent
        axes, not two views of the same state.
        """
        state = state or self.data
        if state is None:
            return OP_IDLE
        zones = list(state.zones.values())
        if not zones:
            return OP_IDLE
        if all(zone.mode == ZONE_MODE_OFF for zone in zones):
            return OP_OFF
        for zone in zones:
            action = self.zone_action(zone, state)
            if action in (OP_HEATING, OP_COOLING):
                return action
        return OP_IDLE

    def dhw_operation(self, state: PlantState | None = None) -> str:
        """State of the TANK, in `HVACAction` vocabulary."""
        state = state or self.data
        if state is None:
            return OP_IDLE
        if state.dhw_off:
            return OP_OFF
        return OP_HEATING if state.dhw_heating else OP_IDLE

    # -- optimistic display ---------------------------------------------------

    @callback
    def async_set_optimistic(self, key: str, value: float) -> None:
        """Immediately shows a value that was just written, then re-reads.

        The window is bounded: past `OPTIMISTIC_TTL`, the server's value
        takes over no matter what. We never lie for longer than that, even
        if the write was refused by the machine.
        """
        self._optimistic[key] = _Optimistic(
            value=value,
            expires=dt_util.utcnow().timestamp() + OPTIMISTIC_TTL,
        )
        if self.data is not None:
            self._apply_optimistic(self.data)
            self.async_update_listeners()

        for delay in OPTIMISTIC_REFRESH_DELAYS:
            async_call_later(self.hass, delay, self._async_scheduled_refresh)

    async def _async_scheduled_refresh(self, _now) -> None:
        await self.async_request_refresh()

    def _apply_optimistic(self, state: PlantState) -> None:
        """Overlays the server state with pending, non-expired values."""
        now = dt_util.utcnow().timestamp()
        for key, entry in list(self._optimistic.items()):
            if entry.expires <= now:
                self._optimistic.pop(key, None)
                continue
            _overlay(state, key, entry.value)


def _overlay(state: PlantState, key: str, value: float) -> None:
    """Applies an optimistic value onto the state."""
    if key == "dhw_comfort":
        state.dhw_comfort = _with_value(state.dhw_comfort, value)
    elif key == "dhw_mode":
        state.dhw_mode = int(value)
    elif key.startswith("zone_"):
        parts = key.split("_")
        try:
            number = int(parts[1])
        except (IndexError, ValueError):
            return
        zone = state.zones.get(number)
        if zone is None:
            return
        what = "_".join(parts[2:])
        if what == "comfort":
            if zone.cooling_on:
                zone.cool_comfort = _with_value(zone.cool_comfort, value)
            else:
                zone.ch_comfort = _with_value(zone.ch_comfort, value)
        elif what == "mode":
            zone.mode = int(value)


def _with_value(bounded: Bounded, value: float) -> Bounded:
    return Bounded(
        value=float(value), min=bounded.min, max=bounded.max, step=bounded.step
    )
