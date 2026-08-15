"""Constantes de l'intégration Elco Remocon.

Le protocole est documenté dans PROTOCOL.md à la racine du dépôt.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "elco_remocon"

CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"
CONF_GATEWAY_ID: Final = "gateway_id"
CONF_SCAN_INTERVAL_MINUTES: Final = "scan_interval_minutes"
CONF_SUMMER_SWITCHOVER: Final = "summer_switchover"

DEFAULT_SCAN_INTERVAL_MINUTES: Final = 10
MIN_SCAN_INTERVAL_MINUTES: Final = 2
MAX_SCAN_INTERVAL_MINUTES: Final = 60

# Point de bascule été/hiver. Au-dessus, la machine ne peut pas être en train
# de chauffer la maison : si elle produit de la chaleur, c'est pour l'eau
# chaude. Réglable, parce qu'un seuil juste en Suisse romande ne l'est pas
# ailleurs.
DEFAULT_SUMMER_SWITCHOVER: Final = 21.0
MIN_SUMMER_SWITCHOVER: Final = 5.0
MAX_SUMMER_SWITCHOVER: Final = 35.0

# Identité annoncée au serveur. Reprend celle de l'application officielle :
# le serveur refuse les clients qu'il ne reconnaît pas.
APP_ID: Final = "com.remotethermo.remoconnet"
APP_VERSION: Final = "7.0.1.40358"
APP_OS: Final = 2

# Hôte par marque (AppSettings.serverUrl dans l'application).
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

# --- identifiants de dataItems ---------------------------------------------
# Numérotation de `eRemoteDataItemDto`. ATTENTION : l'application définit une
# SECONDE énumération, `eMenuItemDto`, avec les mêmes noms et des numéros
# différents. Employer celle-ci fait répondre HTTP 500 sans aucun indice.
# Voir PROTOCOL.md §7.
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

# Demandés à chaque cycle. Le serveur retire silencieusement ce que la
# passerelle ne gère pas : on demande large et on n'expose que ce qui revient.
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

# --- affichage optimiste ----------------------------------------------------
# Après une écriture, l'entité affiche la valeur demandée, puis on relit à ces
# instants. Passé le dernier, la valeur du serveur reprend la main quoi qu'il
# arrive : on ne ment jamais plus longtemps que ça.
OPTIMISTIC_REFRESH_DELAYS: Final = (5, 30, 120)
OPTIMISTIC_TTL: Final = 150

# --- modes ------------------------------------------------------------------
DHW_MODE_OFF: Final = 0
DHW_MODE_ON: Final = 1

ZONE_MODE_OFF: Final = 0
ZONE_MODE_AUTO: Final = 1
ZONE_MODE_COMFORT: Final = 2
ZONE_MODE_REDUCED: Final = 3

# --- états de fonctionnement ------------------------------------------------
# Vocabulaire `HVACAction` de Home Assistant, sans invention. Ne JAMAIS
# renvoyer "unknown" : la chaîne entre en collision avec l'état natif de HA.
OP_OFF: Final = "off"
OP_IDLE: Final = "idle"
OP_HEATING: Final = "heating"
OP_COOLING: Final = "cooling"

# Le capteur de la pompe décrit le CIRCUIT, et rien d'autre. Pendant une charge
# du ballon il vaut `idle` : c'est le capteur ECS qui porte l'information.
OPERATION_STATES: Final = [OP_OFF, OP_IDLE, OP_HEATING, OP_COOLING]
DHW_OPERATION_STATES: Final = [OP_OFF, OP_IDLE, OP_HEATING]

# --- migration --------------------------------------------------------------
# Les unique_id de la version 0.1 n'étaient pas préfixés par la passerelle, ce
# qui rendait le multi-installation impossible. `async_migrate_entry` les
# réécrit pour préserver les entity_id et TOUT l'historique déjà enregistré.
LEGACY_UNIQUE_IDS: Final = {
    "elco_heat_pump": "climate_zone_1",
    "elco_dhw": "water_heater",
    "elco_outside_temp": "outside_temp",
    "elco_boiler_temp": "dhw_temp",
    "elco_hvac_op": "operation",
    "elco_dhw_op": "dhw_operation",
}
