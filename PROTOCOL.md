# Remocon-net / Remote Thermo — protocole observé

Notes de rétro-ingénierie de l'API utilisée par cette intégration, établies le
2026-08-15 en lecture seule contre un compte `FinalUser`, passerelle BSB
(Elco Aerotop S2).

L'API n'est ni documentée ni publique. Elle peut changer sans préavis.

---

## 1. Deux API coexistent sur le même hôte

| Base | Usage | Statut |
|---|---|---|
| `https://www.remocon-net.remotethermo.com/api/` | API des applications mobiles | **à privilégier** — voir §8 |
| `https://www.remocon-net.remotethermo.com/R2/` | interface web | utile pour le catalogue BSB (§5) |

L'API mobile est nettement supérieure : authentification par jeton, JSON propre,
setters dédiés par champ, découverte des installations et des zones. Le contrat
a été établi en décompilant l'application Android 7.0.1.40358
(`com.remotethermo.remoconnet`, Kotlin/Ktor).

Le choix de l'hôte dépend de la marque (`AppSettings.serverUrl`) — pour Elco /
Remocon c'est `www.remocon-net.remotethermo.com`, pour Ariston
`www.ariston-net.remotethermo.com`, etc. Une intégration multi-marques doit
rendre l'hôte configurable.

## 2. Authentification `/R2/`

```
POST /R2/Account/Login?returnUrl=%2FR2%2FHome
{"email": ..., "password": ..., "rememberMe": true, "language": "English_Gb"}
```

Session par cookie. Une réponse HTTP 200 dont le corps contient
`<title>LOGIN</title>` signifie **non authentifié ou non autorisé** — le serveur
ne renvoie pas 401. Tout appelant doit détecter ce cas explicitement.

## 3. Table de routes

L'interface web publie sa table complète dans chaque page, via
`my.appUrls.initialize({...})`. C'est la source de vérité la plus fiable pour
découvrir des endpoints ; elle est extractible d'un simple GET sur
`/R2/Plant/Index/{gatewayId}?navMenuItem=PlantDashboard`.

Routes utiles :

| Route | Remarque |
|---|---|
| `POST /R2/PlantHomeBsb/GetData/{gw}` | données d'accueil (voir §4) |
| `POST /R2/PlantHomeBsb/SetData/{gw}` | **setter métier** — préférable aux écritures de datapoints bruts |
| `POST /R2/PlantDhwBsb/Save/{gw}` | consignes et mode ECS |
| `GET /R2/PlantMenuBsb/UserMenu/{gw}?navMenuItem=BsbUserMenu` | **arbre BSB complet en JSON embarqué** (voir §5) |
| `GET /R2/PlantMenuBsb/ReadDataPoints/{gw}?addresses=a,b,c` | lecture par adresse, **10 maximum par requête** |
| `POST /R2/PlantMenuBsb/WriteDataPoints/{gw}` | écriture par adresse |
| `GET /R2/PlantManagement/Index/{gw}?navMenuItem=ManagePlants` | liste des installations du compte |
| `GET /R2/PlantLog/GetDataPoints/{gw}?itemIds=&from=&to=` | historique |

Réservé aux comptes installateur/CAT — un `FinalUser` est redirigé vers la page
de login : tout `/R2/PlantLogConfig/*`, `/TechR2/*`.

`GET /R2/Plant/NavMenuPartial/{gw}` **sans** `?activeItem=` renvoie 500.

## 4. `PlantHomeBsb/GetData`

```json
{"useCache": false, "zone": 1, "filter": {"progIds": null, "plant": true, "zone": true}}
```

`useCache: true` renvoie un cache serveur : une relecture juste après une
écriture rend l'ancienne valeur. Utiliser `false` pour toute relecture de
confirmation.

Champs observés :

- `plantData` : `outsideTemp`, `dhwStorageTemp`, `dhwComfortTemp`,
  `dhwReducedTemp`, `dhwMode`, `dhwEnabled`, `heatPumpOn`, `flameSensor`,
  `hasOutsideTempProbe`, `hasDhwStorageProbe`, `*Error`
