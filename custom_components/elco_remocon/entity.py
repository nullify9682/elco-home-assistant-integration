"""Common base for entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ElcoCoordinator


class ElcoEntity(CoordinatorEntity[ElcoCoordinator]):
    """Entity attached to an installation.

    `_attr_has_entity_name = True`: the displayed name is composed by Home
    Assistant from the DEVICE name and the entity name. Entities must
    therefore never repeat "Elco" or the heat pump's name — that's what
    produced inconsistent labels like "Elco Ext Temp" next to "Boiler Temp".
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: ElcoCoordinator, key: str) -> None:
        super().__init__(coordinator)
        plant = coordinator.plant
        self._key = key
        # Prefixed by the gateway: this is what makes multiple installations
        # possible. The migration from version 0.1 rewrites the old
        # unprefixed identifiers (see __init__.async_migrate_entry).
        self._attr_unique_id = f"{plant.gateway_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, plant.gateway_id)},
            name=plant.name,
            manufacturer="Elco",
            model=_model(plant),
            serial_number=plant.serial,
            sw_version=plant.firmware,
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None


class ElcoZoneEntity(ElcoEntity):
    """Entity attached to a zone of an installation."""

    def __init__(self, coordinator: ElcoCoordinator, zone: int, key: str) -> None:
        super().__init__(coordinator, f"{key}_zone_{zone}")
        self._zone = zone

    @property
    def zone_state(self):
        data = self.coordinator.data
        return data.zones.get(self._zone) if data else None

    @property
    def available(self) -> bool:
        return super().available and self.zone_state is not None


def _model(plant) -> str:
    """Model label, absent a field that provides one.

    The API only returns a numeric `gwSysType`; we display it as-is rather
    than inventing a commercial name that could be wrong.
    """
    if plant.system_type is None:
        return "BSB gateway"
    return f"BSB gateway (type {plant.system_type})"
