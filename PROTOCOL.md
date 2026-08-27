# Remocon-net / Remote Thermo — observed protocol

Reverse-engineering notes on the API used by this integration, gathered on
2026-08-15 in read-only mode against a `FinalUser` account, BSB gateway
(Elco Aerotop S2).

The API is neither documented nor public. It may change without notice.

---

## 1. Two APIs coexist on the same host

| Base | Use | Status |
|---|---|---|
| `https://www.remocon-net.remotethermo.com/api/` | mobile app API | **preferred** — see §8 |
| `https://www.remocon-net.remotethermo.com/R2/` | web interface | useful for the BSB catalog (§5) |

The mobile API is clearly superior: token authentication, clean JSON,
dedicated setters per field, discovery of installations and zones. The
contract was established by decompiling the Android app 7.0.1.40358
(`com.remotethermo.remoconnet`, Kotlin/Ktor).

The host choice depends on the brand (`AppSettings.serverUrl`) — for Elco /
Remocon it's `www.remocon-net.remotethermo.com`, for Ariston
`www.ariston-net.remotethermo.com`, etc. A multi-brand integration should
make the host configurable.

## 2. `/R2/` authentication

```
POST /R2/Account/Login?returnUrl=%2FR2%2FHome
{"email": ..., "password": ..., "rememberMe": true, "language": "English_Gb"}
```

Cookie-based session. An HTTP 200 response whose body contains
`<title>LOGIN</title>` means **not authenticated or not authorized** — the
server does not return 401. Any caller must detect this case explicitly.

## 3. Route table

The web interface publishes its full route table on every page, via
`my.appUrls.initialize({...})`. It's the most reliable source of truth for
discovering endpoints; it can be extracted from a simple GET on
`/R2/Plant/Index/{gatewayId}?navMenuItem=PlantDashboard`.

Useful routes:

| Route | Note |
|---|---|
| `POST /R2/PlantHomeBsb/GetData/{gw}` | dashboard data (see §4) |
| `POST /R2/PlantHomeBsb/SetData/{gw}` | **business setter** — preferable to raw datapoint writes |
| `POST /R2/PlantDhwBsb/Save/{gw}` | DHW setpoints and mode |
| `GET /R2/PlantMenuBsb/UserMenu/{gw}?navMenuItem=BsbUserMenu` | **full BSB tree as embedded JSON** (see §5) |
| `GET /R2/PlantMenuBsb/ReadDataPoints/{gw}?addresses=a,b,c` | read by address, **10 max per request** |
| `POST /R2/PlantMenuBsb/WriteDataPoints/{gw}` | write by address |
| `GET /R2/PlantManagement/Index/{gw}?navMenuItem=ManagePlants` | list of the account's installations |
| `GET /R2/PlantLog/GetDataPoints/{gw}?itemIds=&from=&to=` | history |

Reserved for installer/CAT accounts — a `FinalUser` is redirected to the
login page: all of `/R2/PlantLogConfig/*`, `/TechR2/*`.

`GET /R2/Plant/NavMenuPartial/{gw}` **without** `?activeItem=` returns 500.

## 4. `PlantHomeBsb/GetData`

```json
{"useCache": false, "zone": 1, "filter": {"progIds": null, "plant": true, "zone": true}}
```

`useCache: true` returns a server cache: re-reading right after a write
returns the old value. Use `false` for any confirmation re-read.

Observed fields:

- `plantData`: `outsideTemp`, `dhwStorageTemp`, `dhwComfortTemp`,
  `dhwReducedTemp`, `dhwMode`, `dhwEnabled`, `heatPumpOn`, `flameSensor`,
  `hasOutsideTempProbe`, `hasDhwStorageProbe`, `*Error`
- `zoneData`: `mode`, `isHeatingActive`, `isCoolingActive`, `heatOrCoolRequest`,
  `hasRoomSensor`, `roomTemp`, `chComfortTemp`, `chReducedTemp`,
  `coolComfortTemp`, `coolReducedTemp`, `desiredRoomTemp`, `*ProtectionTemp`

### Pitfalls

- **`heatPumpOn` is at the installation level**, not the DHW's. It's `true`
  whenever the heat pump is running for heating *or* cooling *or* DHW.
  `dhwMode == 1` means "DHW allowed", not "DHW currently heating". Concluding
  `dhwMode == 1 and heatPumpOn` ⇒ "DHW heating" is **wrong**: measured on
  2026-08-15, the tank was losing 0.9 °C/h at rest while this condition held
  true.
