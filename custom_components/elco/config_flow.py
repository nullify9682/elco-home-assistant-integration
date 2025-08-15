import voluptuous as vol
from homeassistant import config_entries
from .const import DOMAIN, CONF_EMAIL, CONF_PASSWORD, CONF_GATEWAY_ID

class ElcoRemoconConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            return self.async_create_entry(title="Elco Remocon", data=user_input)

        data_schema = vol.Schema({
            vol.Required(CONF_EMAIL): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Required(CONF_GATEWAY_ID): str
        })

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)