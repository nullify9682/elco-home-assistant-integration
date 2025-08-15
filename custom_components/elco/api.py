import requests

BASE_URL = "https://www.remocon-net.remotethermo.com/R2"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/139.0.0.0 Safari/537.36",
    "Content-Type": "application/json"
}

ADDR_1 = 2950516
ADDR_2 = 6621734

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

    def get_state(self):
        if not self.logged_in:
            self.login()
        data = self.read_datapoints([ADDR_1, ADDR_2])
        values = {item["address"]: item["valueAsNumber"] for item in data["data"]}
        return values.get(ADDR_1) == 3.0 and values.get(ADDR_2) == 3.0

    def turn_on(self):
        if not self.logged_in:
            self.login()
        self.write_datapoint(ADDR_1, 3, 0)
        self.write_datapoint(ADDR_2, 3, 0)

    def turn_off(self):
        if not self.logged_in:
            self.login()
        self.write_datapoint(ADDR_1, 0, 3)
        self.write_datapoint(ADDR_2, 0, 3)