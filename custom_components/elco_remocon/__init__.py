"""Elco Remocon integration for Home Assistant."""

from __future__ import annotations

import logging
import socket
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import ElcoApiError, ElcoAuthError, ElcoClient
from .const import (
    CONF_GATEWAY_ID,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_SUMMER_SWITCHOVER,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_SUMMER_SWITCHOVER,
    LEGACY_UNIQUE_IDS,
)
from .coordinator import ElcoCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.WATER_HEATER,
]

type ElcoConfigEntry = ConfigEntry[dict[str, ElcoCoordinator]]


async def async_setup_entry(hass: HomeAssistant, entry: ElcoConfigEntry) -> bool:
    """Opens a session for the account and discovers its installations."""
    # IPv4 only: on a dual-stack LAN whose WAN has no IPv6 egress, aiohttp's
    # default resolver still issues an AAAA query alongside the A one, and if
    # it lands on an IPv6 nameserver that isn't actually reachable, that leg
    # times out and fails the whole getaddrinfo() call — even though the A
    # query would have resolved fine on its own. `family` is the same knob
    # core's `cloudflare` integration uses, for an unrelated reason.
    session = async_create_clientsession(hass, family=socket.AF_INET)
    client = ElcoClient(
        session,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
    )

    try:
        await client.async_login()
        plants = await client.async_get_plants()
    except ElcoAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except ElcoApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    if not plants:
        raise ConfigEntryNotReady("no installation on this account")

    await _async_migrate_unique_ids(hass, entry, plants)

    minutes = entry.options.get(
        CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
    )
    interval = timedelta(minutes=minutes)
    switchover = entry.options.get(CONF_SUMMER_SWITCHOVER, DEFAULT_SUMMER_SWITCHOVER)

    coordinators: dict[str, ElcoCoordinator] = {}
    for plant in plants:
        try:
            features = await client.async_get_features(plant.gateway_id)
        except ElcoApiError as err:
            # An unreachable installation must not deprive the others.
            _LOGGER.warning("installation %s skipped: %s", plant.gateway_id, err)
            continue
        coordinator = ElcoCoordinator(
            hass, client, plant, features, interval, switchover
        )
        await coordinator.async_config_entry_first_refresh()
        coordinators[plant.gateway_id] = coordinator

    if not coordinators:
        raise ConfigEntryNotReady("no installation reachable")

    entry.runtime_data = coordinators
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ElcoConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Moves an entry from version 1 to version 2.

    Version 1 stored a manually entered `gateway_id` and only handled one
    installation. Version 2 discovers the account's installations; the
    `gateway_id` is kept, and used to rewrite the old `unique_id` values.
    """
    if entry.version > 2:
        return False
    if entry.version == 1:
        hass.config_entries.async_update_entry(entry, version=2)
        _LOGGER.debug("entry %s migrated to version 2", entry.entry_id)
    return True


async def _async_migrate_unique_ids(
    hass: HomeAssistant, entry: ConfigEntry, plants
) -> None:
    """Rewrites version 0.1's `unique_id` values to the prefixed format.

    Without this, changing the format would create new entities (`..._2`) and
    leave the old ones orphaned: `entity_id` values referenced in automations
    and dashboards would stop working, and previously recorded history would
    be lost. The rewrite preserves both.
    """
    legacy_gateway = entry.data.get(CONF_GATEWAY_ID)
    if legacy_gateway:
        gateway_id = str(legacy_gateway).upper()
    elif len(plants) == 1:
        gateway_id = plants[0].gateway_id
    else:
        # Multiple installations and no clue which one is the original one:
        # guessing would do more harm than good.
        return

    def _migrate(registry_entry: er.RegistryEntry) -> dict[str, str] | None:
        new_key = LEGACY_UNIQUE_IDS.get(registry_entry.unique_id)
        if new_key is None:
            return None
        return {"new_unique_id": f"{gateway_id}_{new_key}"}

    await er.async_migrate_entries(hass, entry.entry_id, _migrate)


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
