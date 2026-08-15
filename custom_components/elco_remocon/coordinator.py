"""Cycle de rafraîchissement et logique d'état."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ElcoApiError, ElcoAuthError, ElcoClient, build_state
from .const import (
    DOMAIN,
    OP_COOLING,
    OP_HEATING,
    OP_IDLE,
    OP_OFF,
    OPTIMISTIC_REFRESH_DELAYS,
    OPTIMISTIC_TTL,
    POLLED_PLANT_ITEMS,
    POLLED_ZONE_ITEMS,
    ZONE_MODE_OFF,
)
from .models import Bounded, Features, Plant, PlantState, ZoneState

_LOGGER = logging.getLogger(__name__)

# Noms tels que le serveur les renvoie dans `dataItems` — la requête se fait
# par numéro, la réponse est indexée par nom.
ITEM_CH_FLOW_TEMP_NAME = "ChFlowTemp"
ITEM_IS_HEATING_PUMP_ON_NAME = "IsHeatingPumpOn"
ITEM_CH_FLOW_SETPOINT_NAME = "ChFlowSetpointTemp"
ITEM_HEATING_CIRCUIT_PRESSURE_NAME = "HeatingCircuitPressure"


@dataclass(slots=True)
class _Optimistic:
    """Une valeur affichée avant confirmation du serveur."""

    value: float
    expires: float


class ElcoCoordinator(DataUpdateCoordinator[PlantState]):
    """Rafraîchit une installation et décide de ce qu'elle est en train de faire.

    L'API ne dit nulle part si la machine chauffe la maison ou le ballon. Elle
    ne fournit que des signaux qu'il faut savoir lire — et dont deux se
    laissent facilement mal interpréter :

    - `hpOn` est au niveau de l'INSTALLATION : vrai dès que la pompe tourne,
      pour le chauffage, le refroidissement ou l'eau chaude.
    - `heatingOn` / `coolingOn` décrivent le mode HYDRAULIQUE de la machine,
      pas l'activité de la zone. Mesuré le 2026-08-15 : `heatingOn` était vrai
      par 30 °C dehors, parce que la machine avait quitté le refroidissement
      pour pouvoir charger le ballon.

    Le seul signal qui dit ce que veut le circuit est `heatOrCoolReq`.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: ElcoClient,
        plant: Plant,
        features: Features,
        scan_interval: timedelta,
        summer_switchover: float,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {plant.name}",
            update_interval=scan_interval,
        )
        self.client = client
        self.plant = plant
        self.features = features
        self.summer_switchover = summer_switchover
        self._optimistic: dict[str, _Optimistic] = {}

    # -- cycle --------------------------------------------------------------

    async def _async_update_data(self) -> PlantState:
        gw = self.plant.gateway_id
        try:
            raw = await self.client.async_get_plant_data(gw, zone=self._first_zone)
            state = build_state(gw, raw)

            items = [(item, 0) for item in POLLED_PLANT_ITEMS]
            for zone in self.features.zones:
                items += [(item, zone.number) for item in POLLED_ZONE_ITEMS]
            state.items = await self.client.async_get_data_items(
                gw, self.features.raw, items
            )
            # Le serveur retire silencieusement les mesures que la passerelle
            # ne gère pas, et une valeur reçue peut malgré tout être écartée
            # par ses propres bornes. Tracer les trois listes est le seul
            # moyen de comprendre pourquoi une entité n'apparaît pas.
            _LOGGER.debug(
                "dataItems demandés=%s reçus=%s exploitables=%s",
                [item_id for item_id, _ in items],
                sorted(state.items),
                sorted(name for name, item in state.items.items() if item.available),
            )
        except ElcoAuthError as err:
            # Déclenche le flux de ré-authentification de Home Assistant plutôt
            # que de réessayer indéfiniment avec des identifiants périmés.
            raise ConfigEntryAuthFailed(str(err)) from err
        except ElcoApiError as err:
            raise UpdateFailed(str(err)) from err

        state.dhw_heating = self._is_dhw_producing(state)
        self._apply_optimistic(state)
        return state

    @property
    def _first_zone(self) -> int:
        return self.features.zones[0].number if self.features.zones else 1

    # -- interprétation -----------------------------------------------------

    @staticmethod
    def _is_dhw_producing(state: PlantState) -> bool:
        """La machine tourne-t-elle pour le ballon ?

        La machine tourne (`hpOn`) alors qu'AUCUNE zone ne demande quoi que ce
        soit (`heatOrCoolReq`) : elle ne peut travailler que pour l'eau chaude.
        Aucun seuil, aucune saison, aucune inertie — c'est vrai dès la première
        lecture du cycle.

        Une inférence par la pente de la température du ballon a été essayée et
        abandonnée : elle mettait 30 minutes à réagir, parce que le ballon
        commence par se REFROIDIR quand la charge démarre.
        """
        if state.dhw_off or not state.heat_pump_on:
            return False
        return not any(zone.heat_or_cool_request for zone in state.zones.values())

    def _is_heating_season(self, state: PlantState) -> bool:
        """Au-dessus du point de bascule, la maison ne peut pas être chauffée.

        Garde-fou contre le mode hydraulique : la machine passe en chauffage
        pour produire de l'eau chaude et y reste ensuite, ce qui ferait
        afficher « chauffage » en plein mois d'août.
        """
        if state.outside_temp is None:
            return True
        return state.outside_temp <= self.summer_switchover

    def zone_action(self, zone: ZoneState, state: PlantState | None = None) -> str:
        """Ce que fait réellement une zone, en vocabulaire `HVACAction`."""
        state = state or self.data
        if state is None or zone.mode == ZONE_MODE_OFF:
            return OP_OFF
        if not zone.heat_or_cool_request:
            return OP_IDLE
        if zone.cooling_on:
            return OP_COOLING
        if zone.heating_on and self._is_heating_season(state):
            return OP_HEATING
        return OP_IDLE

    def zone_hvac_mode(self, zone: ZoneState, state: PlantState | None = None) -> str:
        """Le régime affiché par le thermostat.

        `auto` quand la machine est en marche sans qu'on puisse affirmer le
        régime — c'est plus honnête qu'annoncer « chauffage » par 30 °C dehors.
        """
        state = state or self.data
        if state is None or zone.mode == ZONE_MODE_OFF:
            return "off"
        if zone.cooling_on:
            return "cool"
        if zone.heating_on and self._is_heating_season(state):
            return "heat"
        return "auto"

    def operation(self, state: PlantState | None = None) -> str:
        """État du CIRCUIT, et de lui seul.

        Vaut `idle` pendant une charge du ballon : c'est `dhw_operation` qui
        porte cette information. Les deux capteurs décrivent deux axes
        indépendants, pas deux vues du même état.
        """
        state = state or self.data
        if state is None:
            return OP_IDLE
        zones = list(state.zones.values())
        if not zones:
            return OP_IDLE
        if all(zone.mode == ZONE_MODE_OFF for zone in zones):
            return OP_OFF
        for zone in zones:
            action = self.zone_action(zone, state)
            if action in (OP_HEATING, OP_COOLING):
                return action
        return OP_IDLE

    def dhw_operation(self, state: PlantState | None = None) -> str:
        """État du BALLON, en vocabulaire `HVACAction`."""
        state = state or self.data
        if state is None:
            return OP_IDLE
        if state.dhw_off:
            return OP_OFF
        return OP_HEATING if state.dhw_heating else OP_IDLE

    # -- affichage optimiste ------------------------------------------------

    @callback
    def async_set_optimistic(self, key: str, value: float) -> None:
        """Affiche immédiatement une valeur qu'on vient d'écrire, puis relit.

        La fenêtre est bornée : passé `OPTIMISTIC_TTL`, la valeur du serveur
        reprend la main quoi qu'il arrive. On ne ment jamais plus longtemps
        que ça, même si l'écriture a été refusée par la machine.
        """
        self._optimistic[key] = _Optimistic(
            value=value,
            expires=dt_util.utcnow().timestamp() + OPTIMISTIC_TTL,
        )
        if self.data is not None:
            self._apply_optimistic(self.data)
            self.async_update_listeners()

        for delay in OPTIMISTIC_REFRESH_DELAYS:
            async_call_later(self.hass, delay, self._async_scheduled_refresh)

    async def _async_scheduled_refresh(self, _now) -> None:
        await self.async_request_refresh()

    def _apply_optimistic(self, state: PlantState) -> None:
        """Recouvre l'état serveur par les valeurs en attente non expirées."""
        now = dt_util.utcnow().timestamp()
        for key, entry in list(self._optimistic.items()):
            if entry.expires <= now:
                self._optimistic.pop(key, None)
                continue
            _overlay(state, key, entry.value)


def _overlay(state: PlantState, key: str, value: float) -> None:
    """Applique une valeur optimiste sur l'état."""
    if key == "dhw_comfort":
        state.dhw_comfort = _with_value(state.dhw_comfort, value)
    elif key == "dhw_mode":
        state.dhw_mode = int(value)
    elif key.startswith("zone_"):
        parts = key.split("_")
        try:
            number = int(parts[1])
        except (IndexError, ValueError):
            return
        zone = state.zones.get(number)
        if zone is None:
            return
        what = "_".join(parts[2:])
        if what == "comfort":
            if zone.cooling_on:
                zone.cool_comfort = _with_value(zone.cool_comfort, value)
            else:
                zone.ch_comfort = _with_value(zone.ch_comfort, value)
        elif what == "mode":
            zone.mode = int(value)


def _with_value(bounded: Bounded, value: float) -> Bounded:
    return Bounded(
        value=float(value), min=bounded.min, max=bounded.max, step=bounded.step
    )
