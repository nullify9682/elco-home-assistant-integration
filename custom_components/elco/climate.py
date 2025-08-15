from __future__ import annotations
from datetime import timedelta
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    UnitOfTemperature,
    ATTR_TEMPERATURE,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

SCAN_INTERVAL = timedelta(minutes=5)

async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    api = data["api"]
    async_add_entities([HeatPumpClimate(coordinator, api)], update_before_add=True)


class HeatPumpClimate(CoordinatorEntity, ClimateEntity):
    def __init__(self, coordinator, api):
        super().__init__(coordinator)
        self._api = api
        self._name = "Elco Heat Pump"
        self._attr_unique_id = "elco_heat_pump"
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_hvac_modes = [HVACMode.AUTO, HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF]
        self._attr_icon = "mdi:fan"
        self._attr_supported_features = (ClimateEntityFeature.TARGET_TEMPERATURE |
                                         ClimateEntityFeature.TURN_ON |
                                         ClimateEntityFeature.TURN_OFF)

    @property
    def name(self):
        return self._name

    @property
    def current_temperature(self):
        return self.coordinator.data.get("data", {}).get("plantData", {}).get("outsideTemp")

    @property
    def min_temp(self):
        return self.coordinator.data.get("data", {}).get("zoneData", {}).get("chComfortTemp", {}).get("min")

    @property
    def max_temp(self):
        return self.coordinator.data.get("data", {}).get("zoneData", {}).get("chComfortTemp", {}).get("max")

    async def async_set_hvac_mode(self, hvac_mode):
        await self.hass.async_add_executor_job(self._api.set_hvac_mode, self._attr_hvac_mode, hvac_mode)
        self._attr_hvac_mode = hvac_mode
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs):
        if ATTR_TEMPERATURE not in kwargs:
            raise ValueError(f"Missing parameter {ATTR_TEMPERATURE}")
        self._attr_target_temperature = kwargs[ATTR_TEMPERATURE]
        self.async_write_ha_state()
        await self.hass.async_add_executor_job(self._api.set_hvac_temperature, kwargs[ATTR_TEMPERATURE], self._attr_target_temperature)

    async def async_update(self):
        self._attr_target_temperature = self.coordinator.data.get("data", {}).get("zoneData", {}).get("chComfortTemp", {}).get("value")
        self._attr_hvac_mode = await self.hass.async_add_executor_job(self._api.get_hvac_mode)

        is_heat_pump_running = self.coordinator.data.get("data", {}).get("zoneData", {}).get("heatOrCoolRequest")
        is_heating = self.coordinator.data.get("data", {}).get("zoneData", {}).get("isHeatingActive")
        is_cooling = self.coordinator.data.get("data", {}).get("zoneData", {}).get("isCoolingActive")
        if self._attr_hvac_mode == HVACMode.OFF:
            self._attr_hvac_action = HVACAction.OFF
        elif is_heat_pump_running and is_heating:
            self._attr_hvac_action = HVACAction.HEATING
        elif is_heat_pump_running and is_cooling:
            self._attr_hvac_action = HVACAction.COOLING
        else:
            self._attr_hvac_action = HVACAction.IDLE