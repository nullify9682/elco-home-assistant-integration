# Elco Remocon — intégration Home Assistant

Pilote une pompe à chaleur **Elco** (Aerotop et apparentées, passerelle BSB) via
le cloud Remocon-net, sans matériel supplémentaire.

> Intégration non officielle, sans lien avec Elco ni Ariston/Remote Thermo.
> Elle s'appuie sur l'API de leur application mobile, qui n'est ni documentée ni
> garantie : elle peut cesser de fonctionner sans préavis. Le protocole observé
> est documenté dans [`PROTOCOL.md`](PROTOCOL.md).

## Ce que ça apporte

- **Thermostat par zone** — consigne, mode, programmation, avec la bonne
  consigne selon la saison : chauffage et refroidissement ont des paramètres
  distincts, et l'intégration écrit celui qui correspond au mode courant.
- **Température de départ du circuit** — ce que la pompe produit réellement.
  C'est elle qui permet de distinguer un *freecooling* d'un refroidissement
  actif ou d'une chauffe du ballon.
- **Eau chaude sanitaire** — température, consigne, marche/arrêt, et un état
  « en chauffe » **déduit de la pente réelle du ballon** plutôt que d'un
  drapeau qui ment (voir *Pièges* plus bas).
- **Réponse immédiate** — la valeur réglée s'affiche tout de suite, puis
  l'intégration relit la machine et corrige si elle a refusé.
- **Plusieurs pompes, plusieurs zones** — découvertes automatiquement ; une
  seule connexion pour tout le compte.
- **Signaux bruts de diagnostic** — compresseur, circulateur, demandes par
  zone, désactivés par défaut, pour vérifier ce que fait vraiment la machine.

## Installation

Via [HACS](https://hacs.xyz), en dépôt personnalisé :

1. HACS → menu ⋮ → **Dépôts personnalisés**
2. URL : `https://github.com/nullify9682/elco-home-assistant-integration`, type
   **Integration**
3. Installer **Elco Remocon**, puis redémarrer Home Assistant
4. **Paramètres → Appareils et services → Ajouter une intégration → Elco Remocon**

Renseignez l'adresse e-mail et le mot de passe de votre compte Remocon-net. Le
`gateway_id` n'est plus demandé : les installations sont découvertes.

## Entités

| Entité | Remarque |
|---|---|
| `climate` | une par zone ; `current_temperature` = ambiance si la machine a une sonde, sinon température de départ |
| `water_heater` | ballon ECS |
| `sensor` température extérieure | avec `state_class`, donc statistiques long terme |
| `sensor` ballon d'eau chaude | |
| `sensor` température de départ | selon la génération de passerelle |
| `sensor` consigne de départ, pression du circuit | diagnostic, si disponibles |
| `sensor` fonctionnement | `off` / `idle` / `heating` / `cooling` / `freecooling` / `dhw` |
| `sensor` fonctionnement eau chaude | |
| `binary_sensor` × 7 | signaux bruts, **désactivés par défaut** |

Toutes les mesures fines dépendent de la génération de la passerelle. Le serveur
retire silencieusement ce qu'elle ne gère pas : les entités correspondantes ne
sont alors simplement pas créées.

## Options

**Intervalle d'interrogation**, réglable de 2 à 60 minutes (10 par défaut).
Descendre en dessous capte les cycles courts de refroidissement, au prix d'une
sollicitation accrue de leurs serveurs — un limiteur de débit existe côté
Remote Thermo.

## Pièges du protocole, et ce que l'intégration en fait

Ces comportements sont contre-intuitifs et documentés en détail dans
[`PROTOCOL.md`](PROTOCOL.md) :

- **`hpOn` n'est pas « l'eau chaude chauffe ».** C'est un drapeau au niveau de
  l'installation, vrai dès que la pompe tourne pour n'importe quelle raison.
  Le croiser avec `dhwMode` produit un capteur qui annonce une chauffe pendant
  que le ballon refroidit. L'intégration déduit l'état réel de la pente de la
  température du ballon.
- **Deux consignes distinctes**, chauffage et refroidissement, avec des bornes
  différentes. Le paramètre `isCooling` décide laquelle est écrite ; l'ignorer
  modifie la mauvaise en plein été, sans erreur.
- **La température extérieure n'est pas une température ambiante.** L'exposer
  comme `current_temperature` d'un thermostat compare des grandeurs sans
  rapport.
- **`invalid` ne suffit pas à écarter une valeur.** Certains compteurs
  renvoient un entier 32 bits en débordement sans lever de drapeau ; les bornes
  déclarées les filtrent.

## Migration depuis la version 0.1

Les identifiants d'entités ont changé de format pour permettre plusieurs
installations. La migration est **automatique** : les `entity_id` existants et
tout l'historique enregistré sont conservés. Aucune automatisation ni tableau de
bord n'est à reprendre.

## Limites connues

- Les compteurs d'énergie du menu BSB ne sont pas exposés : ils ne sont
  accessibles que par l'API web, et sur les machines testées les compteurs de
  consommation ne sont pas instrumentés — un COP calculé à partir d'eux serait
  faux.
- Pas de remontée en push. La passerelle communique avec le cloud en MQTT, mais
  ce canal n'est pas accessible à un client tiers ; l'interrogation périodique
  reste la seule voie.

## Licence

MIT.
