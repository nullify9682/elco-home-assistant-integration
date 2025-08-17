import requests
from homeassistant.components.climate import HVACMode

BASE_URL = "https://www.remocon-net.remotethermo.com/R2"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/139.0.0.0 Safari/537.36",
    "Content-Type": "application/json"
}

ADDR_HEATING_MODE = 2950516
ADDR_COOLING_MODE = 6621734
ADDR_TEMPERATURE = 2950542

class ElcoRemoconAPI:
    def __init__(self, email, password, gateway_id):
        self.email = email
        self.password = password
        self.gateway_id = gateway_id
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.logged_in = False

    def login(self):
        payload = {
            "email": self.email,
            "password": self.password,
            "rememberMe": True,
            "language": "English_Gb"
        }
        r = self.session.post(
            f"{BASE_URL}/Account/Login?returnUrl=%2FR2%2FHome",
            json=payload,
            headers=HEADERS,
            allow_redirects=True
        )
        r.raise_for_status()
        self.logged_in = True

    def write_datapoint(self, address, new_value, old_value):
        url = f"{BASE_URL}/PlantMenuBsb/WriteDataPoints/{self.gateway_id}"
        payload = [{
            "address": address,
            "newValueAsNumber": new_value,
            "oldValueAsNumber": old_value,
            "newValueAsString": None,
            "oldValueAsString": None,
            "newOsv": False,
            "oldOsv": False
        }]
        r = self.session.post(url, json=payload, headers=HEADERS)
        r.raise_for_status()
        return r.json()

    def read_datapoints(self, addresses):
        addr_str = ",".join(str(a) for a in addresses)
        url = f"{BASE_URL}/PlantMenuBsb/ReadDataPoints/{self.gateway_id}?addresses={addr_str}"
        r = self.session.get(url, headers=HEADERS)
        r.raise_for_status()
        return r.json()

    def get_hvac_data(self, zone: int = 1, use_cache: bool = True):
        """Fetch HVAC plant + zone data from the Remocon API."""
        if not self.logged_in:
            self.login()

        url = f"{BASE_URL}/PlantHomeBsb/GetData/{self.gateway_id}"
        payload = {
            "useCache": use_cache,
            "zone": zone,
            "filter": {
                "progIds": None,
                "plant": True,
                "zone": True
            }
        }

        r = self.session.post(url, json=payload, headers=HEADERS)
        r.raise_for_status()
        return r.json()

    def get_hvac_mode(self):
        data = self.read_datapoints([ADDR_HEATING_MODE, ADDR_COOLING_MODE])
        values = {item["address"]: item["valueAsNumber"] for item in data["data"]}
        heating_mode = values.get(ADDR_HEATING_MODE)
        cooling_mode = values.get(ADDR_COOLING_MODE)

        if heating_mode == 3 and cooling_mode == 3:
            return HVACMode.AUTO
        elif heating_mode == 3 and cooling_mode == 0:
            return HVACMode.HEAT
        elif heating_mode == 0 and cooling_mode == 3:
            return HVACMode.COOL
        return HVACMode.OFF

    def set_hvac_mode(self, old_mode : HVACMode, new_mode : HVACMode):
        if not self.logged_in:
            self.login()
        heating_old_value = 3 if old_mode == HVACMode.AUTO or old_mode == HVACMode.HEAT else 0
        cooling_old_value = 3 if old_mode == HVACMode.AUTO or old_mode == HVACMode.COOL else 0

        heating_new_value = 3 if new_mode == HVACMode.AUTO or new_mode == HVACMode.HEAT else 0
        cooling_new_value = 3 if new_mode == HVACMode.AUTO or new_mode == HVACMode.COOL else 0

        self.write_datapoint(ADDR_HEATING_MODE, heating_new_value, heating_old_value)
        self.write_datapoint(ADDR_COOLING_MODE, cooling_new_value, cooling_old_value)

    def set_hvac_temperature(self, new_temp: float, old_temp: float):
        if not self.logged_in:
            self.login()
        self.write_datapoint(ADDR_TEMPERATURE, new_temp, old_temp)

    def set_dhw_temperature(self, comfort_temp: float, reduced_temp: float = None):
        """Set DHW target temperatures (comfort and optionally reduced)."""
        if not self.logged_in:
            self.login()

        # Fetch current plantData first to preserve other fields
        data = self.get_hvac_data()  # or another call if needed specifically for DHW
        plant_data = data["data"]["plantData"]

        payload = {
            "plantData": plant_data,
            "comfortTemp": comfort_temp,
            "reducedTemp": reduced_temp if reduced_temp is not None else plant_data["dhwReducedTemp"]["value"],
            "dhwMode": plant_data["dhwMode"]["value"]
        }

        url = f"{BASE_URL}/PlantDhwBsb/Save/{self.gateway_id}"
        r = self.session.post(url, json=payload, headers=HEADERS)
        r.raise_for_status()
        return r.json()

    def set_dhw_mode(self, mode: int):
        """Turn DHW on or off using the PlantDhwBsb/Save endpoint."""
        if not self.logged_in:
            self.login()

        # Fetch current plantData first
        data = self.get_hvac_data()
        plant_data = data["data"]["plantData"]

        payload = {
            "plantData": plant_data,
            "comfortTemp": plant_data["dhwComfortTemp"]["value"],
            "reducedTemp": plant_data["dhwReducedTemp"]["value"],
            "dhwMode": mode
        }

        url = f"{BASE_URL}/PlantDhwBsb/Save/{self.gateway_id}"
        r = self.session.post(url, json=payload, headers=HEADERS)
        r.raise_for_status()
        return r.json()