#!/usr/bin/env python3
import sys
import requests

EMAIL = "maisongranges2i@gmail.com"
PASSWORD = "xifpyh-1timsu-jikKys"
GATEWAY_ID = "F0AD4E3145F5"  # replace with yours
BASE_URL = "https://www.remocon-net.remotethermo.com/R2"

# Addresses
ADDR_1 = 2950516
ADDR_2 = 6621734

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/139.0.0.0 Safari/537.36",
    "Content-Type": "application/json"
}

def login(session: requests.Session):
    payload = {
        "email": EMAIL,
        "password": PASSWORD,
        "rememberMe": True,
        "language": "English_Gb"
    }
    r = session.post(
        f"{BASE_URL}/Account/Login?returnUrl=%2FR2%2FHome",
        json=payload,
        headers=HEADERS,
        allow_redirects=True
    )
    r.raise_for_status()

def write_datapoint(session: requests.Session, address, new_value, old_value):
    url = f"{BASE_URL}/PlantMenuBsb/WriteDataPoints/{GATEWAY_ID}"
    payload = [{
        "address": address,
        "newValueAsNumber": new_value,
        "oldValueAsNumber": old_value,
        "newValueAsString": None,
        "oldValueAsString": None,
        "newOsv": False,
        "oldOsv": False
    }]
    r = session.post(url, json=payload, headers=HEADERS)
    r.raise_for_status()
    return r.json()

def read_datapoints(session: requests.Session, addresses):
    addr_str = ",".join(str(a) for a in addresses)
    url = f"{BASE_URL}/PlantMenuBsb/ReadDataPoints/{GATEWAY_ID}?addresses={addr_str}"
    r = session.get(url, headers=HEADERS)
    r.raise_for_status()
    return r.json()

def get_state(session: requests.Session):
    """Return True if both addresses are in ON state (value 3.0), else False."""
    data = read_datapoints(session, [ADDR_1, ADDR_2])
    values = {item["address"]: item["valueAsNumber"] for item in data["data"]}
    return values.get(ADDR_1) == 3.0 and values.get(ADDR_2) == 3.0

def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("on", "off", "state"):
        print("Usage: elco_switch.py on|off|state")
        sys.exit(1)

    with requests.Session() as s:
        s.headers.update(HEADERS)
        login(s)

        if sys.argv[1] == "on":
            write_datapoint(s, ADDR_1, 3, 0)
            write_datapoint(s, ADDR_2, 3, 0)
        elif sys.argv[1] == "off":
            write_datapoint(s, ADDR_1, 0, 3)
            write_datapoint(s, ADDR_2, 0, 3)
        elif sys.argv[1] == "state":
            if get_state(s):
                print("ON")
            else:
                print("OFF")

if __name__ == "__main__":
    main()