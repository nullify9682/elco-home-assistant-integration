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
from .const import DOMAIN

SCAN_INTERVAL = timedelta(minutes=1)

async def async_setup_entry(hass, entry, async_add_entities):
    api = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([HeatPumpClimate(api, hass)], update_before_add=True)


class HeatPumpClimate(ClimateEntity):

    def __init__(self, api, hass):
        self._api = api
        self.hass = hass
        self._name = "Elco Heat Pump"
        self._attr_unique_id = "elco_heat_pump"
        self._is_cooling = True
        self._is_heating = False
        self._is_heat_pump_running = False
        self._hvac_mode = HVACMode.AUTO
        self._target_temp = 0.0
        self._outside_temp = 0.0
        self._min_temp = 0.0
        self._max_temp = 0.0

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        """Return a unique id for the device."""
        return "Elco_heat_pump"

    @property
    def icon(self):
        if self._hvac_mode == HVACMode.OFF:
            return "mdi:radiator-off"
        if self._is_heat_pump_running and self._is_heating:
            return "mdi:fire"
        elif self._is_heat_pump_running and self._is_cooling:
            return "mdi:snowflake"
        else:
            return "mdi:radiator-disabled"
    @property
    def temperature_unit(self):
        return UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self):
        return self._outside_temp

    @property
    def target_temperature(self):
        return self._target_temp

    @property
    def min_temp(self):
        return self._min_temp

    @property
    def max_temp(self):
        return self._max_temp

    @property
    def hvac_mode(self):
        return self._hvac_mode

    @property
    def hvac_modes(self):
        return [HVACMode.AUTO, HVACMode.OFF]

    @property
    def current_hvac_action(self):
        if self._hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if self._is_heat_pump_running and self._is_heating:
            return HVACAction.HEATING
        elif self._is_heat_pump_running and self._is_cooling:
            return HVACAction.COOLING
        return HVACAction.IDLE

    @property
    def supported_features(self):
        return (ClimateEntityFeature.TARGET_TEMPERATURE |
                ClimateEntityFeature.TURN_ON |
                ClimateEntityFeature.TURN_OFF)

    async def async_set_hvac_mode(self, hvac_mode):
        self._hvac_mode = hvac_mode
        if hvac_mode == HVACMode.AUTO:
            self.hass.async_add_executor_job(self._api.turn_on)
        if hvac_mode == HVACMode.OFF:
            self.hass.async_add_executor_job(self._api.turn_off)
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs):
        if ATTR_TEMPERATURE not in kwargs:
            raise ValueError(f"Missing parameter {ATTR_TEMPERATURE}")
        await self.hass.async_add_executor_job(self._api.set_temperature, kwargs[ATTR_TEMPERATURE], self._target_temp)
        self._target_temp = kwargs[ATTR_TEMPERATURE]
        self.async_write_ha_state()

    async def async_update(self):
        data = await self.hass.async_add_executor_job(self._api.get_hvac_data)
        self._is_cooling = data["data"]["zoneData"]["isCoolingActive"]
        self._is_heating = data["data"]["zoneData"]["isHeatingActive"]
        self._is_heat_pump_running = data["data"]["zoneData"]["heatOrCoolRequest"]
        if data["data"]["zoneData"]["mode"]["value"] == 0:
            self._hvac_mode = HVACMode.OFF
        else:
            self._hvac_mode = HVACMode.AUTO
        self._target_temp = data["data"]["zoneData"]["chComfortTemp"]["value"]
        self._min_temp = data["data"]["zoneData"]["chComfortTemp"]["min"]
        self._max_temp = data["data"]["zoneData"]["chComfortTemp"]["max"]
        self._outside_temp = data["data"]["plantData"]["outsideTemp"]
