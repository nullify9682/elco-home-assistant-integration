"""Constants for the Elco Remocon integration.

The protocol is documented in PROTOCOL.md at the root of the repository.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "elco_remocon"

CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"
CONF_GATEWAY_ID: Final = "gateway_id"
CONF_SCAN_INTERVAL_MINUTES: Final = "scan_interval_minutes"
CONF_SUMMER_SWITCHOVER: Final = "summer_switchover"

# 15 min since v1.4.0 (was 10). The gateway itself only refreshes its cloud
# picture every few minutes, so a shorter cycle mostly buys duplicate
# readings; writes don't wait for it either, they re-read on their own
# schedule (`OPTIMISTIC_REFRESH_DELAYS`).
DEFAULT_SCAN_INTERVAL_MINUTES: Final = 15
MIN_SCAN_INTERVAL_MINUTES: Final = 2
MAX_SCAN_INTERVAL_MINUTES: Final = 60

# Summer/winter switchover point. Above it, the machine cannot be heating the
# house: any heat it produces is for hot water instead. Configurable, because
# a threshold that fits French-speaking Switzerland doesn't fit everywhere
# else.
DEFAULT_SUMMER_SWITCHOVER: Final = 21.0
MIN_SUMMER_SWITCHOVER: Final = 5.0
MAX_SUMMER_SWITCHOVER: Final = 35.0

# Identity announced to the server. Mirrors the official app's: the server
# refuses clients it doesn't recognize.
APP_ID: Final = "com.remotethermo.remoconnet"
APP_VERSION: Final = "7.0.1.40358"
APP_OS: Final = 2

# Host per brand (AppSettings.serverUrl in the app).
BRAND_HOSTS: Final = {
    "remocon": "www.remocon-net.remotethermo.com",
    "ariston": "www.ariston-net.remotethermo.com",
    "chaffolink": "www.chaffolink.remotethermo.com",
    "atagzone": "www.atagzone.remotethermo.com",
    "nti": "www.nti.remotethermo.com",
    "htp": "www.htp-net.remotethermo.com",
    "racold": "www.racold-net.remotethermo.com",
    "calorex": "www.calorex-net.remotethermo.com",
}
DEFAULT_BRAND: Final = "remocon"

AUTH_HEADER: Final = "ar.authToken"

# --- dataItem identifiers ---------------------------------------------------
# Numbering from `eRemoteDataItemDto`. WARNING: the app defines a SECOND
# enumeration, `eMenuItemDto`, with the same names but different numbers.
# Using that one gets an HTTP 500 with no clue why. See PROTOCOL.md §7.
ITEM_DHW_TEMP: Final = 3
ITEM_OUTSIDE_TEMP: Final = 4
ITEM_DHW_STORAGE_TEMP: Final = 5
ITEM_ZONE_MEASURED_TEMP: Final = 10
ITEM_ZONE_DESIRED_TEMP: Final = 12
ITEM_IS_HEATING_PUMP_ON: Final = 22
ITEM_HEATING_CIRCUIT_PRESSURE: Final = 23
ITEM_CH_FLOW_TEMP: Final = 38
ITEM_CH_FLOW_SETPOINT_TEMP: Final = 65
ITEM_HEATING_FLOW_TEMP: Final = 68
ITEM_COOLING_FLOW_TEMP: Final = 69

# Requested on every cycle. The server silently drops whatever the gateway
# doesn't support: we ask broadly and only expose what comes back.
POLLED_PLANT_ITEMS: Final = (
    ITEM_CH_FLOW_TEMP,
    ITEM_CH_FLOW_SETPOINT_TEMP,
    ITEM_HEATING_FLOW_TEMP,
    ITEM_COOLING_FLOW_TEMP,
    ITEM_IS_HEATING_PUMP_ON,
    ITEM_HEATING_CIRCUIT_PRESSURE,
    ITEM_DHW_STORAGE_TEMP,
)
POLLED_ZONE_ITEMS: Final = (
    ITEM_ZONE_MEASURED_TEMP,
    ITEM_ZONE_DESIRED_TEMP,
)

# --- optimistic display ------------------------------------------------------
# After a write, the entity shows the requested value, then we re-read at
# these instants. Past the last one, the server's value takes over no matter
# what: we never lie for longer than that.
OPTIMISTIC_REFRESH_DELAYS: Final = (5, 30, 120)
OPTIMISTIC_TTL: Final = 150

# --- modes -------------------------------------------------------------------
DHW_MODE_OFF: Final = 0
DHW_MODE_ON: Final = 1

# Bug corrige le 2026-08-20 : COMFORT et REDUCED etaient inverses. Confirme
# par David en lisant zoneData.mode.allowedOptionTexts sur l'API web R2 --
# ["Mode protection", "Automatique", "Reduit", "Confort"] pour les valeurs
# [0, 1, 2, 3]. Le preset "eco" (REDUCED) commandait donc en realite le
# confort, et "comfort" (COMFORT) commandait le mode reduit.
ZONE_MODE_OFF: Final = 0
ZONE_MODE_AUTO: Final = 1
ZONE_MODE_REDUCED: Final = 2
ZONE_MODE_COMFORT: Final = 3

# --- operating states ---------------------------------------------------------
# Home Assistant's `HVACAction` vocabulary, nothing invented. NEVER return
# "unknown": the string collides with HA's own native state.
OP_OFF: Final = "off"
OP_IDLE: Final = "idle"
OP_HEATING: Final = "heating"
OP_COOLING: Final = "cooling"

# The heat pump sensor describes the CIRCUIT, and nothing else. It reads
# `idle` while the tank is being charged: the DHW sensor carries that
# information instead.
OPERATION_STATES: Final = [OP_OFF, OP_IDLE, OP_HEATING, OP_COOLING]
DHW_OPERATION_STATES: Final = [OP_OFF, OP_IDLE, OP_HEATING]

# --- migration ----------------------------------------------------------------
# Version 0.1's unique_id values weren't prefixed by the gateway, which made
# multi-installation impossible. `async_migrate_entry` rewrites them to
# preserve entity_id values and ALL previously recorded history.
LEGACY_UNIQUE_IDS: Final = {
    "elco_heat_pump": "climate_zone_1",
    "elco_dhw": "water_heater",
    "elco_outside_temp": "outside_temp",
    "elco_boiler_temp": "dhw_temp",
    "elco_hvac_op": "operation",
    "elco_dhw_op": "dhw_operation",
}
