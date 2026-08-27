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

# The server answers **405** — not 401, not 403 — to a token it no longer
# accepts. Verified 2026-08-27 against every authenticated route, GET and
# POST alike: an absent, empty or expired `ar.authToken` yields HTTP 405
# (PROTOCOL.md §7). Treating it as a plain error is what left the
# integration dead for 46 h on 2026-08-25: the token was never dropped, so
# every later cycle replayed the same refused token.
STATUS_TOKEN_REJECTED = 405

# Statuses that mean "log in again, then retry once".
_REAUTH_STATUSES = (401, 403, STATUS_TOKEN_REJECTED)

# Waits between attempts when the connection was never established. Three
# attempts spread over ~8 s stay well inside one poll cycle.
_CONNECT_RETRY_DELAYS = (2.0, 6.0)

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
            status, raw = await self._send(
                "POST",
                self._base / "v2/accounts/login",
                headers=_HEADERS,
                payload=payload,
                label="login",
            )
            if status in (400, 401, 403):
                raise ElcoAuthError(f"credentials refused (HTTP {status})")
            if status >= 400:
                raise ElcoApiError(f"login: HTTP {status}")
            try:
                body = json.loads(raw) if raw else None
            except ValueError as err:
                raise ElcoApiError("login: non-JSON response") from err

            token = (body or {}).get("token")
            if not token:
                raise ElcoAuthError("the server did not return a token")
            self._token = token

    def invalidate(self, token: str | None = None) -> None:
        """Drops the token, optionally only if it is still the current one.

        Passing the token that actually failed matters as soon as the account
        has several installations: their coordinators poll in parallel, and a
        refusal arriving late must not discard a token another one has just
        minted — that would have the two of them logging in in a loop,
        against an account that locks out.
        """
        if token is None or self._token == token:
            self._token = None

    # -- transport ------------------------------------------------------------

    async def _send(
        self,
        method: str,
        url: URL,
        *,
        headers: dict[str, str],
        payload: Any | None = None,
        label: str,
    ) -> tuple[int, str]:
        """Sends one request, retrying only what provably never arrived.

        `ClientConnectorError` covers name resolution and TCP connect
        failures: no connection was ever established, so the request cannot
        have reached the server and replaying it cannot apply a write twice.
        Every other `ClientError` is reported as-is — a response cut off
        mid-read may correspond to an order the machine already accepted,
        and this client drives writes too.

        Retrying at all matters because one failed attempt takes the whole
        installation `unavailable` until the next cycle. Measured over the
        9 days to 2026-08-27: 67 outages, **every one of them exactly one
        cycle long**. Forcing IPv4 (v1.3.0) did not thin them out, so the
        AAAA-query explanation was wrong; the blip is upstream of us and
        tolerating it is the only lever we actually hold.
        """
        attempts = len(_CONNECT_RETRY_DELAYS) + 1
        last: aiohttp.ClientConnectorError | None = None
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(_CONNECT_RETRY_DELAYS[attempt - 1])
            try:
                async with self._session.request(
                    method, url, json=payload, headers=headers, timeout=_TIMEOUT
                ) as resp:
                    return resp.status, await resp.text()
            except aiohttp.ClientConnectorError as err:
                last = err
                _LOGGER.debug(
                    "%s: attempt %d/%d could not connect: %s",
                    label,
                    attempt + 1,
                    attempts,
                    err,
                )
            except aiohttp.ClientError as err:
                raise ElcoApiError(f"{label} : {err}") from err
        raise ElcoApiError(f"{label} : {last}") from last

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

        A refused token shows up as HTTP 405 on this server, not 401 or 403
        — see `STATUS_TOKEN_REJECTED`.
        """
        if self._token is None:
            await self.async_login()

        url = self._base / path.lstrip("/")
        if params:
            url = url.with_query({k: v for k, v in params.items() if v is not None})

        token = self._token or ""
        label = f"{method} {path}"
        status, body = await self._send(
            method,
            url,
            headers={**_HEADERS, AUTH_HEADER: token},
            payload=payload,
            label=label,
        )

        if status in _REAUTH_STATUSES:
            self.invalidate(token)
            if _retry:
                await self.async_login()
                return await self._request(
                    method, path, params=params, payload=payload, _retry=False
                )
            if status == STATUS_TOKEN_REJECTED:
                # A token minted seconds ago and refused all the same: then
                # whatever is wrong, it is not the password. Raising
                # ElcoAuthError here would prompt for it again, over what is
                # far more likely a passing server fault or a route that
                # moved — 405 is also the plain HTTP meaning of "wrong
                # method". One failed cycle, retried at the next one, is the
                # honest outcome.
                raise ElcoApiError(f"{label}: HTTP {status} with a fresh token")
            raise ElcoAuthError(f"{label}: refused after reconnecting")

        if status >= 400:
            raise ElcoApiError(f"{label}: HTTP {status}")

        if not body:
            return None
        try:
            return json.loads(body)
        except ValueError as err:
            raise ElcoApiError(f"{label}: non-JSON response") from err

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
