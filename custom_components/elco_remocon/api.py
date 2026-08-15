"""Client for the Remocon-net / Remote Thermo mobile API.

The protocol is documented in PROTOCOL.md. This module is the only place that
knows about the transport: the rest of the integration only sees `models.py`.
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

# The server filters on the User-Agent: it responds 403 to anything that
# looks like Python, aiohttp, or Home Assistant, and 200 to the official
# app's Ktor client. It must therefore be announced explicitly, or Home
# Assistant's default session gets refused right at login.
_HEADERS = {
    "User-Agent": "ktor-client",
    "Accept": "application/json",
}


class ElcoApiError(Exception):
    """Failure to communicate with the API."""


class ElcoAuthError(ElcoApiError):
    """Credentials refused, or token became invalid."""


class ElcoClient:
    """Async client, one token shared across the whole account.

    A single session serves all installations: this is what lets us open
    only one authentication regardless of how many heat pumps there are.
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

    # -- authentication -------------------------------------------------------

    async def async_login(self) -> None:
        """Obtains a token. Serialized: several concurrent calls only
        trigger one."""
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
                            f"credentials refused (HTTP {resp.status})"
                        )
                    if resp.status >= 400:
                        raise ElcoApiError(f"login: HTTP {resp.status}")
                    body = await resp.json(content_type=None)
            except aiohttp.ClientError as err:
                raise ElcoApiError(f"login unreachable: {err}") from err

            token = (body or {}).get("token")
            if not token:
                raise ElcoAuthError("the server did not return a token")
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
        """Authenticated request, with at most one reconnection.

        Only one: looping on credentials that have become invalid would get
        the account locked out.
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
                raise ElcoAuthError(f"{method} {path}: refused after reconnecting")
            await self.async_login()
            return await self._request(
                method, path, params=params, payload=payload, _retry=False
            )

        if status >= 400:
            raise ElcoApiError(f"{method} {path}: HTTP {status}")

        if not body:
            return None
        try:
            return json.loads(body)
        except ValueError as err:
            raise ElcoApiError(f"{method} {path}: non-JSON response") from err

    # -- discovery ------------------------------------------------------------

    async def async_get_plants(self) -> list[Plant]:
        """Lists the account's installations."""
        raw = await self._request("GET", "v2/remote/plants/lite")
        return [Plant.from_json(item) for item in raw or [] if item.get("gwId")]

    async def async_get_features(self, gateway_id: str) -> Features:
        """Capabilities and zone list — this is multi-zone discovery."""
        raw = await self._request("GET", f"v2/remote/plants/{gateway_id}/features")
        return Features.from_json(raw or {})

    # -- reads ------------------------------------------------------------------

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
        """Reads fine-grained measurements.

        `items` is a list of (identifier, zone) pairs. The identifiers come
        from `eRemoteDataItemDto` — definitely NOT `eMenuItemDto`, whose
        numbers differ and trigger an HTTP 500 (PROTOCOL.md §7).

        The server silently drops whatever the gateway doesn't support: a
        missing entry in the response is not an error.
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
            # Fine-grained measurements are a bonus: their absence must not
            # deprive the user of everything else.
            _LOGGER.debug("dataItems unavailable for %s: %s", gateway_id, err)
            return {}

        out: dict[str, DataItem] = {}
        for entry in (raw or {}).get("items") or []:
            item = DataItem.from_json(entry)
            if item.name:
                out[item.name] = item
        return out

    # -- writes -----------------------------------------------------------------

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
        """Writes a zone's setpoints.

        `is_cooling` decides whether the write applies to the heating or the
        cooling setpoint. Omitting it changes the wrong parameter as soon as
        the machine switches season, with no error message.

        `old_*` is the protocol's guard against concurrent overwrites: the
        caller must supply the value that was actually read.
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
    """Converts a `bsbPlantData` response into domain state."""
    return PlantState.from_json(gateway_id, raw or {})
