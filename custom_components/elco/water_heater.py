from __future__ import annotations
from datetime import timedelta
from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import (
    UnitOfTemperature,
    ATTR_TEMPERATURE,
)
from .const import DOMAIN

STATE_OFF = "off"
STATE_ON = "heat_pump"
STATE_IDLE = "eco"

SCAN_INTERVAL = timedelta(minutes=1)

async def async_setup_entry(hass, entry, async_add_entities):
    api = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ElcoWaterHeater(api, hass)], update_before_add=True)


class ElcoWaterHeater(WaterHeaterEntity):

    def __init__(self, api, hass):
        self._api = api
        self.hass = hass
        self._name = "Elco DHW"
        self._attr_unique_id = "elco_dhw"
        self._min_temp = 0.0
        self._max_temp = 0.0
        self._current_temp = 0.0
        self._target_temp = 0.0
        self._target_temperature_step = 0.0
        self._current_operation = STATE_OFF
        self._operation_mode = STATE_OFF

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        """Return a unique id for the device."""
        return "Elco_water_heater"

    @property
    def icon(self):
        """Return the icon to use in the frontend."""
        return "mdi:water-boiler"

    @property
    def current_operation(self):
        return self._current_operation

    @property
    def operation_list(self):
        """List of available operation modes."""
        return [STATE_ON, STATE_OFF]

    @property
    def min_temp(self):
        return self._min_temp

    @property
    def max_temp(self):
        return self._max_temp

    @property
    def current_temperature(self):
        return self._current_temp

    @property
    def target_temperature(self):
        return self._target_temp

    @property
    def temperature_unit(self):
        return UnitOfTemperature.CELSIUS

    @property
    def precision(self):
        """Return the precision of temperature for the device."""
        return 1.0

    @property
    def target_temperature_step(self):
        return self._target_temperature_step

    @property
    def supported_features(self):
        return WaterHeaterEntityFeature.TARGET_TEMPERATURE | WaterHeaterEntityFeature.ON_OFF | WaterHeaterEntityFeature.OPERATION_MODE

    async def async_set_temperature(self, **kwargs):
        if ATTR_TEMPERATURE not in kwargs:
            raise ValueError(f"Missing parameter {ATTR_TEMPERATURE}")
        await self.hass.async_add_executor_job(self._api.set_dhw_temperature, kwargs[ATTR_TEMPERATURE])
        self._target_temp = kwargs[ATTR_TEMPERATURE]
        self.async_write_ha_state()

    async def async_set_operation_mode(self, operation_mode):
        if operation_mode not in self.operation_list:
            raise ValueError(f"Invalid operation mode {operation_mode}")
        await self.hass.async_add_executor_job(self._api.set_dhw_operation_mode, operation_mode)
        self._current_operation = operation_mode
        self.async_write_ha_state()

    async def async_update(self):
        data = await self.hass.async_add_executor_job(self._api.get_hvac_data)

        self._current_temp = data["data"]["plantData"]["dhwStorageTemp"]
        self._target_temp = data["data"]["plantData"]["dhwComfortTemp"]["value"]
        self._min_temp = data["data"]["plantData"]["dhwComfortTemp"]["min"]
        self._max_temp = data["data"]["plantData"]["dhwComfortTemp"]["max"]
        self._target_temperature_step = data["data"]["plantData"]["dhwComfortTemp"]["step"]
        self._operation_mode = STATE_ON if (data["data"]["plantData"]["dhwMode"]["value"]) == 1 else STATE_OFF
        if self._operation_mode == STATE_ON and data["data"]["plantData"]["heatPumpOn"]:
            self._current_operation = STATE_ON
        if self._operation_mode == STATE_ON and data["data"]["plantData"]["heatPumpOn"] == False:
            self._current_operation = STATE_IDLE
        else:
            self._current_operation = STATE_OFF