- `zoneData` : `mode`, `isHeatingActive`, `isCoolingActive`, `heatOrCoolRequest`,
  `hasRoomSensor`, `roomTemp`, `chComfortTemp`, `chReducedTemp`,
  `coolComfortTemp`, `coolReducedTemp`, `desiredRoomTemp`, `*ProtectionTemp`

### Pièges

- **`heatPumpOn` est au niveau de l'installation**, pas de l'ECS. Il vaut `true`
  quand la PAC tourne pour le chauffage *ou* le refroidissement *ou* l'ECS.
  `dhwMode == 1` signifie « ECS autorisée », pas « ECS en train de chauffer ».
  Conclure `dhwMode == 1 and heatPumpOn` ⇒ « ECS en chauffe » est **faux** :
  mesuré le 2026-08-15, le ballon perdait 0.9 °C/h en veille pendant que cette
  condition était vraie.
- **`isHeatingActive` / `isCoolingActive` ne décrivent pas l'activité de la
  zone**, mais le **mode hydraulique de la machine**. Mesuré le 2026-08-15 :
  `isHeatingActive` valait `true` par 30,5 °C de température extérieure, avec
  `heatOrCoolRequest` à `false` et le circulateur à l'arrêt — la machine avait
  quitté le refroidissement à 10:03 uniquement pour pouvoir charger le ballon,
  et y est restée après. Les lire comme « la zone chauffe » fait afficher
  « chauffage » en plein été.
- **`heatOrCoolRequest` est le seul champ qui dit ce que veut le circuit.**
- **Il n'existe aucun champ indiquant que l'ECS chauffe réellement**, mais il
  se déduit sans ambiguïté : la machine tourne (`heatPumpOn`) alors qu'aucune
  zone ne demande rien (`heatOrCoolRequest`) ⇒ elle travaille pour l'ECS.
  Instantané, sans seuil ni saison.

  Une inférence par la pente de `dhwStorageTemp` a été essayée puis
  abandonnée : elle mettait **30 minutes** à réagir, parce que la température
  du ballon commence par *baisser* au démarrage de la charge (mesuré :
  40,0 → 38,5 °C avant de remonter).
- **Consignes chaud et froid distinctes** : `chComfortTemp` et `coolComfortTemp`,
  avec des bornes différentes. Lire et écrire celle qui correspond au mode
  courant.
- `hasRoomSensor: false` est fréquent : la consigne pilote alors une courbe de
  chauffe, il n'y a pas de température d'ambiance. `roomTemp` vaut `0.0`.
- `outsideTemp` est la sonde extérieure, **pas** une température ambiante — ne
  pas l'exposer comme `current_temperature` d'une entité `climate`.

## 5. Arbre BSB et adressage

`GET /R2/PlantMenuBsb/UserMenu/{gw}?navMenuItem=BsbUserMenu` renvoie du HTML
contenant `nodes: [...]` en JSON. Chaque nœud :

```json
{"nodeId": "Heating_circuit_1", "label": "Heating circuit 1",
 "dataPoints": [{"address": 2950542, "lineNum": "710", "label": "710 - Comfort setpoint",
                 "unitLabel": "°C", "readOnly": false, "min": 0.0, "max": 0.0,
                 "decimals": 0, "increment": 0.0, "uiElement": 4}]}
```

C'est la façon correcte de découvrir les adresses : **ne pas coder d'adresses en
dur**, elles dépendent de l'installation.

### Structure d'une adresse

`address = (page << 16) | paramètre`

Vérifié sur trois familles :

| Famille | Page | Paramètre |
|---|---|---|
| Holidays zone 1 / 2 / 3 | 45 / 46 / 47 | 2 |
| Diagnostics consumers zone 1 / 2 / 3 | 105 / 106 / 107 | 2681 (départ mesuré), 2589 (consigne de départ) |