- **`isHeatingActive` / `isCoolingActive` do not describe the zone's
  activity**, but the **machine's hydraulic mode**. Measured on 2026-08-15:
  `isHeatingActive` was `true` at 30.5 °C outside temperature, with
  `heatOrCoolRequest` at `false` and the circulator stopped — the machine
  had left cooling mode at 10:03 solely to charge the tank, and stayed there
  afterwards. Reading these as "the zone is heating" makes the UI display
  "heating" in the middle of summer.
- **`heatOrCoolRequest` is the only field that says what the circuit
  actually wants.**
- **There is no field indicating that DHW is actually heating**, but it can
  be inferred without ambiguity: the machine is running (`heatPumpOn`) while
  no zone is requesting anything (`heatOrCoolRequest`) ⇒ it's working for
  DHW. Instantaneous, no threshold or season involved.

  Inferring this from the slope of `dhwStorageTemp` was tried and then
  abandoned: it took **30 minutes** to react, because the tank's temperature
  starts by *dropping* when charging begins (measured: 40.0 → 38.5 °C before
  rising back up).
- **Distinct heating and cooling setpoints**: `chComfortTemp` and
  `coolComfortTemp`, with different bounds. Read and write whichever matches
  the current mode.
- `hasRoomSensor: false` is common: the setpoint then drives a heating curve,
  there's no room temperature. `roomTemp` is `0.0`.
- `outsideTemp` is the outdoor probe, **not** a room temperature — do not
  expose it as a `climate` entity's `current_temperature`.

## 5. BSB tree and addressing

`GET /R2/PlantMenuBsb/UserMenu/{gw}?navMenuItem=BsbUserMenu` returns HTML
containing `nodes: [...]` as JSON. Each node:

```json
{"nodeId": "Heating_circuit_1", "label": "Heating circuit 1",
 "dataPoints": [{"address": 2950542, "lineNum": "710", "label": "710 - Comfort setpoint",
                 "unitLabel": "°C", "readOnly": false, "min": 0.0, "max": 0.0,
                 "decimals": 0, "increment": 0.0, "uiElement": 4}]}
```

This is the correct way to discover addresses: **do not hardcode addresses**,
they depend on the installation.

### Address structure

`address = (page << 16) | parameter`

Verified across three families:

| Family | Page | Parameter |
|---|---|---|
| Holidays zone 1 / 2 / 3 | 45 / 46 / 47 | 2 |
| Diagnostics consumers zone 1 / 2 / 3 | 105 / 106 / 107 | 2681 (measured flow), 2589 (flow setpoint) |

The step between zones is therefore exactly **65536 (0x10000)**. The address
for an unlisted zone can be derived from a listed zone. Verified:
`6949497 − 65536 = 6883961` does exist and is readable.

### Reading

`ReadDataPoints` returns per address:

```json
{"address": 6883869, "valueAsNumber": 19.0, "valueAsString": null,
 "osv": false, "deviceFailure": false, "bsbErrorCode": 0, "anyError": false}
```

- **`osv: true` means "no value available"**. The numeric value is then a
  sentinel (`0.0`, or `140.0` for a setpoint). Always check `osv` before
  using `valueAsNumber`.
- **A single unknown address fails the whole batch with HTTP 500.**
  Scanning addresses is therefore impractical, and would generate an error
  volume likely to trigger the rate limiter
  (`/R2/ApiRateLimiterWhiteList/` exists server-side). Only query addresses
  from the catalog.

### Observed addresses (Aerotop S2, BSB gateway)

| Address | Line | Label | Writable |
|---|---|---|---|
| 2950516 | 700 | Heating circuit 1 — Operating mode | yes |
| 2950542 | 710 | Heating circuit 1 — Comfort setpoint | yes |
| 2950544 | 712 | Heating circuit 1 — Reduced setpoint | yes |
| 2950646 | 720 | Heating curve slope | yes |
| 2950653 | 730 | Summer/winter heating limit | yes |
| 6621734 | 901 | Cooling circuit 1 — Operating mode | yes |
| 6621722 | 902 | Cooling circuit 1 — Comfort setpoint | yes |
| 6625603 | 903 | Cooling circuit 1 — Reduced setpoint | yes |
| 3212657 | 1600 | DHW — Operating mode | yes |
| 3212985 | 1610 | DHW — Nominal setpoint | yes |
| 6883869 | — | Flow temp setp cooling 1 (derived) | no |
| 6883961 | — | Flow temp cooling 1 (derived) | no |
| 6949497 | 8786 | Flow temp cooling 2 | no |
| 6949405 | 8787 | Flow temp setp cooling 2 | no |
| 7015033 | 8816 | Flow temp cooling 3 | no |
| 7014941 | 8817 | Flow temp setp cooling 3 | no |

