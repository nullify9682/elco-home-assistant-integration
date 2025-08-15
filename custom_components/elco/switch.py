from homeassistant.components.switch import SwitchEntity
from .const import DOMAIN

class ElcoSwitch(SwitchEntity):
    def __init__(self, api):
        self._api = api
        self._attr_name = "Elco Heat Pump"
        self._attr_unique_id = "elco_heat_pump_switch"
        self._state = False

    def turn_on(self, **kwargs):
        self._api.turn_on()
        self._state = True
        self.schedule_update_ha_state()

    def turn_off(self, **kwargs):
        self._api.turn_off()
        self._state = False
        self.schedule_update_ha_state()

    def update(self):
        self._state = self._api.get_state()

    @property
    def is_on(self):
        return self._state

async def async_setup_entry(hass, entry, async_add_entities):
    api = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ElcoSwitch(api)], update_before_add=True)