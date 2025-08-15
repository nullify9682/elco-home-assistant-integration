from __future__ import annotations
from homeassistant.components.sensor import SensorEntity
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"] # use existing coordinator

    entities = [
        ElcoOutsideTempSensor(coordinator),
        ElcoBoilerTempSensor(coordinator),
        ElcoHvacOperationSensor(coordinator),
        ElcoWaterHeaterOpSensor(coordinator),
    ]
    async_add_entities(entities)


class BaseElcoSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, name, unique_id):
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = unique_id

    @property
    def data(self):
        return self.coordinator.data or {}


class ElcoOutsideTempSensor(BaseElcoSensor):
    def __init__(self, coordinator):
        super().__init__(coordinator, "Elco Ext Temp", "elco_outside_temp")
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_icon = "mdi:thermometer"

    @property
    def native_value(self):
        return self.coordinator.data.get("data", {}).get("plantData", {}).get("outsideTemp")

class ElcoBoilerTempSensor(BaseElcoSensor):
    def __init__(self, coordinator):
        super().__init__(coordinator, "Boiler Temp", "elco_boiler_temp")
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_icon = "mdi:thermometer"

    @property
    def native_value(self):
        return (
            self.coordinator.data.get("data", {}).get("plantData", {}).get("dhwStorageTemp")
        )

class ElcoHvacOperationSensor(BaseElcoSensor):
    def __init__(self, coordinator):
        super().__init__(coordinator, "Heat Pump op", "elco_hvac_op")

    @property
    def native_value(self):
        zone = self.coordinator.data.get("data", {}).get("zoneData", {})
        heating = zone.get("isHeatingActive")
        cooling = zone.get("isCoolingActive")
        heat_pump_active = zone.get("heatOrCoolRequest")

        if not heat_pump_active:
            return "idle"
        elif cooling:
            return "cooling"
        elif heating:
            return "heating"
        return "unknown"


class ElcoWaterHeaterOpSensor(BaseElcoSensor):
    def __init__(self, coordinator):
        super().__init__(coordinator, "DHW op", "elco_dhw_op")

    @property
    def native_value(self):
        plant = self.coordinator.data.get("data", {}).get("plantData", {})
        dhw_mode = plant.get("dhwMode", {}).get("value")
        heat_pump_on = plant.get("heatPumpOn")

        if dhw_mode == 0:
            return "off"
        elif dhw_mode == 1 and heat_pump_on:
            return "heating"
        return "idle"