The `Energy_meter` node exposes **81 read-only points**: heat delivered for
heating (3121), heat delivered for DHW (3122), cooling delivered (3123),
energy consumed for heating / DHW / cooling (3124-3126), annual coefficient
of performance (3120), over ten periods. Usable as a base for an Energy
dashboard and a measured COP.

### Known limitation

On the machine tested, **the measured flow temperatures** (addresses
`…2681`) all return `osv: true`, for all three circuits — only the cooling
circuit 1 flow *setpoint* holds a usable value. The water temperature shown
in the mobile app therefore likely comes from the `/api/v2/` API (§1), not
from `/R2/`.

## 6. Writing

```
POST /R2/PlantMenuBsb/WriteDataPoints/{gw}
[{"address": 2950542, "newValueAsNumber": 20.0, "oldValueAsNumber": 19.0,
  "newValueAsString": null, "oldValueAsString": null,
  "newOsv": false, "oldOsv": false}]
```

`oldValueAsNumber` is the BSB protocol's guard against concurrent
overwrites. Sending `null` disarms this protection: read the current value
before writing.

For setpoints and modes, prefer the business setters
(`PlantHomeBsb/SetData`, `PlantDhwBsb/Save`) over raw datapoint writes: they
apply server-side validation and are what the web interface itself uses.

## 7. Mobile API `/api/v2/` — the contract

Base: `https://www.remocon-net.remotethermo.com/api`

### Authentication

```
POST /api/v2/accounts/login
{"usr": ..., "pwd": ..., "imp": false, "notTrack": true,
 "appInfo": {"os": 2, "appVer": "7.0.1.40358", "appId": "com.remotethermo.remoconnet"}}
```

Response: `{"token": ..., "act": {...}, "incompatibleAppVersion": null}`.

The token is then presented in the **`ar.authToken` header** — not as a
cookie, not as `Authorization: Bearer`.

### The server filters on `User-Agent`

**Check this first on any unexplained HTTP 403.** The server refuses agents
that identify as generic clients:

