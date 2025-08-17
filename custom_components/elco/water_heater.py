from __future__ import annotations
from datetime import timedelta
from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import UnitOfTemperature, ATTR_TEMPERATURE
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

OPERATION_OFF = "off"
OPERATION_HEATPUMP = "heat_pump"
OPERATION_ECO = "eco"

SCAN_INTERVAL = timedelta(minutes=10)

async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    api = data["api"]

    async_add_entities([ElcoWaterHeater(coordinator, api)], update_before_add=True)


class ElcoWaterHeater(CoordinatorEntity, WaterHeaterEntity):

    def __init__(self, coordinator, api):
        super().__init__(coordinator)
        self._api = api
        self._name = "Elco DHW"
        self._attr_unique_id = "elco_dhw"
        self._attr_icon = "mdi:water-boiler"
        self._attr_operation_list = [OPERATION_OFF, OPERATION_HEATPUMP, OPERATION_ECO]
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_supported_features = (WaterHeaterEntityFeature.TARGET_TEMPERATURE |
                                         WaterHeaterEntityFeature.ON_OFF |
                                         WaterHeaterEntityFeature.OPERATION_MODE)

    @property
    def name(self):
        return self._name

    @property
    def current_temperature(self):
        return self.coordinator.data.get("data", {}).get("plantData", {}).get("dhwStorageTemp")

    @property
    def min_temp(self):
        return self.coordinator.data.get("data", {}).get("plantData", {}).get("dhwComfortTemp", {}).get("min")

    @property
    def max_temp(self):
        return self.coordinator.data.get("data", {}).get("plantData", {}).get("dhwComfortTemp", {}).get("max")

    @property
    def target_temperature_step(self):
        return self.coordinator.data.get("data", {}).get("plantData", {}).get("dhwComfortTemp", {}).get("step")

    async def async_set_temperature(self, **kwargs):
        if ATTR_TEMPERATURE not in kwargs:
            raise ValueError(f"Missing parameter {ATTR_TEMPERATURE}")

        self._attr_target_temperature = kwargs[ATTR_TEMPERATURE]
        self.async_write_ha_state()
        await self.hass.async_add_executor_job(self._api.set_dhw_temperature, kwargs[ATTR_TEMPERATURE])

    async def async_set_operation_mode(self, operation_mode):
        if operation_mode not in self.operation_list:
            raise ValueError(f"Invalid operation mode {operation_mode}")

        self._attr_current_operation = operation_mode
        self.async_write_ha_state()
        await self.hass.async_add_executor_job(
            self._api.set_dhw_mode, 0 if operation_mode == OPERATION_OFF else 1
        )

    async def async_update(self):
        self._attr_target_temperature = self.coordinator.data.get("data", {}).get("plantData", {}).get("dhwComfortTemp", {}).get("value")
        plant = self.coordinator.data.get("data", {}).get("plantData", {})
        dhw_mode = plant.get("dhwMode", {}).get("value", 0)
        heat_pump_on = plant.get("heatPumpOn", False)

        if dhw_mode == 1 and heat_pump_on:
            self._attr_current_operation = OPERATION_HEATPUMP
        elif dhw_mode == 1 and not heat_pump_on:
            self._attr_current_operation = OPERATION_ECO
        else:
            self._attr_current_operation = OPERATION_OFF