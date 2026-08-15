"""Client de l'API mobile Remocon-net / Remote Thermo.

Le protocole est documenté dans PROTOCOL.md. Ce module est le seul endroit qui
connaît le transport : le reste de l'intégration ne voit que `models.py`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
from yarl import URL

from .const import (
    APP_ID,
    APP_OS,
    APP_VERSION,
    AUTH_HEADER,
    BRAND_HOSTS,
    DEFAULT_BRAND,
)
from .models import DataItem, Features, Plant, PlantState

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Le serveur filtre sur le User-Agent : il répond 403 à tout ce qui ressemble à
# Python, aiohttp ou Home Assistant, et 200 à celui du client Ktor de
# l'application officielle. Il faut donc l'annoncer explicitement, sinon la
# session par défaut de Home Assistant se fait refuser dès le login.
_HEADERS = {
    "User-Agent": "ktor-client",
    "Accept": "application/json",
}


class ElcoApiError(Exception):
    """Échec de communication avec l'API."""


class ElcoAuthError(ElcoApiError):
    """Identifiants refusés, ou jeton devenu invalide."""


class ElcoClient:
    """Client asynchrone, un jeton partagé pour tout le compte.

    Une seule session dessert toutes les installations : c'est ce qui permet de
    n'ouvrir qu'une authentification quel que soit le nombre de pompes.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        brand: str = DEFAULT_BRAND,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._base = URL(
            f"https://{BRAND_HOSTS.get(brand, BRAND_HOSTS[DEFAULT_BRAND])}/api"
        )
        self._token: str | None = None
        self._login_lock = asyncio.Lock()

    # -- authentification ---------------------------------------------------

    async def async_login(self) -> None:
        """Obtient un jeton. Sérialisé : plusieurs appels concurrents n'en
        déclenchent qu'un seul."""
        async with self._login_lock:
            if self._token is not None:
                return
            payload = {
                "usr": self._email,
                "pwd": self._password,
                "imp": False,
                "notTrack": True,
                "appInfo": {
                    "os": APP_OS,
                    "appVer": APP_VERSION,
                    "appId": APP_ID,
                },
            }
            try:
                async with self._session.post(
                    self._base / "v2/accounts/login",
                    json=payload,
                    headers=_HEADERS,
                    timeout=_TIMEOUT,
                ) as resp:
                    if resp.status in (400, 401, 403):
                        raise ElcoAuthError(
                            f"identifiants refusés (HTTP {resp.status})"
                        )
                    if resp.status >= 400:
                        raise ElcoApiError(f"login : HTTP {resp.status}")
                    body = await resp.json(content_type=None)
            except aiohttp.ClientError as err:
                raise ElcoApiError(f"login injoignable : {err}") from err

            token = (body or {}).get("token")
            if not token:
                raise ElcoAuthError("le serveur n'a pas renvoyé de jeton")
            self._token = token

    def invalidate(self) -> None:
        self._token = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: Any | None = None,
        _retry: bool = True,
    ) -> Any:
        """Requête authentifiée, avec au plus une reconnexion.

        Une seule : boucler sur des identifiants devenus invalides ferait
        verrouiller le compte.
        """
        if self._token is None:
            await self.async_login()

        url = self._base / path.lstrip("/")
        if params:
            url = url.with_query({k: v for k, v in params.items() if v is not None})

        try:
            async with self._session.request(
                method,
                url,
                json=payload,
                headers={**_HEADERS, AUTH_HEADER: self._token or ""},
                timeout=_TIMEOUT,
            ) as resp:
                status = resp.status
                body = await resp.text()
        except aiohttp.ClientError as err:
            raise ElcoApiError(f"{method} {path} : {err}") from err

        if status in (401, 403):
            self.invalidate()
            if not _retry:
                raise ElcoAuthError(f"{method} {path} : refusé après reconnexion")
            await self.async_login()
            return await self._request(
                method, path, params=params, payload=payload, _retry=False
            )

        if status >= 400:
            raise ElcoApiError(f"{method} {path} : HTTP {status}")

        if not body:
            return None
        try:
            return json.loads(body)
        except ValueError as err:
            raise ElcoApiError(f"{method} {path} : réponse non-JSON") from err

    # -- découverte ---------------------------------------------------------

    async def async_get_plants(self) -> list[Plant]:
        """Liste les installations du compte."""
        raw = await self._request("GET", "v2/remote/plants/lite")
        return [Plant.from_json(item) for item in raw or [] if item.get("gwId")]

    async def async_get_features(self, gateway_id: str) -> Features:
        """Capacités et liste des zones — c'est la découverte du multi-zone."""
        raw = await self._request("GET", f"v2/remote/plants/{gateway_id}/features")
        return Features.from_json(raw or {})

    # -- lectures -----------------------------------------------------------

    async def async_get_plant_data(
        self, gateway_id: str, zone: int = 1, *, first_round_trip: bool = False
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"v2/remote/bsbPlantData/{gateway_id}",
            params={
                "zoneNum": zone,
                "isFirstRoundTrip": str(first_round_trip).lower(),
                "fetchFlame": "true",
                "fetchHpOn": "true",
            },
        )

    async def async_get_data_items(
        self,
        gateway_id: str,
        features: dict[str, Any],
        items: list[tuple[int, int]],
    ) -> dict[str, DataItem]:
        """Lit des mesures fines.

        `items` est une liste de couples (identifiant, zone). Les identifiants
        proviennent de `eRemoteDataItemDto` — surtout PAS de `eMenuItemDto`,
        dont les numéros diffèrent et font répondre HTTP 500 (PROTOCOL.md §7).

        Le serveur retire silencieusement ce que la passerelle ne gère pas :
        l'absence d'une entrée dans la réponse n'est pas une erreur.
        """
        payload = {
            "useCache": False,
            "items": [{"id": item_id, "zn": zone} for item_id, zone in items],
            "features": features,
            "culture": "en-US",
        }
        try:
            raw = await self._request(
                "POST",
                f"v2/remote/dataItems/{gateway_id}/get",
                params={"umsys": "si"},
                payload=payload,
            )
        except ElcoApiError as err:
            # Les mesures fines sont un supplément : leur absence ne doit pas
            # priver l'utilisateur du reste.
            _LOGGER.debug("dataItems indisponibles pour %s : %s", gateway_id, err)
            return {}

        out: dict[str, DataItem] = {}
        for entry in (raw or {}).get("items") or []:
            item = DataItem.from_json(entry)
            if item.name:
                out[item.name] = item
        return out

    # -- écritures ----------------------------------------------------------

    async def async_set_zone_temperatures(
        self,
        gateway_id: str,
        zone: int,
        *,
        is_cooling: bool,
        comfort: float,
        reduced: float,
        old_comfort: float | None,
        old_reduced: float | None,
    ) -> None:
        """Écrit les consignes d'une zone.

        `is_cooling` décide si l'écriture porte sur la consigne de chauffage ou
        celle de refroidissement. L'omettre modifie le mauvais paramètre dès que
        la machine change de saison, sans message d'erreur.

        `old_*` est le garde-fou anti-écrasement concurrent du protocole :
        l'appelant doit fournir la valeur réellement lue.
        """
        await self._request(
            "POST",
            f"v2/remote/bsbZones/{gateway_id}/{zone}/temperatures",
            params={"isCooling": str(is_cooling).lower()},
            payload={
                "new": {"comf": comfort, "econ": reduced},
                "old": {"comf": old_comfort, "econ": old_reduced},
            },
        )

    async def async_set_zone_mode(
        self,
        gateway_id: str,
        zone: int,
        *,
        is_cooling: bool,
        mode: int,
        old_mode: int | None,
    ) -> None:
        await self._request(
            "POST",
            f"v2/remote/bsbZones/{gateway_id}/{zone}/mode",
            params={"isCooling": str(is_cooling).lower()},
            payload={"new": mode, "old": old_mode},
        )

    async def async_set_dhw_temperature(
        self,
        gateway_id: str,
        *,
        comfort: float,
        reduced: float,
        old_comfort: float | None,
        old_reduced: float | None,
    ) -> None:
        await self._request(
            "POST",
            f"v2/remote/bsbPlantData/{gateway_id}/dhwTemp",
            payload={
                "new": {"comf": comfort, "econ": reduced},
                "old": {"comf": old_comfort, "econ": old_reduced},
            },
        )

    async def async_set_dhw_mode(
        self, gateway_id: str, *, mode: int, old_mode: int | None
    ) -> None:
        await self._request(
            "POST",
            f"v2/remote/bsbPlantData/{gateway_id}/dhwMode",
            payload={"new": mode, "old": old_mode},
        )


def build_state(gateway_id: str, raw: dict[str, Any]) -> PlantState:
    """Convertit une réponse `bsbPlantData` en état de domaine."""
    return PlantState.from_json(gateway_id, raw or {})
