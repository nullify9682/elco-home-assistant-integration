from datetime import timedelta
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import (
    Platform,
)

from .const import DOMAIN
from .api import ElcoRemoconAPI

import logging

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = [
    Platform.CLIMATE,
    Platform.WATER_HEATER,
    Platform.SENSOR,
]

async def async_setup(hass: HomeAssistant, config: dict):
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    email = entry.data["email"]
    password = entry.data["password"]
    gateway_id = entry.data["gateway_id"]

    api = ElcoRemoconAPI(email, password, gateway_id)

    async def async_update_data():
        try:
            return await hass.async_add_executor_job(api.get_hvac_data)
        except Exception as err:
            raise UpdateFailed(f"Error fetching data: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="elco_remocon",
        update_method=async_update_data,
        update_interval=timedelta(minutes=10),
    )

    # Do first refresh
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok