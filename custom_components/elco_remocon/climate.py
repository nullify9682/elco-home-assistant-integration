"""climate entity — one per heating zone."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ElcoConfigEntry
from .const import (
    ZONE_MODE_AUTO,
    ZONE_MODE_COMFORT,
    ZONE_MODE_OFF,
    ZONE_MODE_REDUCED,
)
from .coordinator import ITEM_CH_FLOW_TEMP_NAME, ElcoCoordinator
from .entity import ElcoZoneEntity

PRESET_COMFORT = "comfort"
PRESET_ECO = "eco"
PRESET_AUTO = "auto"

_PRESET_TO_MODE = {
    PRESET_AUTO: ZONE_MODE_AUTO,
    PRESET_COMFORT: ZONE_MODE_COMFORT,
    PRESET_ECO: ZONE_MODE_REDUCED,
}
_MODE_TO_PRESET = {mode: preset for preset, mode in _PRESET_TO_MODE.items()}

# Matches the official app's own post-write check (decompiled 2026-08-20,
# AsyncOperationHelper.withDefaultRunDelay -> 5000 ms, used by
# SynchronizeBsbZoneDataAsyncOperation right after any zone mode write).
_VERIFY_DELAY_SECONDS = 5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElcoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entities: list[ElcoZoneClimate] = []
    for coordinator in entry.runtime_data.values():
        for zone in coordinator.features.zones:
            entities.append(ElcoZoneClimate(coordinator, zone.number, zone.name))
    async_add_entities(entities)


class ElcoZoneClimate(ElcoZoneEntity, ClimateEntity):
    """A heating circuit."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    # HEAT/COOL removed 2026-08-20: the device has no heat-only or
    # cool-only mode (eBsbZoneMode is Off/Auto/Reduced/Comfort, confirmed
    # by decompiling the official app), only Off/Auto -- the machine alone
    # decides whether Auto behaves as heating or cooling. Selecting
    # HEAT/COOL used to force `is_cooling`, which doesn't match how the
    # device is actually driven and was never validated against it; now
    # that the write mirrors the app exactly, offering them as commands
    # would silently write the same thing as AUTO and let the entity
    # "snap back" to a different mode than the one clicked. `hvac_action`
    # still reports heating/cooling as a read-only reflection of hydraulic
    # state.
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.AUTO]
    _attr_preset_modes = [PRESET_AUTO, PRESET_COMFORT, PRESET_ECO]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_translation_key = "zone"

    def __init__(self, coordinator: ElcoCoordinator, zone: int, zone_name: str) -> None:
        super().__init__(coordinator, zone, "climate")
        # A single zone: the entity carries the device's name, with no
        # pointless suffix. Multiple zones: each one takes its own name.
        self._attr_name = None if len(coordinator.features.zones) == 1 else zone_name

    # -- reads ------------------------------------------------------------------

    @property
    def hvac_mode(self) -> HVACMode:
        """The zone's mode.

        Above the summer/winter switchover point, `heat` is impossible: the
        machine switches to hydraulic heating mode to charge the tank and
        stays there, which would otherwise display "heating" in the middle
        of August.
        """
        zone = self.zone_state
        if zone is None:
            return HVACMode.OFF
        return HVACMode(self.coordinator.zone_hvac_mode(zone))

    @property
    def hvac_action(self) -> HVACAction:
        zone = self.zone_state
        if zone is None:
            return HVACAction.OFF
        return HVACAction(self.coordinator.zone_action(zone))

    @property
    def preset_mode(self) -> str | None:
        zone = self.zone_state
        return _MODE_TO_PRESET.get(zone.mode) if zone else None

    @property
    def current_temperature(self) -> float | None:
        """The zone's temperature.

        The room sensor when the machine has one; otherwise the circuit's
        FLOW temperature, which is what the pump actually produces. Never
        the outside temperature: comparing it to a room setpoint makes no
        sense, and that's what version 0.1 did.
        """
        zone = self.zone_state
        if zone is None:
            return None
        if zone.has_room_sensor and zone.room_temp:
            return zone.room_temp
        data = self.coordinator.data
        return data.item_value(ITEM_CH_FLOW_TEMP_NAME) if data else None

    @property
    def target_temperature(self) -> float | None:
        zone = self.zone_state
        return zone.active_comfort.value if zone else None

    @property
    def min_temp(self) -> float:
        zone = self.zone_state
        value = zone.active_comfort.min if zone else None
        return value if value is not None else 5.0

    @property
    def max_temp(self) -> float:
        zone = self.zone_state
        value = zone.active_comfort.max if zone else None
        return value if value is not None else 35.0

    @property
    def target_temperature_step(self) -> float:
        zone = self.zone_state
        value = zone.active_comfort.step if zone else None
        return value if value is not None else 0.5

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        zone = self.zone_state
        if zone is None:
            return {}
        return {
            "heating_setpoint": zone.ch_comfort.value,
            "cooling_setpoint": zone.cool_comfort.value,
            "reduced_setpoint": zone.active_reduced.value,
        }

    # -- writes ---------------------------------------------------------------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        zone = self.zone_state
        if zone is None:
            return

        # `is_cooling` decides WHICH of the two parameters is written.
        # Getting it wrong sets the heating setpoint in the middle of
        # summer, with no visible error.
        # KNOWN GAP (2026-08-20, out of scope here): under `auto`, both
        # heating and cooling can legitimately matter at once, but this
        # still picks a single setpoint from the CURRENT hydraulic flag --
        # effectively a coin flip between the two if that flag flips
        # between reads. Not touched by this session's fix; revisit if a
        # temperature set is ever reported as landing on the wrong setpoint.
        is_cooling = zone.cooling_on
        await self.coordinator.client.async_set_zone_temperatures(
            self.coordinator.plant.gateway_id,
            self._zone,
            is_cooling=is_cooling,
            comfort=temperature,
            reduced=zone.active_reduced.value,
            old_comfort=zone.active_comfort.value,
            old_reduced=zone.active_reduced.value,
        )
        self.coordinator.async_set_optimistic(f"zone_{self._zone}_comfort", temperature)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        # `is_cooling` always mirrors the CURRENT hydraulic state
        # (`zone.cooling_on`), never the requested hvac_mode -- matching
        # the official app exactly (SetBsbZoneModeAsyncOperationBase passes
        # `mPrevZoneData.isCoolingActive()` regardless of the target mode).
        # The app has no "heat only" / "cool only" button at all: only
        # Off/Auto/Reduced/Comfort, and the machine alone decides whether
        # Auto behaves as heating or cooling. Forcing `is_cooling` from
        # HVACMode.HEAT/COOL (as this used to) doesn't match how the device
        # is actually driven and was never validated against it.
        zone = self.zone_state
        if zone is None:
            return
        mode = ZONE_MODE_OFF if hvac_mode == HVACMode.OFF else ZONE_MODE_AUTO
        await self._async_write_zone_mode(zone.cooling_on, mode)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        zone = self.zone_state
        mode = _PRESET_TO_MODE.get(preset_mode)
        if zone is None or mode is None:
            return
        await self._async_write_zone_mode(zone.cooling_on, mode)

    async def _async_write_zone_mode(self, is_cooling: bool, mode: int) -> None:
        """Writes the zone's mode and confirms the machine actually took it.

        Mirrors the official app (decompiled 2026-08-20:
        SetBsbZoneModeAsyncOperationBase writes once, exactly like this --
        `is_cooling` picks whichever circuit is CURRENTLY active, there is
        no second write). Confirmed by David: what looked like two
        independent circuit registers is not how the app drives this API.

        What the app does have, and this integration didn't: right after
        writing, it waits 5 s and enqueues SynchronizeBsbZoneDataAsyncOperation,
        which force-refreshes past any server-side cache and compares the
        result to what was requested -- logging a warning and failing
        visibly if it doesn't match. That step exists because a write can
        return HTTP 200 and still not change anything (PROTOCOL.md already
        warns the sibling /R2/ endpoint serves a stale cache right after a
        write). The 2026-08-20 incident -- a cooling zone switching to
        heating the instant it was turned off -- is consistent with exactly
        that: the write never really landed, and Home Assistant had no way
        to notice for up to a full poll interval. This reproduces the same
        check. No retry: the app doesn't retry either, it just fails loudly
        and leaves the retry to the user.
        """
        zone = self.zone_state
        if zone is None:
            return
        await self.coordinator.client.async_set_zone_mode(
            self.coordinator.plant.gateway_id,
            self._zone,
            is_cooling=is_cooling,
            mode=mode,
            old_mode=zone.mode,
        )
        await asyncio.sleep(_VERIFY_DELAY_SECONDS)
        # Deliberately no async_set_optimistic: overlaying the requested
        # value here would mask exactly the failure this checks for, since
        # the overlay reads back as "confirmed" regardless of what the
        # machine actually did (OPTIMISTIC_TTL, 150 s).
        await self.coordinator.async_refresh()
        if not self.coordinator.last_update_success:
            # The verification READ failed (network hiccup, API error) --
            # `coordinator.data` still holds the last known-good snapshot,
            # so checking `zone.mode` here would compare against stale data
            # and could report a perfectly good write as failed. Silently
            # not confirming is preferable to a false alarm; the next
            # regular poll will show the true state either way.
            return
        zone = self.zone_state
        if zone is None or zone.mode != mode:
            raise HomeAssistantError(
                f"the heat pump did not confirm mode {mode} on zone {self._zone}"
            )

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)
