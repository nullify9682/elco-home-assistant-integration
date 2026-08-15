"""Entité climate — une par zone de chauffage."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
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
    """Un circuit de chauffage."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT, HVACMode.COOL]
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
        # Une seule zone : l'entité porte le nom de l'appareil, sans suffixe
        # inutile. Plusieurs zones : chacune prend son nom.
        self._attr_name = None if len(coordinator.features.zones) == 1 else zone_name

    # -- lecture ------------------------------------------------------------

    @property
    def hvac_mode(self) -> HVACMode:
        """Régime de la zone.

        Au-dessus du point de bascule été/hiver, `heat` est impossible : la
        machine passe en mode hydraulique chauffage pour charger le ballon et
        y reste, ce qui afficherait « chauffage » en plein mois d'août.
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
        """Température de la zone.

        La sonde d'ambiance quand la machine en a une ; sinon la température de
        DÉPART du circuit, qui est ce que la pompe produit réellement. Jamais
        la température extérieure : la comparer à une consigne d'ambiance n'a
        aucun sens, et c'est ce que faisait la version 0.1.
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

    # -- écriture -----------------------------------------------------------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        zone = self.zone_state
        if zone is None:
            return

        # `is_cooling` décide LEQUEL des deux paramètres est écrit. Se tromper
        # règle la consigne de chauffage en plein été, sans erreur visible.
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
        zone = self.zone_state
        if zone is None:
            return

        if hvac_mode == HVACMode.OFF:
            mode, is_cooling = ZONE_MODE_OFF, zone.cooling_on
        elif hvac_mode == HVACMode.COOL:
            mode, is_cooling = ZONE_MODE_AUTO, True
        elif hvac_mode == HVACMode.HEAT:
            mode, is_cooling = ZONE_MODE_AUTO, False
        else:
            mode, is_cooling = ZONE_MODE_AUTO, zone.cooling_on

        await self.coordinator.client.async_set_zone_mode(
            self.coordinator.plant.gateway_id,
            self._zone,
            is_cooling=is_cooling,
            mode=mode,
            old_mode=zone.mode,
        )
        self.coordinator.async_set_optimistic(f"zone_{self._zone}_mode", mode)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        zone = self.zone_state
        mode = _PRESET_TO_MODE.get(preset_mode)
        if zone is None or mode is None:
            return
        await self.coordinator.client.async_set_zone_mode(
            self.coordinator.plant.gateway_id,
            self._zone,
            is_cooling=zone.cooling_on,
            mode=mode,
            old_mode=zone.mode,
        )
        self.coordinator.async_set_optimistic(f"zone_{self._zone}_mode", mode)

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.AUTO)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)