Le pas entre zones est donc exactement **65536 (0x10000)**. L'adresse d'une zone
non listée se déduit d'une zone listée. Vérifié : `6949497 − 65536 = 6883961`
existe bien et est lisible.

### Lecture

`ReadDataPoints` renvoie par adresse :

```json
{"address": 6883869, "valueAsNumber": 19.0, "valueAsString": null,
 "osv": false, "deviceFailure": false, "bsbErrorCode": 0, "anyError": false}
```

- **`osv: true` signifie « pas de valeur disponible »**. La valeur numérique est
  alors une sentinelle (`0.0`, ou `140.0` pour une consigne). Toujours tester
  `osv` avant d'exploiter `valueAsNumber`.
- **Une seule adresse inconnue fait échouer tout le lot en HTTP 500.** Le
  balayage d'adresses est donc impraticable, et générerait un volume d'erreurs
  susceptible de déclencher le limiteur de débit (`/R2/ApiRateLimiterWhiteList/`
  existe côté serveur). N'interroger que des adresses issues du catalogue.

### Adresses observées (Aerotop S2, passerelle BSB)

| Adresse | Ligne | Libellé | Écriture |
|---|---|---|---|
| 2950516 | 700 | Heating circuit 1 — Operating mode | oui |
| 2950542 | 710 | Heating circuit 1 — Comfort setpoint | oui |
| 2950544 | 712 | Heating circuit 1 — Reduced setpoint | oui |
| 2950646 | 720 | Heating curve slope | oui |
| 2950653 | 730 | Summer/winter heating limit | oui |
| 6621734 | 901 | Cooling circuit 1 — Operating mode | oui |
| 6621722 | 902 | Cooling circuit 1 — Comfort setpoint | oui |
| 6625603 | 903 | Cooling circuit 1 — Reduced setpoint | oui |
| 3212657 | 1600 | DHW — Operating mode | oui |
| 3212985 | 1610 | DHW — Nominal setpoint | oui |
| 6883869 | — | Flow temp setp cooling 1 (déduit) | non |
| 6883961 | — | Flow temp cooling 1 (déduit) | non |
| 6949497 | 8786 | Flow temp cooling 2 | non |
| 6949405 | 8787 | Flow temp setp cooling 2 | non |
| 7015033 | 8816 | Flow temp cooling 3 | non |
| 7014941 | 8817 | Flow temp setp cooling 3 | non |

Le nœud `Energy_meter` expose **81 points** en lecture seule : chaleur livrée
chauffage (3121), chaleur livrée ECS (3122), froid livré (3123), énergie
consommée chauffage / ECS / froid (3124-3126), coefficient de performance annuel
(3120), sur dix périodes. Base exploitable pour le tableau de bord Énergie et
un COP mesuré.

### Limite connue

Sur l'installation testée, **les températures de départ mesurées** (adresses
`…2681`) renvoient toutes `osv: true`, pour les trois circuits — seule la
*consigne* de départ froid du circuit 1 porte une valeur exploitable. La
température d'eau visible dans l'application mobile provient donc vraisemblablement
de l'API `/api/v2/` (§1), non de `/R2/`.

## 6. Écriture

```
POST /R2/PlantMenuBsb/WriteDataPoints/{gw}
[{"address": 2950542, "newValueAsNumber": 20.0, "oldValueAsNumber": 19.0,
  "newValueAsString": null, "oldValueAsString": null,
  "newOsv": false, "oldOsv": false}]
```

`oldValueAsNumber` est le garde-fou anti-écrasement concurrent du protocole BSB.
L'envoyer à `null` désarme cette protection : lire la valeur courante avant
d'écrire.

Pour les consignes et modes, préférer les setters métier
(`PlantHomeBsb/SetData`, `PlantDhwBsb/Save`) aux écritures de datapoints brutes :
ils appliquent la validation du serveur et sont ce qu'utilise l'interface web.

## 7. API mobile `/api/v2/` — le contrat

Base : `https://www.remocon-net.remotethermo.com/api`

### Authentification

