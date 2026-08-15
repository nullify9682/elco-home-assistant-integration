"""Cycle de rafraîchissement et logique d'état."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ElcoApiError, ElcoAuthError, ElcoClient, build_state
from .const import (
    DHW_HEATING_SLOPE_THRESHOLD,
    DHW_SLOPE_WINDOW,
    DOMAIN,
    FREECOOLING_MAX_FLOW_TEMP,
    OP_COOLING,
    OP_DHW,
    OP_FREECOOLING,
    OP_HEATING,
    OP_IDLE,
    OP_OFF,
    OPTIMISTIC_REFRESH_DELAYS,
    OPTIMISTIC_TTL,
    POLLED_PLANT_ITEMS,
    POLLED_ZONE_ITEMS,
)
from .models import Bounded, Features, Plant, PlantState

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _Optimistic:
    """Une valeur affichée avant confirmation du serveur."""

    value: float | int
    expires: float


class ElcoCoordinator(DataUpdateCoordinator[PlantState]):
    """Rafraîchit une installation.

    Deux responsabilités que l'API n'assume pas : afficher immédiatement une
    consigne qu'on vient d'écrire (§ affichage optimiste), et déduire si l'ECS
    chauffe réellement (aucun champ ne le dit — PROTOCOL.md §4).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: ElcoClient,
        plant: Plant,
        features: Features,
        scan_interval: timedelta,
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
        self._optimistic: dict[str, _Optimistic] = {}
        self._dhw_history: deque[tuple[float, float]] = deque(maxlen=DHW_SLOPE_WINDOW)

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
        except ElcoAuthError as err:
            # Déclenche le flux de ré-authentification de Home Assistant plutôt
            # que de réessayer indéfiniment avec des identifiants périmés.
            raise ConfigEntryAuthFailed(str(err)) from err
        except ElcoApiError as err:
            raise UpdateFailed(str(err)) from err

        self._infer_dhw_heating(state)
        self._apply_optimistic(state)
        return state

    @property
    def _first_zone(self) -> int:
        return self.features.zones[0].number if self.features.zones else 1

    # -- inférence ECS ------------------------------------------------------

    def _infer_dhw_heating(self, state: PlantState) -> None:
        """Déduit si le ballon est en chauffe, faute d'un champ le disant.

        `hpOn and dhwMode == 1` est FAUX : `hpOn` vaut vrai dès que la pompe
        tourne pour n'importe quelle raison, et `dhwMode == 1` signifie
        seulement « ECS autorisée ». Sur l'installation de référence, ce test
        annonçait une chauffe pendant que le ballon perdait 0.9 °C/h.

        On se rabat sur la pente réelle de la température du ballon.
        """
        temp = state.dhw_temp
        if temp is None or state.dhw_off:
            state.dhw_heating = False
            return

        now = dt_util.utcnow().timestamp()
        self._dhw_history.append((now, temp))
        if len(self._dhw_history) < 2:
            state.dhw_heating = False
            return

        (t0, v0), (t1, v1) = self._dhw_history[0], self._dhw_history[-1]
        hours = (t1 - t0) / 3600
        if hours <= 0:
            state.dhw_heating = False
            return

        slope = (v1 - v0) / hours
        state.dhw_heating = state.heat_pump_on and slope >= DHW_HEATING_SLOPE_THRESHOLD

    # -- état de fonctionnement ---------------------------------------------

    def operation(self, state: PlantState | None = None) -> str:
        """État de fonctionnement de synthèse.

        L'ordre compte : l'ECS est prioritaire sur la machine, donc si le
        ballon monte c'est bien ce qu'elle fait.
        """
        state = state or self.data
        if state is None:
            return OP_IDLE

        zones = list(state.zones.values())
        if not state.heat_pump_on and not any(z.heat_or_cool_request for z in zones):
            return OP_OFF if state.dhw_off else OP_IDLE

        if state.dhw_heating:
            return OP_DHW

        cooling = any(z.cooling_on for z in zones)
        heating = any(z.heating_on for z in zones)

        if cooling:
            # Freecooling : le circulateur tourne, le compresseur non, et le
            # départ reste frais. C'est ce que `hpOn` seul ne permet pas de
            # distinguer d'un refroidissement actif.
            flow = state.item_value(ITEM_CH_FLOW_TEMP_NAME)
            pump = state.item_value(ITEM_IS_HEATING_PUMP_ON_NAME)
            if (
                not state.heat_pump_on
                and pump
                and flow is not None
                and flow <= FREECOOLING_MAX_FLOW_TEMP
            ):
                return OP_FREECOOLING
            return OP_COOLING
        if heating:
            return OP_HEATING
        return OP_IDLE

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


# Noms tels que le serveur les renvoie dans `dataItems` (pas les numéros).
ITEM_CH_FLOW_TEMP_NAME = "ChFlowTemp"
ITEM_IS_HEATING_PUMP_ON_NAME = "IsHeatingPumpOn"
ITEM_CH_FLOW_SETPOINT_NAME = "ChFlowSetpointTemp"
ITEM_HEATING_CIRCUIT_PRESSURE_NAME = "HeatingCircuitPressure"


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