| `User-Agent` | Response |
|---|---|
| `ktor-client` (the app's own) | **200** |
| `HomeAssistant/2025.8.1 aiohttp/3.12.0 Python/3.13` | 403 |
| `Python/3.13 aiohttp/3.12.0` | 403 |

Credentials have nothing to do with it: same account, same payload. Any
HTTP library that sets its own default agent — and Home Assistant does, on
its shared session — gets refused right at login. The header must therefore
be forced explicitly on **every** request.

`act` contains the account's identity (name, email, phone, country). Never
log it.

### An expired token answers `405`, not `401`

**Check this second, on any unexplained HTTP 405.** The status this server
returns for a token it does not accept is **`405 Method Not Allowed`**.
Not 401, not 403, and no body to disambiguate it.

Verified 2026-08-27 with no credentials at all — an absent, empty or
syntactically invalid `ar.authToken` behaves identically:

| Request | Response |
|---|---|
| `GET /v2/remote/plants/lite` | **405** |
| `GET /v2/remote/plants/{gw}/features` | **405** |
| `GET /v2/remote/bsbPlantData/{gw}` | **405** |
| `GET /v2/remote/bsbZones/{gw}/1` | **405** |
| `GET /v2/remote/actions/{gw}/mostRecent` | **405** |
| `POST /v2/remote/bsbPlantData/{gw}/dhwMode` | **405** |
| `POST /v2/remote/bsbZones/{gw}/1/mode` | **405** |

Reproduce it in one line, no account needed:

```
curl -s -o /dev/null -w '%{http_code}\n' -A ktor-client \
  -H 'ar.authToken: bogus' \
  https://www.remocon-net.remotethermo.com/api/v2/remote/plants/lite
```

**Why this matters.** A client that only treats 401/403 as "authenticate
again" never drops the dead token, and every later cycle replays it for the
same 405. That is not a passing error — it is permanent, until the process
restarts. It cost 46 h of silence on 2026-08-25: the Home Assistant config
entry stayed `loaded` and merely logged one failed update per cycle, because
a failed refresh is not a setup failure. Observed token lifetime was roughly
**5 days**.

The trap is that 405 is *also* the honest HTTP meaning of "wrong method", so
it cannot be read as "bad credentials" either. The two are indistinguishable
from the outside, which settles what a client should do: re-authenticate and
retry once, then — if a token minted seconds ago is refused all the same —
report a failed cycle, **not** an authentication failure. A fresh token
refused is not a password problem, and prompting for the password would send
the user chasing the wrong thing.

### Useful endpoints for a BSB gateway

| Method | Path | Content |
|---|---|---|
| GET | `/v2/remote/plants/lite` | **list of installations**: `gwId`, `gwSerial`, `plantName`, `gwSysType`, `gwFwVer`, `mqttApiVersion`, and postal address |
| GET | `/v2/remote/plants/{gw}/features` | `zones[]` (number, name, hidden), `hpSys`, `hasMetering`, `distinctHeatCoolSetpoints`… — **this is zone discovery** |
| GET | `/v2/remote/bsbPlantData/{gw}?zoneNum=N` | installation + zone data (equivalent to `PlantHomeBsb/GetData`, abbreviated names) |
| GET | `/v2/remote/bsbZones/{gw}/{zone}` | zone-only data, **with `prevValue`** |
| POST | `/v2/remote/bsbPlantData/{gw}/dhwTemp` | DHW setpoint |
| POST | `/v2/remote/bsbPlantData/{gw}/dhwMode` | DHW mode |
| POST | `/v2/remote/bsbZones/{gw}/{zone}/temperatures` | zone setpoints |
| POST | `/v2/remote/bsbZones/{gw}/{zone}/mode` | zone mode |
| GET | `/v2/remote/actions/{gw}/mostRecent` | action history (`k` = kind, `t` = date, `z` = zone) |

### Shape of writes

All setters use the same `ChangeValueDto` envelope:

```json
{"new": <value>, "old": <value>}
```

The anti-overwrite guard from §6 therefore also applies here: `old` must be
the value that was actually read.

For setpoints, `<value>` is a `TemperaturesDto`: `{"comf": …, "econ": …}`.
For modes, it's an integer.

**`?isCooling=true|false` is mandatory** on `/bsbZones/{gw}/{z}/temperatures`
and `/bsbZones/{gw}/{z}/mode`. It decides whether the write applies to the
heating or the cooling setpoint — the two are distinct and have different
bounds. An integration that ignores it changes the wrong parameter as soon
as the machine switches season, **with no error message whatsoever**.

### The mobile token doesn't open `/R2/`

Verified: presenting `ar.authToken` on an `/R2/` endpoint returns the login
page. The two APIs have independent sessions. An integration that needs
both (the BSB datapoints of §5 are only accessible via `/R2/`) must maintain
**two authentications**, one per API.

Name mapping between the two APIs:

| `/api/v2` | `/R2` |
|---|---|
| `outTemp` | `outsideTemp` |
| `dhwTemp` | `dhwStorageTemp` |
| `dhwComfTemp` / `dhwReduTemp` | `dhwComfortTemp` / `dhwReducedTemp` |
| `hpOn` | `heatPumpOn` |
| `flame` | `flameSensor` |
| `zones.N.chComfTemp` | `zoneData.chComfortTemp` |
| `zones.N.coolComfTemp` | `zoneData.coolComfortTemp` |
| `zones.N.heatingOn` / `coolingOn` | `isHeatingActive` / `isCoolingActive` |
| `zones.N.heatOrCoolReq` | `heatOrCoolRequest` |
| `zones.N.hasRoomSens` | `hasRoomSensor` |

### `dataItems` — fine-grained measurements, and the two-enumerations trap

```
POST /v2/remote/dataItems/{gw}/get?umsys=si
{"useCache": false,
 "items": [{"id": 38, "zn": 0}, {"id": 22, "zn": 0}],
 "features": <object returned by /plants/{gw}/features, as-is>,
 "culture": "en-US"}
```

Response: `{"items": [{"id": "ChFlowTemp", "zone": 0, "kind": 1, "min": 0.0,
"max": 140.0, "step": 0.1, "value": 18.3, "decimals": 1, "readOnly": true,
"error": false, "invalid": false}]}`.

This is where the **measurements** missing from `bsbPlantData` live —
notably the water circuit's flow temperature.

**The trap.** The app defines *two* enumerations with identical entry names
but **different numbers**:

| Name | `eMenuItemDto` (for `/v2/menuItems`) | `eRemoteDataItemDto` (for `/v2/remote/dataItems`) |
|---|---|---|
| `OutsideTemp` | 1 | **4** |
| `ChFlowTemp` | 123 | **38** |
| `ChFlowSetpointTemp` | 122 | **65** |

Using `eMenuItemDto` numbers on `dataItems` gets an **HTTP 500**, with no
clue whatsoever. This is the only observed cause of failure — the endpoint
is perfectly accessible to BSB gateways.

Other useful `eRemoteDataItemDto` identifiers: `DhwTemp(3)`,
`DhwStorageTemperature(5)`, `ZoneMeasuredTemp(10)`, `ZoneDesiredTemp(12)`,
`IsHeatingPumpOn(22)`, `HeatingCircuitPressure(23)`, `HeatingFlowTemp(68)`,
`CoolingFlowTemp(69)`.

**An unsupported identifier is silently dropped from the response**, with
no error. You must therefore ask broadly and only expose what comes back —
never assume an identifier exists. On the Aerotop S2 tested, only
`ChFlowTemp(38)` and `IsHeatingPumpOn(22)` come back.

`/v2/menuItems/{gw}` stays at HTTP 500 on this gateway, whatever variant is
tried. Doesn't matter: `dataItems` covers the need.

### MQTT: a false lead, verified

`/v2/remote/plants/lite` returns `mqttApiVersion`, which raises hope for
push updates. **It is not one.** The app doesn't embed any MQTT client (no
library, no broker, no topic in the binary): this field describes the
protocol between the **gateway and the cloud**, which a third-party client
has no access to. There's nothing to listen to — polling remains the only
option.