```
POST /api/v2/accounts/login
{"usr": ..., "pwd": ..., "imp": false, "notTrack": true,
 "appInfo": {"os": 2, "appVer": "7.0.1.40358", "appId": "com.remotethermo.remoconnet"}}
```

Réponse : `{"token": ..., "act": {...}, "incompatibleAppVersion": null}`.

Le jeton se présente ensuite dans **l'entête `ar.authToken`** — pas en cookie,
pas en `Authorization: Bearer`.

### Le serveur filtre sur le `User-Agent`

**À vérifier en premier devant tout HTTP 403 inexpliqué.** Le serveur refuse les
agents qui se déclarent comme des clients génériques :

| `User-Agent` | Réponse |
|---|---|
| `ktor-client` (celui de l'application) | **200** |
| `HomeAssistant/2025.8.1 aiohttp/3.12.0 Python/3.13` | 403 |
| `Python/3.13 aiohttp/3.12.0` | 403 |

Les identifiants n'y sont pour rien : c'est le même compte, la même charge
utile. Toute bibliothèque HTTP qui pose son agent par défaut — et Home Assistant
le fait sur sa session partagée — se fait refuser dès le login. Il faut donc
imposer l'entête explicitement sur **chaque** requête.

`act` contient l'identité du compte (nom, courriel, téléphone, pays). Ne jamais
la journaliser.

### Endpoints utiles pour une passerelle BSB

| Méthode | Chemin | Contenu |
|---|---|---|
| GET | `/v2/remote/plants/lite` | **liste des installations** : `gwId`, `gwSerial`, `plantName`, `gwSysType`, `gwFwVer`, `mqttApiVersion`, et l'adresse postale |
| GET | `/v2/remote/plants/{gw}/features` | `zones[]` (numéro, nom, masquée), `hpSys`, `hasMetering`, `distinctHeatCoolSetpoints`… — **c'est la découverte des zones** |
| GET | `/v2/remote/bsbPlantData/{gw}?zoneNum=N` | données installation + zone (équivalent de `PlantHomeBsb/GetData`, noms abrégés) |
| GET | `/v2/remote/bsbZones/{gw}/{zone}` | données de zone seule, **avec `prevValue`** |
| POST | `/v2/remote/bsbPlantData/{gw}/dhwTemp` | consigne ECS |
| POST | `/v2/remote/bsbPlantData/{gw}/dhwMode` | mode ECS |
| POST | `/v2/remote/bsbZones/{gw}/{zone}/temperatures` | consignes de zone |
| POST | `/v2/remote/bsbZones/{gw}/{zone}/mode` | mode de zone |
| GET | `/v2/remote/actions/{gw}/mostRecent` | historique des actions (`k` = genre, `t` = date, `z` = zone) |

### Forme des écritures

Tous les setters prennent le même enveloppe `ChangeValueDto` :

```json
{"new": <valeur>, "old": <valeur>}
```

Le garde-fou anti-écrasement du §6 s'applique donc aussi ici : `old` doit être
la valeur réellement lue.

Pour les consignes, `<valeur>` est un `TemperaturesDto` : `{"comf": …, "econ": …}`.
Pour les modes, c'est un entier.

**`?isCooling=true|false` est obligatoire** sur `/bsbZones/{gw}/{z}/temperatures`
et `/bsbZones/{gw}/{z}/mode`. C'est lui qui décide si l'écriture porte sur la
consigne de chauffage ou celle de refroidissement — les deux sont distinctes et
ont des bornes différentes. Une intégration qui l'ignore modifie le mauvais
paramètre dès que la machine change de saison, **sans aucun message d'erreur**.

### Le jeton mobile n'ouvre pas `/R2/`

Vérifié : présenter `ar.authToken` sur un endpoint `/R2/` renvoie la page de
login. Les deux API ont des sessions indépendantes. Une intégration qui a besoin
des deux (les datapoints BSB du §5 ne sont accessibles que par `/R2/`) doit
maintenir **deux authentifications**, une par API.

Correspondance des noms entre les deux API :

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

### `dataItems` — les mesures fines, et le piège des deux énumérations

```
POST /v2/remote/dataItems/{gw}/get?umsys=si
{"useCache": false,
 "items": [{"id": 38, "zn": 0}, {"id": 22, "zn": 0}],
 "features": <objet renvoyé par /plants/{gw}/features, tel quel>,
 "culture": "en-US"}
```

Réponse : `{"items": [{"id": "ChFlowTemp", "zone": 0, "kind": 1, "min": 0.0,
"max": 140.0, "step": 0.1, "value": 18.3, "decimals": 1, "readOnly": true,
"error": false, "invalid": false}]}`.

C'est ici que vivent les **mesures** absentes de `bsbPlantData` — notamment la
température de départ du circuit d'eau.

**Le piège.** L'application définit *deux* énumérations aux noms d'entrée
identiques mais aux **numéros différents** :

| Nom | `eMenuItemDto` (pour `/v2/menuItems`) | `eRemoteDataItemDto` (pour `/v2/remote/dataItems`) |
|---|---|---|
| `OutsideTemp` | 1 | **4** |
| `ChFlowTemp` | 123 | **38** |
| `ChFlowSetpointTemp` | 122 | **65** |

Employer les numéros de `eMenuItemDto` sur `dataItems` fait répondre **HTTP 500**,
sans le moindre indice. C'est la seule cause d'échec observée — l'endpoint est
parfaitement accessible aux passerelles BSB.

Autres identifiants `eRemoteDataItemDto` utiles : `DhwTemp(3)`,
`DhwStorageTemperature(5)`, `ZoneMeasuredTemp(10)`, `ZoneDesiredTemp(12)`,
`IsHeatingPumpOn(22)`, `HeatingCircuitPressure(23)`, `HeatingFlowTemp(68)`,
`CoolingFlowTemp(69)`.

**Un identifiant non supporté est retiré de la réponse en silence**, sans erreur.
Il faut donc demander large et n'exposer que ce qui revient — jamais supposer
qu'un identifiant existe. Sur l'Aerotop S2 testée, seuls `ChFlowTemp(38)` et
`IsHeatingPumpOn(22)` reviennent.

`/v2/menuItems/{gw}` reste en HTTP 500 sur cette passerelle, quelles que soient
les variantes essayées. Sans importance : `dataItems` couvre le besoin.

### MQTT : fausse piste, vérifiée

`/v2/remote/plants/lite` renvoie `mqttApiVersion`, ce qui laisse espérer une
remontée en push. **Ce n'en est pas une.** L'application n'embarque aucun client
MQTT (aucune bibliothèque, aucun broker, aucun sujet dans le binaire) : ce champ
décrit le protocole entre la **passerelle et le cloud**, auquel un client tiers
n'a pas accès. Il n'y a rien à écouter — le sondage reste la seule option.

Dans l'application, ce numéro ne sert qu'à basculer entre deux formats de
rapports de consommation, sur des tests `mqttApiVersion >= 8`.

### Générations de passerelles

Le `mqttApiVersion` est aussi le meilleur indicateur de ce qu'une passerelle sait
faire. Sur la machine testée — Aerotop S2, firmware `201.115.61C` — il vaut **3**,
et c'est l'explication la plus plausible des HTTP 500 sur `menuItems` et
`dataItems` : ces endpoints, et donc les identifiants `HpChFlowTemperature` &
consorts, appartiennent aux générations plus récentes. Une intégration ne doit
donc jamais supposer qu'un identifiant du catalogue `eMenuItemDto` est lisible :
il faut sonder, et dégrader proprement.

## 8. Prudence

Un limiteur de débit existe côté serveur. Les relevés observés remontent toutes
les ~10 minutes et changent à chaque fois : la passerelle a du signal plus fin
que ce que l'intégration échantillonnait. Sonder séquentiellement, avec un délai,
et ne jamais réessayer en boucle sur une erreur.
