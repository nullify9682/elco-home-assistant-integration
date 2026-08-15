"""Configuration flow."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import ElcoApiError, ElcoAuthError, ElcoClient
from .const import (
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_SUMMER_SWITCHOVER,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_SUMMER_SWITCHOVER,
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MAX_SUMMER_SWITCHOVER,
    MIN_SCAN_INTERVAL_MINUTES,
    MIN_SUMMER_SWITCHOVER,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
    }
)


class ElcoConfigFlow(ConfigFlow, domain=DOMAIN):
    """One entry per account; installations are discovered."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            # Credentials are VERIFIED before creating the entry: without
            # this the integration installs then silently fails, and the
            # user has no way to know what's wrong.
            error, plant_count = await _async_validate(
                self.hass, email, user_input[CONF_PASSWORD]
            )
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(email.lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=email,
                    data={CONF_EMAIL: email, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                    description_placeholders={"count": str(plant_count)},
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allows fixing the password without reconfiguring everything."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            error, _ = await _async_validate(
                self.hass, entry.data[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="current-password",
                        )
                    )
                }
            ),
            description_placeholders={CONF_EMAIL: entry.data[CONF_EMAIL]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return ElcoOptionsFlow()


class ElcoOptionsFlow(OptionsFlow):
    """Polling cadence and summer/winter switchover point."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL_MINUTES: int(
                        user_input[CONF_SCAN_INTERVAL_MINUTES]
                    ),
                    CONF_SUMMER_SWITCHOVER: float(user_input[CONF_SUMMER_SWITCHOVER]),
                }
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL_MINUTES,
                        default=options.get(
                            CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL_MINUTES,
                            max=MAX_SCAN_INTERVAL_MINUTES,
                            step=1,
                            unit_of_measurement="min",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_SUMMER_SWITCHOVER,
                        default=options.get(
                            CONF_SUMMER_SWITCHOVER, DEFAULT_SUMMER_SWITCHOVER
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SUMMER_SWITCHOVER,
                            max=MAX_SUMMER_SWITCHOVER,
                            step=0.5,
                            unit_of_measurement="°C",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )


async def _async_validate(hass, email: str, password: str) -> tuple[str | None, int]:
    """Attempts a login. Returns (error key or None, installation count)."""
    session = async_create_clientsession(hass)
    client = ElcoClient(session, email, password)
    try:
        await client.async_login()
        plants = await client.async_get_plants()
    except ElcoAuthError:
        return "invalid_auth", 0
    except ElcoApiError:
        return "cannot_connect", 0
    if not plants:
        return "no_plants", 0
    return None, len(plants)