In the app, this number is only used to switch between two consumption
report formats, in tests against `mqttApiVersion >= 8`.

### Gateway generations

`mqttApiVersion` is also the best indicator of what a gateway can do. On the
machine tested — Aerotop S2, firmware `201.115.61C` — it's **3**, and this
is the most plausible explanation for the HTTP 500s on `menuItems` and
`dataItems`: these endpoints, and therefore identifiers like
`HpChFlowTemperature` and friends, belong to newer generations. An
integration must therefore never assume that an `eMenuItemDto` catalog
identifier is readable: it must probe, and degrade gracefully.

### Zone mode: one write, never two (decompiled 2026-08-20)

The raw BSB tree (§5) lists "Heating circuit 1 — Operating mode" and
"Cooling circuit 1 — Operating mode" as two separate addresses (700 and
901), which looks like two independently-writable registers reachable
through `isCooling`. **They are not, from the app's perspective, on
`/api/v2/`.** Decompiled `com.remotethermo.remoconnet` 7.0.1.40358 (see
"How to redo this" below) to settle it after an incident where a cooling
zone switched to heating the moment it was told to turn off.

`SetBsbZoneModeAsyncOperationBase.doRunAsync` — used by all four zone
buttons (Off/Auto/Reduced/Comfort) — issues exactly **one** `setMode`
call:

```kotlin
val isCooling = mPrevZoneData.isCoolingActive()   // current hydraulic state, read before the write
zoneProxy.setMode(gatewayId, zone, isCooling, newMode, prevMode)
```

`isCooling` always mirrors `BsbZoneData.isCoolingActive` — a read-only
flag reflecting current hydraulic activity, never something the operation
decides based on the target mode. **The app has no heat-only or
cool-only command at all**: only `eBsbZoneMode` exists (`Off`, `Auto`,
`Reduced`, `Comfort`), and the machine alone decides whether `Auto` ends
up heating or cooling. `SetBsbZoneOffAsyncOperation` and friends are
trivial subclasses with no override — there is no special-cased "off
means write both circuits" path anywhere.

