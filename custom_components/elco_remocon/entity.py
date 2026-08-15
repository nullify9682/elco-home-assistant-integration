"""Socle commun des entités."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ElcoCoordinator


class ElcoEntity(CoordinatorEntity[ElcoCoordinator]):
    """Entité rattachée à une installation.

    `_attr_has_entity_name = True` : le nom affiché est composé par Home
    Assistant à partir du nom de l'APPAREIL et du nom de l'entité. Les entités
    ne doivent donc jamais répéter « Elco » ni le nom de la pompe — c'est ce
    qui produisait des libellés incohérents comme « Elco Ext Temp » à côté de
    « Boiler Temp ».
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: ElcoCoordinator, key: str) -> None:
        super().__init__(coordinator)
        plant = coordinator.plant
        self._key = key
        # Préfixé par la passerelle : c'est ce qui rend plusieurs installations
        # possibles. La migration depuis la version 0.1 réécrit les anciens
        # identifiants non préfixés (voir __init__.async_migrate_entry).
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
    """Entité rattachée à une zone d'une installation."""

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
    """Libellé de modèle, à défaut d'un champ le donnant.

    L'API ne renvoie qu'un `gwSysType` numérique ; on l'affiche tel quel plutôt
    que d'inventer un nom commercial qui pourrait être faux.
    """
    if plant.system_type is None:
        return "Passerelle BSB"
    return f"Passerelle BSB (type {plant.system_type})"
