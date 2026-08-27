# Elco Remocon — Home Assistant integration

Drives an **Elco** heat pump (Aerotop and related models, BSB gateway) via the
Remocon-net cloud, with no extra hardware.

> Unofficial integration, not affiliated with Elco or Ariston/Remote Thermo.
> It relies on their mobile app's API, which is neither documented nor
> guaranteed: it may stop working without notice. The observed protocol is
> documented in [`PROTOCOL.md`](PROTOCOL.md).

## What it provides

- **Per-zone thermostat** — setpoint, mode, scheduling, with the right
  setpoint for the season: heating and cooling have separate parameters, and
  the integration writes the one that matches the current mode.
- **Circuit flow temperature** — what the pump actually produces. It's what
  lets you tell apart *freecooling* from active cooling or the tank being
  charged.
- **Two separate states, one for the circuit and one for the tank.** When
  the machine is charging hot water, the heat pump sensor goes *idle* and
  the tank's sensor goes to *heating*: you immediately see **what** is being
  heated, instead of an ambiguous "heating".
- **Domestic hot water** — temperature, setpoint, on/off.
- **Immediate feedback** — the value you set is shown right away, then the
  integration re-reads the machine and corrects it if the write was refused.
- **Multiple heat pumps, multiple zones** — discovered automatically; a
  single connection for the whole account.
- **Raw diagnostic signals** — compressor, circulator, per-zone requests,
  disabled by default, to verify what the machine is actually doing.

## Installation

Via [HACS](https://hacs.xyz), as a custom repository:

1. HACS → ⋮ menu → **Custom repositories**
2. URL: `https://github.com/nullify9682/elco-home-assistant-integration`,
   type **Integration**
3. Install **Elco Remocon**, then restart Home Assistant
4. **Settings → Devices & services → Add integration → Elco Remocon**

Enter the email address and password of your Remocon-net account. The
`gateway_id` is no longer requested: installations are discovered
automatically.

## Entities

| Entity | Note |
|---|---|
| `climate` | one per zone; `current_temperature` = room temperature if the machine has a sensor, otherwise flow temperature |
| `water_heater` | DHW tank |
| `sensor` outside temperature | with `state_class`, so long-term statistics |
| `sensor` hot water tank | |
| `sensor` flow temperature | depends on the gateway generation |
| `sensor` flow setpoint, circuit pressure | diagnostic, if available |
| `sensor` operation | the **circuit** only: `off` / `idle` / `heating` / `cooling` — reads `idle` while the tank is being charged |
| `sensor` hot water operation | the **tank** only: `off` / `idle` / `heating` |
| `binary_sensor` × 7 | raw signals, **disabled by default** |

All fine-grained measurements depend on the gateway's generation. The server
silently drops whatever it doesn't support: the corresponding entities are
then simply not created.

## Options

**Polling interval**, from 2 to 60 minutes (**15 by default since 1.4.0**,
10 before). Lowering it catches brief cooling and hot water cycles, at the
cost of more load on their servers — a rate limiter exists on the Remote
Thermo side. Changing the default does **not** move an installation that
already saved this option: set it from the integration's options page.

**Summer/winter switchover point**, 21 °C by default. Above this outside
temperature, the machine cannot be heating the house: it is therefore never
reported as heating. The pump still switches to its heating circuit **to
charge the tank**, and stays there afterwards — without this threshold, the
thermostat would show "heating" for hours in the middle of August.

## Protocol pitfalls, and what the integration does about them

These behaviors are counter-intuitive and documented in detail in
[`PROTOCOL.md`](PROTOCOL.md):

- **`hpOn` does not mean "hot water is heating".** It's an installation-level
  flag, true as soon as the pump is running for any reason. Crossing it with
  `dhwMode` produces a sensor that reports heating while the tank is
  cooling down.
- **`heatingOn` and `coolingOn` are not the zone's activity**, but the
  machine's hydraulic mode. Measured: `heatingOn` true at 30 °C outside,
  because the pump had left cooling mode to charge the tank. The only
  signal that says what the circuit actually wants is **`heatOrCoolReq`**.
  The integration infers everything else from it: the machine running while
  no zone is requesting anything can only be working for hot water.
- **Two distinct setpoints**, heating and cooling, with different bounds.
  The `isCooling` parameter decides which one gets written; ignoring it
  changes the wrong one in the middle of summer, with no error.
- **The outside temperature is not a room temperature.** Exposing it as a
  thermostat's `current_temperature` compares unrelated quantities.
- **`invalid` isn't enough to discard a value.** Some counters return an
  overflowed 32-bit integer without raising any flag; the declared bounds
  filter those out.
- **A refused token answers HTTP 405, not 401 or 403.** Verified on every
  authenticated route, GET and POST alike: absent, empty or expired,
  `ar.authToken` yields `405 Method Not Allowed`. Nothing distinguishes it
  from the status a genuinely wrong method would return, so the integration
  reacts to a 405 by logging in again and retrying once — and, if a token
  minted seconds earlier is refused all the same, reports a **failed cycle
  rather than an authentication failure**: asking for the password would be
  the wrong question.

## Migration from version 0.1

Entity identifiers changed format to support multiple installations.
Migration is **automatic**: existing `entity_id` values and all recorded
history are preserved. No automation or dashboard needs to be updated.

## Known limitations

- The BSB menu's energy counters are not exposed: they're only accessible
  through the web API, and on the machines tested the consumption counters
  aren't instrumented — a COP computed from them would be wrong.
- No push updates. The gateway talks to the cloud over MQTT, but that
  channel isn't accessible to a third-party client; periodic polling remains
  the only way.
- **Sporadic `Timeout while contacting DNS servers`.** Measured over the
  9 days to 2026-08-27: 67 outages, every single one exactly one poll cycle
  long. Each one takes the whole installation `unavailable` until the next
  cycle.

  The session forces IPv4 (`family=socket.AF_INET`, since 1.3.0), on the
  theory that the resolver's AAAA leg was timing out on a LAN with no IPv6
  egress and taking the A query down with it. **That did not thin the
  outages out** — they ran at the same rate for the five days after,
  so the explanation was wrong. The setting is kept (it is harmless, and
  the account is reachable over IPv4) but it is not the fix.

  What the integration does instead, since 1.4.0: **retry the failures that
  provably never arrived.** A name-resolution or TCP-connect failure means
  no connection was ever established, so replaying the request cannot apply
  a write twice — three attempts, ~8 s apart, well inside one cycle. Any
  other error is reported as-is and never replayed: a response cut off
  mid-read may correspond to an order the machine already accepted.

  This is tolerance, not a cure. The blip is upstream of Home Assistant and
  nothing here can stop it happening.

## License

MIT.
