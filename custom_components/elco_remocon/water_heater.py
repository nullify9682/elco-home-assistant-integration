"""Entité water_heater — le ballon d'eau chaude sanitaire."""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    STATE_ECO,
    STATE_HEAT_PUMP,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ElcoConfigEntry
from .const import DHW_MODE_OFF, DHW_MODE_ON
from .coordinator import ElcoCoordinator
from .entity import ElcoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElcoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        ElcoWaterHeater(coordinator) for coordinator in entry.runtime_data.values()
    )


class ElcoWaterHeater(ElcoEntity, WaterHeaterEntity):
    """Ballon ECS."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_operation_list = [STATE_OFF, STATE_ECO, STATE_HEAT_PUMP]
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
        | WaterHeaterEntityFeature.ON_OFF
    )
    _attr_translation_key = "dhw"

    def __init__(self, coordinator: ElcoCoordinator) -> None:
        super().__init__(coordinator, "water_heater")

    @property
    def current_operation(self) -> str | None:
        """État réel du ballon, en vocabulaire `water_heater`.

        `heat_pump` seulement quand la machine travaille effectivement pour le
        ballon : elle tourne alors qu'aucune zone ne demande rien. `eco`
        signifie « autorisée, mais au repos ».

        La version 0.1 concluait « en chauffe » dès que
        `dhwMode == 1 and heatPumpOn`, ce qui est vrai en permanence dès que la
        machine tourne pour le chauffage ou le refroidissement : le capteur
        annonçait une chauffe pendant que le ballon perdait 0.9 °C/h.
        """
        data = self.coordinator.data
        if data is None:
            return None
        if data.dhw_off:
            return STATE_OFF
        return STATE_HEAT_PUMP if data.dhw_heating else STATE_ECO

    @property
    def current_temperature(self) -> float | None:
        data = self.coordinator.data
        return data.dhw_temp if data else None

    @property
    def target_temperature(self) -> float | None:
        data = self.coordinator.data
        return data.dhw_comfort.value if data else None

    @property
    def min_temp(self) -> float:
        data = self.coordinator.data
        value = data.dhw_comfort.min if data else None
        return value if value is not None else 30.0

    @property
    def max_temp(self) -> float:
        data = self.coordinator.data
        value = data.dhw_comfort.max if data else None
        return value if value is not None else 65.0

    @property
    def target_temperature_step(self) -> float:
        data = self.coordinator.data
        value = data.dhw_comfort.step if data else None
        return value if value is not None else 1.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if data is None:
            return {}
        return {
            "reduced_setpoint": data.dhw_reduced.value,
            "dhw_enabled": data.dhw_enabled,
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        data = self.coordinator.data
        if temperature is None or data is None:
            return
        await self.coordinator.client.async_set_dhw_temperature(
            self.coordinator.plant.gateway_id,
            comfort=temperature,
            reduced=data.dhw_reduced.value,
            old_comfort=data.dhw_comfort.value,
            old_reduced=data.dhw_reduced.value,
        )
        self.coordinator.async_set_optimistic("dhw_comfort", temperature)

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        data = self.coordinator.data
        if data is None:
            return
        mode = DHW_MODE_OFF if operation_mode == STATE_OFF else DHW_MODE_ON
        await self.coordinator.client.async_set_dhw_mode(
            self.coordinator.plant.gateway_id, mode=mode, old_mode=data.dhw_mode
        )
        self.coordinator.async_set_optimistic("dhw_mode", mode)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_set_operation_mode(STATE_HEAT_PUMP)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_set_operation_mode(STATE_OFF)
