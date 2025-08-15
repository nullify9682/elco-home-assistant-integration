"""The Elco integration."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN
from .api import ElcoRemoconAPI

PLATFORMS = ["switch"]

async def async_setup(hass: HomeAssistant, config: dict):
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    email = entry.data["email"]
    password = entry.data["password"]
    gateway_id = entry.data["gateway_id"]

    api = ElcoRemoconAPI(email, password, gateway_id)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = api

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok