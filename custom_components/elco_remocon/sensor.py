"""Capteurs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfPressure, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ElcoConfigEntry
from .const import OP_HEATING, OP_IDLE, OP_OFF, OPERATION_STATES
from .coordinator import (
    ITEM_CH_FLOW_SETPOINT_NAME,
    ITEM_CH_FLOW_TEMP_NAME,
    ITEM_HEATING_CIRCUIT_PRESSURE_NAME,
    ElcoCoordinator,
)
from .entity import ElcoEntity
from .models import PlantState

DHW_OPERATION_STATES = [OP_OFF, OP_IDLE, OP_HEATING]


@dataclass(frozen=True, kw_only=True)
class ElcoSensorDescription(SensorEntityDescription):
    """Description d'un capteur.

    `value_fn` renvoie None quand la donnée n'est pas disponible ; `exists_fn`
    décide si l'entité doit seulement être créée — beaucoup de mesures
    dépendent de la génération de la passerelle et de son équipement.
    """

    value_fn: Callable[[PlantState, ElcoCoordinator], float | str | None]
    exists_fn: Callable[[PlantState], bool] = lambda _state: True


SENSORS: tuple[ElcoSensorDescription, ...] = (
    ElcoSensorDescription(
        key="outside_temp",
        translation_key="outside_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        # `state_class` est indispensable : sans elle, aucune statistique long
        # terme n'est conservée et l'historique disparaît à la purge. Elle ne
        # peut PAS être ajoutée après coup depuis le registre d'entités,
        # contrairement à device_class et unit_of_measurement.
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda state, _c: state.outside_temp,
        exists_fn=lambda state: state.has_outside_probe,
    ),
    ElcoSensorDescription(
        key="dhw_temp",
        translation_key="dhw_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda state, _c: state.dhw_temp,
        exists_fn=lambda state: state.has_dhw_probe,
    ),
    ElcoSensorDescription(
        key="flow_temp",
        translation_key="flow_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda state, _c: state.item_value(ITEM_CH_FLOW_TEMP_NAME),
        exists_fn=lambda state: state.item(ITEM_CH_FLOW_TEMP_NAME) is not None,
    ),
    ElcoSensorDescription(
        key="flow_setpoint",
        translation_key="flow_setpoint",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state, _c: state.item_value(ITEM_CH_FLOW_SETPOINT_NAME),
        exists_fn=lambda state: state.item(ITEM_CH_FLOW_SETPOINT_NAME) is not None,
    ),
    ElcoSensorDescription(
        key="circuit_pressure",
        translation_key="circuit_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.BAR,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state, _c: state.item_value(ITEM_HEATING_CIRCUIT_PRESSURE_NAME),
        exists_fn=lambda state: (
            state.item(ITEM_HEATING_CIRCUIT_PRESSURE_NAME) is not None
        ),
    ),
    ElcoSensorDescription(
        key="operation",
        translation_key="operation",
        device_class=SensorDeviceClass.ENUM,
        options=OPERATION_STATES,
        value_fn=lambda _state, coordinator: coordinator.operation(),
    ),
    ElcoSensorDescription(
        key="dhw_operation",
        translation_key="dhw_operation",
        device_class=SensorDeviceClass.ENUM,
        options=DHW_OPERATION_STATES,
        value_fn=lambda state, _c: _dhw_operation(state),
    ),
)


def _dhw_operation(state: PlantState) -> str:
    if state.dhw_off:
        return OP_OFF
    return OP_HEATING if state.dhw_heating else OP_IDLE


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElcoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entities: list[ElcoSensor] = []
    for coordinator in entry.runtime_data.values():
        state = coordinator.data
        if state is None:
            continue
        entities.extend(
            ElcoSensor(coordinator, description)
            for description in SENSORS
            if description.exists_fn(state)
        )
    async_add_entities(entities)


class ElcoSensor(ElcoEntity, SensorEntity):
    """Un capteur de l'installation."""

    entity_description: ElcoSensorDescription

    def __init__(
        self, coordinator: ElcoCoordinator, description: ElcoSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | None:
        state = self.coordinator.data
        if state is None:
            return None
        return self.entity_description.value_fn(state, self.coordinator)
