"""Diagnostic binary sensors.

They expose the machine's RAW signals, disabled by default. Their reason for
existing: the synthesized state (`sensor.*_operation`) relies on assumptions
— heating / cooling / DHW exclusivity, the tank's slope threshold, the flow
threshold for freecooling. These sensors let you verify those assumptions
against real data instead of just trusting them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ElcoConfigEntry
from .coordinator import ITEM_IS_HEATING_PUMP_ON_NAME, ElcoCoordinator
from .entity import ElcoEntity, ElcoZoneEntity
from .models import PlantState, ZoneState


@dataclass(frozen=True, kw_only=True)
class ElcoBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[PlantState], bool | None]
    exists_fn: Callable[[PlantState], bool] = lambda _state: True


@dataclass(frozen=True, kw_only=True)
class ElcoZoneBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[ZoneState], bool | None]


PLANT_SENSORS: tuple[ElcoBinaryDescription, ...] = (
    ElcoBinaryDescription(
        key="heat_pump_on",
        translation_key="heat_pump_on",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.heat_pump_on,
    ),
    ElcoBinaryDescription(
        key="circulator_on",
        translation_key="circulator_on",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: bool(state.item_value(ITEM_IS_HEATING_PUMP_ON_NAME)),
        exists_fn=lambda state: state.item(ITEM_IS_HEATING_PUMP_ON_NAME) is not None,
    ),
    ElcoBinaryDescription(
        key="dhw_heating",
        translation_key="dhw_heating",
        device_class=BinarySensorDeviceClass.HEAT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.dhw_heating,
    ),
    ElcoBinaryDescription(
        key="flame",
        translation_key="flame",
        device_class=BinarySensorDeviceClass.HEAT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.flame,
    ),
)

ZONE_SENSORS: tuple[ElcoZoneBinaryDescription, ...] = (
    ElcoZoneBinaryDescription(
        key="heating_active",
        translation_key="heating_active",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda zone: zone.heating_on,
    ),
    ElcoZoneBinaryDescription(
        key="cooling_active",
        translation_key="cooling_active",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda zone: zone.cooling_on,
    ),
    ElcoZoneBinaryDescription(
        key="heat_or_cool_request",
        translation_key="heat_or_cool_request",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda zone: zone.heat_or_cool_request,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElcoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entities: list[BinarySensorEntity] = []
    for coordinator in entry.runtime_data.values():
        state = coordinator.data
        if state is None:
            continue
        entities.extend(
            ElcoBinarySensor(coordinator, description)
            for description in PLANT_SENSORS
            if description.exists_fn(state)
        )
        for zone in coordinator.features.zones:
            entities.extend(
                ElcoZoneBinarySensor(coordinator, zone.number, description)
                for description in ZONE_SENSORS
            )
    async_add_entities(entities)


class ElcoBinarySensor(ElcoEntity, BinarySensorEntity):
    entity_description: ElcoBinaryDescription

    def __init__(
        self, coordinator: ElcoCoordinator, description: ElcoBinaryDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        state = self.coordinator.data
        return self.entity_description.value_fn(state) if state else None


class ElcoZoneBinarySensor(ElcoZoneEntity, BinarySensorEntity):
    entity_description: ElcoZoneBinaryDescription

    def __init__(
        self,
        coordinator: ElcoCoordinator,
        zone: int,
        description: ElcoZoneBinaryDescription,
    ) -> None:
        super().__init__(coordinator, zone, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        zone = self.zone_state
        return self.entity_description.value_fn(zone) if zone else None