`eBsbZoneMode`'s wire values are an explicit constructor int, not
ordinal-dependent: `Off(0)`, `Auto(1)`, `Reduced(2)`, `Comfort(3)`. Worth
double-checking against any code that assumes a different order — a
prior version of the Home Assistant integration had `Comfort`/`Reduced`
swapped (2/3 instead of 3/2), which silently sent "eco" requests as
"comfort" and vice versa.

`BsbZoneData` (the client-side model) has exactly one `mode` field
(`OptionVariable<eBsbZoneMode>`); `isHeatingActive`/`isCoolingActive` are
separate plain booleans, not a second mode value. Confirms §4's warning
that these two describe hydraulic activity, not a settable mode.

### Zone mode: the write can silently do nothing

`SetBsbZoneModeAsyncOperationBase.onAfterEnqueue()` always follows a mode
write with `SynchronizeBsbZoneDataAsyncOperation`, delayed 5 s
(`AsyncOperationHelper.withDefaultRunDelay` resolves to `Level.TRACE_INT`
in the decompiled source — a JADX artifact where a plain `5000` literal
happened to match logback's `TRACE` constant; read it as `5000 ms`). That
operation:

- Fetches the zone with the cache fully bypassed
  (`CacheExpirationPolicy.getAlwaysExpired()`).
- Compares the fresh `mode` (and all four setpoints) against the values
  expected right after the write.
- On mismatch: logs a `WARNING` and returns failure. **No retry** — the
  queue just records the failure (`mFailedTrackableOperations`) for the
  UI to surface; the user is expected to notice and retry by hand.

**An HTTP 200 on `POST .../bsbZones/{gw}/{z}/mode` is therefore not proof
the write took effect.** This is the most likely explanation for the
2026-08-20 incident (a cooling zone switching to heating instead of
turning off): the write silently didn't land, and nothing detected it
until Home Assistant's next scheduled poll picked up whatever the machine
was actually doing by then. The Home Assistant integration's
`climate.py` (`_async_write_zone_mode`) now reproduces this same
write-then-verify pattern.

### Cache TTL for zone data (client-side, informational)

`DefaultCacheExpirationPolicy.doCheckIfExpiredBsbZoneData`: base **5
minutes**, multiplied by a per-policy factor. The zone screen's
`loadDataIfExpired()` (triggered when the screen becomes active) uses the
"slow" policy, factor 2.0 → **10 minutes** before it forces a reload. A
few sub-branches drop to `2 × factor` = 4 minutes in specific states.
Roughly comparable to this integration's own default 10-minute
`scan_interval` — opening the app cold is not meaningfully fresher than
Home Assistant's own poll; the freshness gap, if any, is smaller than it
looks.

### How to redo this

APK used (2026-08-20): `~/Downloads/Remocon%2BNET_7.0.1.40358_apkcombo.com.xapk`
on the dev Mac — an `.xapk` is a zip of per-ABI `.apk` files; decompile
`com.remotethermo.remoconnet.apk` specifically, the others are
resource/ABI splits. `jadx` is installed at `/opt/homebrew/bin/jadx`:

```
unzip -o "<file>.xapk" -d xapk_extracted
jadx --no-res -d decompiled xapk_extracted/com.remotethermo.remoconnet.apk
```

Expect several hundred decompilation errors on Kotlin coroutine state
machines — normal, the surrounding business logic still comes out
readable. Relevant packages:

| Package | What's there |
|---|---|
| `.../operations/` | one `Set*AsyncOperation*` class per user action. Start from `SetBsbZoneModeAsyncOperationBase` and its `SetBsbZone{Off,Auto,Comfort,Reduced}AsyncOperation` subclasses; `SynchronizeBsbZoneDataAsyncOperation` for the post-write check |
| `.../data/local/models/` | client-side models and enums, e.g. `BsbZoneData`, `eBsbZoneMode` |
| `.../data/remote/proxies/BsbZoneProxy.java` | the actual HTTP calls (`getData`, `setMode`, `setTemperatures`) |
| `.../data/remote/services/cache_policies/` | `DefaultCacheExpirationPolicy` and friends, one `doCheckIfExpired*` method per entity type |
| `.../presentation/BsbHomeViewModel.java` | the zone screen's view-model — `loadDataIfExpired`, `saveZoneOffMode`, etc. |

## 8. Caution

A rate limiter exists server-side. Observed readings come back roughly
every ~10 minutes and change each time: the gateway has finer-grained
signal than what the integration was sampling. Poll sequentially, with a
delay, and never retry in a loop on error.
