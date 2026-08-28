# Loxone Collector — mémoire du projet

Collecteur de données Loxone + dashboard web. Brique de collecte du projet
plus large **MCP-Loxone** (récupérer les valeurs de plusieurs miniservers
Loxone pour ensuite générer des factures / décomptes de charges par
appartement). L'historique complet des décisions vit dans le doc projet
Claude "MCP-Loxone" (`loxone-collector-architecture.md`) ; ce fichier-ci est
le résumé opérationnel pour travailler dans ce dépôt sans avoir à tout
réexpliquer.

**Cible de déploiement (mise à jour 2026-08-26)** : le projet visait à
l'origine un Raspberry Pi 4B (2 Go RAM, carte SD 32 Go) -- c'est là que la
v1 a été validée en prod. Décision confirmée le 2026-08-26 : la cible passe
à un PC dédié (Lenovo ThinkCentre M70S Gen3) sous **Ubuntu Server**, pour
héberger les 3 sites suivis en parallèle (voir "Infrastructure serveur
interne" dans le doc projet). **Il n'y a donc plus de contrainte matérielle
réelle** (RAM/CPU/usure de carte SD) -- à garder en tête pour toute future
décision d'architecture (choix de dépendances, framework frontend, etc.) :
les raisons historiques de minimalisme (Pi 2 Go, SD card) ne s'appliquent
plus, seule la simplicité de maintenance pour un développeur solo reste un
critère valable.

## Ce qui fonctionne aujourd'hui

- Poller Python (thread de fond dans `app.py`) qui interroge un ou plusieurs
  Miniservers Loxone via l'API locale HTTP (Basic Auth) — structure
  (`/data/LoxAPP3.json`) + valeurs (`/jdev/sps/io/<uuid>`) — et écrit en
  SQLite (`db.py`).
- Classification automatique par appartement (regex `APPxx`) et type de
  ressource (eau chaude/froide, énergie solaire/réseau/injectée/consommée)
  — `classification.py`. Corrections manuelles via `/admin`, jamais écrasées
  ensuite (flags `apartment_manual`/`resource_type_manual` en base).
  **Règle stricte : ne jamais faire écrire le poller sur une valeur dont le
  flag `_manual` est à 1.**
- Dashboard Flask (`/`) : sidebar appartement > type de ressource (bascule
  `?group_by=room`), graphs Chart.js (vendored en local, pas de dépendance
  internet), sélection multiple de capteurs.
- Déployé et validé en production sur le Pi de l'utilisateur (réseau local),
  firmware Miniserver 17.1.7.27.
- Accès externe (URL DynDNS Loxone) **entièrement fonctionnel** : structure
  en HTTP simple + lecture des valeurs live via `protocol: "websocket"`
  (voir `loxone_ws_client.py` et la section dédiée ci-dessous). Validé en
  réel : 356/356 points reçus en ~2s sur un miniserver client distant. Le
  protocole HTTP simple (`/jdev/sps/io/<uuid>`) reste 404 à distance (par
  design côté Loxone) mais ce n'est plus un problème : le protocole
  Websocket chiffré (dépendance optionnelle `pyloxone-api`) le remplace
  pour tout miniserver accédé à distance.

## Accès distant Loxone (DynDNS Cloud) — RÉSOLU

**Usage réel derrière ce besoin** : valider la connexion à un miniserver
client (auth, structure, lecture des points) **avant de se déplacer sur
place** pour installer le Raspberry Pi.

**Diagnostic final** (confirmé en conditions réelles sur "MS-Arlopi",
2026-08-26) : `/jdev/sps/io/<uuid>` (lecture live par point) échoue en 404
à travers le relais distant Loxone ("Remote Connect"), **aussi bien en HTTP
qu'en lecture active via le protocole Websocket chiffré** (même commande
envoyée sur le canal authentifié RSA/AES+token : 404 renvoyé par le
Miniserver lui-même, confirmé par une réponse LL correctement déchiffrée —
pas une erreur générique du relais). Conclusion : Loxone désactive
volontairement l'interrogation ponctuelle par point à distance, et attend
des clients distants qu'ils utilisent le flux d'événements ("monitor
mode") plutôt que de l'interroger point par point — cohérent avec la doc
officielle Loxone ("Remote Connect only supports using HTTPS/WSS").

**Solution qui fonctionne** : une fois connecté et authentifié via
Websocket chiffré (`jdev/sps/enablebinstatusupdate`), le Miniserver pousse
immédiatement (< 2s en pratique) un burst contenant TOUTES les valeurs
courantes de l'installation. Implémenté dans `loxone_ws_client.py`
(dépendance optionnelle `pyloxone-api`, voir `requirements-websocket.txt`),
activé par `protocol: "websocket"` dans la config d'un miniserver (voir
`config.external.yaml.example`). **Validé en réel : 356/356 points reçus
en 1.8s sur MS-Arlopi** (313 valeurs numériques + 43 valeurs texte, ex: les
flags `jLocked` qui sont des chaînes vides à l'état non verrouillé — pas
une erreur).

Historique des fausses pistes (gardé pour mémoire, utile si un jour un
autre miniserver se comporte différemment) :
1. ~~Rate-limiting du relais~~ — réfutée par un test avec délai de 0.3s
   entre requêtes HTTP (`read_delay_seconds`, toujours dans le code,
   défaut 0, inoffensif) : aucun changement, toujours 1/356.
2. ~~Écoute passive via Websocket ne reçoit rien~~ — **faux négatif dû à un
   bug de mon propre code**, pas au protocole : le filtre `_looks_like_uuid`
   exigeait 4 tirets dans l'UUID, mais les UUID Loxone dans
   `ValueStatesTable`/`TextStatesTable` (voir `pyloxone_api/message.py`)
   sont au format 8-4-4-16 (3 tirets, pas le format UUID standard 8-4-4-4-12
   à 4 tirets) — toutes les valeurs reçues étaient donc silencieusement
   jetées par le filtre. Un script de debug sans filtre
   (`scripts/debug_ws_listen.py`) a montré le burst réel de 588 valeurs dès
   t+0.1s, révélant le bug. Corrigé (regex exacte sur le vrai format).
3. Lecture ACTIVE point par point via Websocket (`jdev/sps/io/<uuid>` envoyé
   en commande chiffrée) — fonctionnait comme mécanisme (round-trip rapide,
   ~0.4s/point) mais recevait un 404 explicite du Miniserver pour chaque
   point, comme en HTTP. Abandonnée au profit de l'écoute passive du burst
   (plus rapide ET fonctionne réellement). Gardée en script de diagnostic
   (`scripts/debug_ws_raw.py`) pour référence.

Scripts de diagnostic disponibles :
- `scripts/diagnose_websocket.py <config> <nom_miniserver> [secondes]` —
  test complet structure + burst de valeurs, avec décompte numérique/texte/
  manquant. C'est le script à lancer pour valider un nouveau miniserver
  client avant une visite sur site.
- `scripts/debug_ws_raw.py` / `scripts/debug_ws_listen.py` — scripts de
  debug bas niveau utilisés pour ce diagnostic, gardés pour un futur souci
  similaire.

## Conventions du projet

- **Langue** : tout le code (commentaires, docstrings), les messages de log,
  l'UI et la doc sont en français. Rester cohérent.
- **Secrets** : jamais de mot de passe en clair dans `config.yaml`. Toujours
  `${NOM_VAR}` avec la vraie valeur dans `.env` (gitignored). `config.py`
  valide au chargement qu'il ne reste pas d'accolade résiduelle après
  substitution (`_validate_no_leftover_placeholders`) — un bug réel déjà
  rencontré (accolade en trop dans `.env` → 401 mystérieux).
- **SQLite** : WAL + `synchronous=NORMAL`, downsampling horaire automatique
  après `raw_retention_days`, checkpoint WAL quotidien, VACUUM seulement
  manuel/mensuel (`scripts/vacuum_db.py`). Conçu à l'origine pour ménager la
  carte SD du Pi (contrainte disparue depuis le passage à un PC Ubuntu
  Server, voir cible de déploiement ci-dessus) — le design est gardé tel
  quel car il reste pertinent pour la taille de la base à long terme (des
  années d'historique sur 3 sites), pas par nécessité matérielle.
- **Migrations DB** : toute évolution du schéma `series_meta`/`readings`
  passe par `db._migrate_schema()` (ALTER TABLE idempotent), jamais par un
  DROP/recreate — la base de prod a de l'historique réel à préserver, quel
  que soit le matériel qui l'héberge.
- **Dépendances** : plus de contrainte RAM/CPU forte (voir cible de
  déploiement ci-dessus), mais rester raisonnable reste une bonne pratique
  pour un projet Python solo — Flask + requests + PyYAML + python-dotenv
  suffisent jusqu'ici. Un ajout de dépendance (framework frontend avec
  build step, ORM, etc.) est désormais une question de simplicité de
  maintenance, plus une question de ressources matérielles.
- **Config alternative** : `app.py` accepte un chemin de config en argument
  CLI (`python app.py config.demo.yaml`), utilisé pour les configs de
  test/démo sans toucher à la prod (`config.yaml`).

## Fichiers de config (aucun n'est commité sauf les `.example`)

- `config.yaml` — production (PC Ubuntu Server dédié, réseau local).
- `config.external.yaml` — test d'accès via URL externe Loxone (voir
  limitation ci-dessus). Non poursuivi activement.
- `config.demo.yaml` + `scripts/seed_demo_data.py` — génère une base
  synthétique mais réaliste (3 appartements, tous les types de ressource,
  14 jours d'historique) pour valider le dashboard **sans dépendre d'un
  accès Loxone réel**. Utile pour isoler un bug d'affichage d'un bug de
  connexion.

## Piège d'environnement important (sessions Claude/Cowork)

Quand ce dépôt est édité via le pont device-bridge d'une session Claude
distante (et non depuis un Terminal natif sur cette machine) : **le mode WAL
de SQLite échoue avec "disk I/O error"** sur ce point de montage. Toute
commande qui ouvre une nouvelle connexion SQLite en écriture (seed, migration,
tests qui créent une base) doit donc être validée dans un environnement avec
accès disque natif (ex: le sandbox cloud de la session), puis **exécutée par
l'utilisateur lui-même dans son propre Terminal** sur cette machine — pas via
l'outil `device_bash` de la session. Les éditions de fichiers texte
(code, config, docs) via `device_bash` fonctionnent normalement, seule
l'ouverture d'une base SQLite WAL pose problème.

Autre limite : `device_bash` ne peut pas supprimer de fichiers par défaut
(permission désactivée) — déplacer dans `_to_delete/` (gitignored) plutôt que
`rm`.

## Données historiques ("Statistics" Loxone) -- besoin réel identifié

Le poller (protocole websocket, voir plus haut) ne récupère QUE la valeur
"à l'instant T" de chaque point, en continu depuis son démarrage. Pour les
décomptes de charges, on a aussi besoin de consommations passées, avant le
démarrage du collecteur -- ce n'est PAS le même besoin et PAS résolu par le
websocket.

Piste identifiée : la fonctionnalité native "Statistics" de Loxone
enregistre déjà un historique sur la carte SD du Miniserver lui-même, mais
:
- elle est opt-in PAR POINT (activée à la main dans Loxone Config, propriété
  "Statistics" sur le bloc de fonction) -- si jamais activée sur un point,
  aucun historique n'existe pour lui, quelle que soit la méthode de
  récupération ;
- deux modes : Standard/absolu, ou Différentiel/delta (recommandé pour les
  compteurs -- évite les resets de compteur de fausser le delta) ;
- récupération via `jdev/sps/getStatisticInfo/<uuid>` (renvoie depuis quand
  l'historique existe) puis `jdev/sps/getStatistic/<uuid>/{raw|diff}/<de>/<à>/...`
  -- API HTTP standard (même auth Basic que `/data/LoxAPP3.json`), mais
  d'après la doc Loxone ce mécanisme est explicitement hors du protocole
  chiffré websocket "Remote Connect" -- probable LAN uniquement (comme
  `/jdev/sps/io/<uuid>` en lecture active, voir plus haut), mais **pas encore
  confirmé empiriquement**.
- rétention illisible/illimitée par défaut : la carte SD se remplit jusqu'à
  purge manuelle.

`scripts/check_statistics.py` teste ça sans rien changer dans Loxone Config :
1. parcourt `LoxAPP3.json`, repère les contrôles ayant un bloc
   `details.statistic`/`details.statisticV2` (= Statistics activé) ;
2. pour un échantillon, appelle `jdev/sps/getStatisticInfo/<uuid>` et affiche
   la réponse brute (code HTTP + corps).

```bash
python3 scripts/check_statistics.py config.yaml maison            # LAN d'abord (moins d'inconnues)
python3 scripts/check_statistics.py config.external.yaml MS-Arlopi   # puis à distance
```

Deux résultats possibles à ce stade :
- **RÉSOLU (2026-08-26) sur MS-Arlopi** : `check_statistics.py` v1 a
  d'abord donné un faux négatif (0 contrôle détecté) -- la détection basée
  sur `details.statistic`/`statisticV2` dans LoxAPP3.json était mal ciblée.
  v2 a confirmé par test direct : `getStatisticInfo` répond HTTP 200 sur les
  42 contrôles, à travers le relais distant "Remote Connect" (contrairement
  à la lecture live qui y est bloquée), avec un historique actif depuis le
  2025-10-01 (`activeSince` en epoch Unix standard, PAS l'epoch Loxone 2009).
  `fetch_statistics_sample.py` a ensuite validé le format de
  `getStatistic/.../raw/...` en conditions réelles : réponse binaire
  (Content-Type `application/octet-stream`), enregistrements Uint32 ts
  (Unix UTC) + Float64 valeur, little-endian -- dates et valeurs cohérentes
  sur un test de 30 jours (résolution native ~horaire observée).
- **Module de backfill livré** : `scripts/backfill_statistics.py` -- importe
  automatiquement, pour chaque point suivi par le poller, tout l'historique
  disponible (depuis `activeSince`) dans `readings_hourly`, sans jamais
  écraser une ligne déjà écrite par le poller live (`db.upsert_hourly_batch`,
  `ON CONFLICT DO NOTHING`). Support `--dry-run` et `--since YYYY-MM-DD`.
  **Premier `--dry-run` réel (2026-08-26) : 0/356 point détecté** -- bug
  trouvé : le mapping cherchait `statisticV2` sous `details.statisticV2`
  (même hypothèse erronée que `check_statistics.py` v1), alors qu'il est en
  réalité à la racine du contrôle (`control["statisticV2"]`). Corrigé
  (repli sur `details.*` gardé par prudence). Pas encore re-testé par
  l'utilisateur avec le correctif.

Piste alternative explorée : LoxBerry (communauté allemande, plateforme de
plugins tierce pour Raspberry Pi, pas un produit Loxone officiel). Deux
familles de plugins pertinentes : Lox2MQTT (valeurs live seulement, même
principe que notre solution websocket, rien de nouveau pour l'historique) et
Stats4Lox-NG (github.com/mschlenstedt/LoxBerry-Plugin-Stats4Lox-NG) qui
d'après son architecture semble être un import/ordonnanceur autour du
mécanisme `/stats/` natif de Loxone lui-même -- pas un enregistreur
indépendant. Autrement dit : LoxBerry ne contourne pas la nécessité d'avoir
Statistics activé côté Loxone -- il s'appuie dessus, comme notre propre
approche prévue.

## Dashboard énergie (réseau vs solaire) + consommations par zone (2026-08-26)

Suite au backfill réussi, analyse de la vraie base (`data/loxone_externe_test.db`,
lecture directe en mode `?immutable=1` pour contourner le souci WAL du pont
device_bash -- voir "Piège d'environnement" plus bas) a révélé un schéma de
données plus riche que prévu : ce n'est pas juste "3 appartements", c'est un
petit immeuble mixte -- 3 appartements (App 1/2/3) + un local commercial
("Commerce") + un rez-jardin ("Rez Jardin") + parties communes ("Communs")
+ des compteurs de bâtiment (Réseau, Production, Batterie). Chaque zone a
généralement un compteur Grid (`energie_reseau`) et/ou Solaire
(`energie_solaire`), avec 6 states par compteur : `actual` (kW, historisé),
`total` (kWh cumulatif, historisé via Statistics), et
`totalDay`/`totalWeek`/`totalMonth`/`totalYear` (compteurs vivants SANS
historique Statistics propre -- confirmé : `readings_hourly` post-backfill
ne contient que des states `actual`/`total`/`totalNeg`, jamais les totalX).

Conséquence pour les graphs de conso journalière/mensuelle sur une période
passée : on ne peut PAS utiliser totalDay historiquement (n'existe pas dans
le passé), donc on dérive une consommation journalière depuis `total`
(cumulatif) via un delta jour-sur-jour (`db.query_daily_last` : MAX(value)
par jour UTC -- valide car "total" est un index strictement croissant, donc
son max journalier = son relevé de fin de journée). C'est exactement la
logique d'un décompte de charges (différence entre deux relevés de
compteur).

**Changements livrés :**
- `classification.py` : `DEFAULT_APARTMENT_PATTERN` étendu pour reconnaître
  aussi "Commerce"/"Rez Jardin"/"Commun(s)" en plus de "APPxx" (zones
  billables non numérotées, sinon classées "Sans appartement").
- `loxone_client.py` : `_extract_statistic_output_units()` -- lit le format
  par output dans `statisticV2` (ex: "0,000kW" -> "kW", au niveau racine du
  contrôle, PAS sous `details`, même correction que le backfill) pour donner
  une unité PAR STATE (kW pour "actual", kWh pour "total"...) au lieu d'une
  seule unité par contrôle comme avant.
- `db.py` : `query_latest()` (dernière valeur connue, sans scanner tout
  l'historique -- important après un backfill de 600k+ lignes) et
  `query_daily_last()` (relevé de fin de journée par jour UTC, pour dériver
  des deltas de consommation).
- `app.py` : deux routes -- `GET /api/series/<id>/latest` et
  `GET /api/series/<id>/daily?days=N` (relevés + deltas journaliers).
- `templates/index.html` + `static/js/energy.js` + CSS : 3 onglets --
  "Explorer" (comportement d'origine, inchangé), "Énergie" (sélecteur de
  zone, tuiles KPI aujourd'hui/ce mois + autoconsommation, graph puissance
  instantanée réseau vs solaire, graphs barres conso/prod journalière (30j)
  et mensuelle (12 mois)), "Consommations par zone" (générique : zone +
  n'importe quel type de ressource avec un state "total", KPI
  jour/semaine/mois/année si dispo sinon relevé courant, mêmes graphs
  barres) -- couvre chauffage et eau chaude en plus de l'énergie, en vue du
  futur module de décompte de charges.
- `scripts/seed_demo_data.py` : réécrit pour générer un schéma fidèle à la
  réalité (actual/total historisés sur 60 jours + totalDay/Week/Month/Year
  dérivés en valeur unique "live", 3 appartements + zone bâtiment) --
  nécessaire pour tester le nouveau dashboard sans dépendre d'un vrai
  Miniserver.

**Validé avant livraison** : tests unitaires (classification étendue,
extraction d'unité par state, `query_latest`/`query_daily_last`), puis test
de bout en bout avec un vrai navigateur headless (Playwright) contre
`config.demo.yaml` -- 3 onglets, sélection de zone/ressource, tuiles KPI,
graphs Chart.js peuplés de données plausibles, AUCUNE exception JS, et
régression vérifiée sur l'onglet Explorer d'origine. Pas encore vu par
l'utilisateur sur le vrai dashboard (MS-Arlopi) -- nécessite un redémarrage
de `app.py` pour charger le nouveau code Python (les fichiers
statiques/templates seuls ne suffisent pas).

## Frontend modulaire (2026-08-26)

`static/js/dashboard.js` + `energy.js` (652 lignes à eux deux, un seul gros
IIFE chacun, pas mal de duplication -- palettes de couleurs, formatage de
dates, options Chart.js répétées entre les 3 onglets) ont été découpés en
modules ES natifs (`<script type="module">`, pas de bundler) -- choix fait
à l'origine aussi pour éviter une étape de build sur le Pi, mais **la cible
matérielle a changé depuis (PC Ubuntu Server, voir en tête de fichier)** :
la raison qui tient encore est la simplicité de maintenance pour un projet
Python solo (pas de node_modules/npm à gérer en plus de la stack Python),
pas une contrainte de ressources. Un framework frontend avec build step
(ex: Vue) redevient une option à évaluer si le besoin d'interactivité
grandit -- voir discussion dans la conversation Claude du 2026-08-26 :

```
static/js/
  main.js                 point d'entrée, câble les onglets
  tabs.js                 bascule générique entre onglets (setupTabs)
  core/
    api.js                fetch + cache mémoire de /api/series
    format.js              fmtNumber/fmtDate.../zoneLabel/resourceLabel
    charts.js              palettes, options Chart.js de base, kpiTile, aggregateMonthly
    health.js              bandeau de statut (bas de page)
    config.js               lecture de window.RESOURCE_TYPE_LABELS
  tabs/
    explorer-tab.js        onglet "Explorer" (sélection libre multi-capteurs)
    energy-tab.js           onglet "Énergie" (réseau vs solaire)
    zone-tab.js              onglet "Consommations par zone" (générique)
```

Règle pour ajouter un onglet : un bouton + un panel dans `templates/index.html`,
un module sous `tabs/` exportant `initXxxTab()`, une entrée dans la map
passée à `setupTabs()` dans `main.js`. Rien à changer dans `tabs.js` (il ne
connaît que `.tab-btn`/`.tab-panel`, pas le contenu des onglets).

Validé avant livraison (cloud sandbox, `config.demo.yaml`) : test headless
Playwright sur les 3 onglets (KPI, graphs Chart.js peuplés, changement de
plage/zone/ressource, sélection/désélection de capteurs sur l'Explorer),
`node --check` sur les 9 modules, aucune erreur JS console, aucune référence
résiduelle aux anciens fichiers. `dashboard.js`/`energy.js` supprimés.
`templates/base.html` et `templates/index.html` mis à jour (chargent
`main.js` en module).

**Rappel git en suspens** (déjà signalé, pas encore traité par
l'utilisateur) : `app.py` et `loxone_client.py` ont encore des hunks non
committés (mélange websocket / backfill / dashboard énergie, laissés de
côté lors du commit par blocs), et `CLAUDE.md` n'est toujours pas suivi par
git (untracked). Non touché par ce refactor frontend -- voir le plan de
commit détaillé donné précédemment dans la conversation.

## Sidebar de sélection des capteurs (2026-08-26)

Suite au refactor JS modulaire, la sidebar (colonne de gauche, onglet
Explorer) a été refaite pour éliminer une vraie duplication : elle était
la SEULE partie de la page encore rendue côté serveur (Jinja,
`build_apartment_groups`/`build_room_groups` dans `app.py`, macro
`sensor_li` dans `templates/index.html`) alors que les onglets Énergie/
Zone font ce même genre de regroupement côté client depuis `/api/series`.
Deux implémentations de la même logique de groupement à maintenir.

**Changements** :
- `static/js/sidebar.js` (nouveau) -- construit l'arbre appartement>type
  ou pièce à partir de `loadAllSeries()` (le même cache que les autres
  onglets), réutilise `resourceLabel()`/`getResourceTypeLabels()` déjà
  existants. Ne connaît rien de la sélection en cours : expose
  `initSidebar({isSelected, onSelectionChange})` et
  `clearAllCheckboxes()`, l'état de sélection reste dans
  `explorer-tab.js`.
- `app.py` : `build_apartment_groups()`/`build_room_groups()` supprimées,
  `index()` simplifiée (ne fait plus que rendre le squelette de la page).
  `_apartment_sort_key()` gardée (encore utilisée par `/admin` pour
  l'autocomplete des appartements connus) -- **bug de refactor attrapé
  avant livraison** : une première passe l'avait supprimée sans voir
  qu'elle était aussi utilisée par la route `/admin`, pas seulement par
  `index()` ; détecté par `grep` avant de synchroniser sur le Mac.
- `templates/index.html` : macro `sensor_li` et les boucles Jinja de
  regroupement supprimées, remplacées par un conteneur vide
  (`#sidebar-tree`) peuplé par `sidebar.js`. Le lien `?group_by=room`
  devient un bouton toggle client-side (`#sidebar-group-toggle`).
- **Bonus obtenu "gratuitement" par le passage au rendu client** : la
  sélection de capteurs ne se perd plus quand on bascule
  appartement/pièce (avant, ce lien rechargeait toute la page = sélection
  perdue). `sidebar.js` interroge `isSelected()` à chaque rendu pour
  recocher les cases.

Validé avant livraison (cloud sandbox, `config.demo.yaml` + Playwright) :
regroupement par appartement puis par pièce, sélection de 2 capteurs +
vérification que le graph a bien 2 datasets, sélection préservée après
bascule de vue, bouton "Tout désélectionner", ET la page `/admin` (pour
attraper la régression `_apartment_sort_key` ci-dessus) -- autocomplete
des appartements toujours correcte (App1/App2/App3 triés numériquement).
Aucune erreur JS, `node --check` sur tous les modules.

## Page de décompte de charges (/decompte) — 2026-08-28

Première brique du module de facturation : **visualiser** les données qui
serviront aux factures. Décompte **MENSUEL**, limité à consommation /
production / réseau. Le mensuel a remplacé un découpage bimestriel essayé
d'abord : il isole le mois de pose des compteurs (octobre 2025, incomplet)
au lieu de perdre tout un bimestre.

### Ce que mesure quoi — ÉTABLI EMPIRIQUEMENT, ne pas re-supposer

Une première version de cette page a traité l'installation comme si elle
était en panne. **C'était une erreur d'interprétation de ma part, pas une
anomalie de compteur** (confirmé par l'installateur, puis vérifié dans les
données). Ce qui suit est mesuré, pas déduit :

- `<Zone> Grid (total)` = énergie **achetée au réseau** par la zone.
  Vérifié : identique à la sortie `Gpwr` du bloc EFM de la zone (99-100 %
  des échantillons bruts) et jamais négative -> import pur, pas un compteur
  bidirectionnel.
- `<Zone> Solaire (total)` = solaire **autoconsommé** par la zone.
  Vérifié deux fois : (a) identique à la sortie `Ppwr` de l'EFM, et (b) son
  cumul est identique, à 0,01 kWh près sur les incréments, à la sortie
  `selfConsumption` du même bloc EFM -- c'est donc Loxone lui-même qui
  qualifie cette série d'autoconsommation. Et la somme des six zones
  (28,78 kWh sur la fenêtre de données brutes) égale le `selfConsumption` du
  bloc EFM du BÂTIMENT (28,79 kWh).
- **=> consommation facturable d'une zone = Grid + Solaire**, scindée en
  deux prix. C'est exactement le modèle RCP que déploient les prestataires
  du marché (Climkit : « calcule toutes les 15 minutes la part solaire et la
  part réseau de chaque consommateur »).
- `Appartement N / Commerce / Rez jardin (total)` = compteur **plus ancien**
  (UUID `1eb6...`, génération antérieure aux compteurs EFM `1f90...` posés
  en octobre 2025), de **périmètre différent** : en août 2026 il enregistre
  6,3 kWh/j sur App 1 quand le seul compteur Grid en enregistre 7,0. Ce
  n'est PAS une seconde mesure de la même chose. Gardé en contrôle,
  **jamais utilisé pour facturer**.
- `Réseau (total/totalNeg)` du bâtiment = compteur au raccordement de
  l'onduleur (même UUID de contrôle que `Production` et `Batterie`), pas sur
  l'alimentation des zones : sur la même fenêtre il enregistre 3,35 kWh
  d'import quand la somme des Grid des zones en enregistre 85,8. **Non
  comparable, donc non utilisé** -- l'injection se déduit de
  `production - autoconsommation`.

Les états EFM (`Gpwr`/`Ppwr`/`Spwr`/`selfConsumption`) n'ont **pas**
d'historique Statistics (2 jours de live seulement) : ils servent à
comprendre la topologie, pas à facturer.

### Les deux taux, à ne jamais confondre

Erreur déjà commise une fois, signalée par l'utilisateur ("l'affichage
d'autoconsommation me paraît inversé") :

- **Taux d'autoproduction** = solaire autoconsommé / consommation. Monte en
  été (1,1 % en fév. 2026, 23,3 % en juil.). C'est l'indicateur mis en avant.
- **Taux d'autoconsommation** = solaire autoconsommé / production. **Baisse**
  en été (83,5 % en fév., 28,8 % en juil.) -- correct mais contre-intuitif :
  à n'afficher qu'avec son explication, jamais seul.

Le graph « Les deux taux d'autonomie » les superpose exprès, pour rendre le
croisement saisonnier lisible. `tests/test_billing.py` fige la distinction.

### Résultat sur les vraies données

**Novembre 2025 -> juillet 2026 : 9 mois, 6 zones sur 6 facturables.**
Seuls octobre 2025 (pose des compteurs) et le mois en cours ne le sont pas.

### Ce qui a été livré

- `billing.py` (nouveau) -- périodes mensuelles en heure **locale
  Europe/Zurich** (et non UTC comme `db.query_daily_last` : sur une facture,
  un mois commence à minuit chez le propriétaire) ; résolution "quelle série
  alimente quelle colonne" ; consommation = relevé de fin - relevé de début
  (jamais une somme de deltas) ; détection des ruptures de compteur ; les
  deux taux ; tarifs et montants.
- `db.py` -- table `tarifs` (dans `SCHEMA`, donc créée automatiquement sur
  une base existante, aucun ALTER nécessaire) + `query_value_at()` (dernier
  relevé à une date donnée, requête indexée) + CRUD tarifs.
- `app.py` -- routes `GET /decompte`, `GET /api/decompte[?from=&to=]`,
  `GET|POST /api/tarifs`, `DELETE /api/tarifs/<id>`.
- `templates/decompte.html`, `static/js/decompte/{main,api,format,table,
  charts,tarifs}.js`, CSS -- page autonome (pas un onglet), sélecteur de
  mois + sélecteur de plage pour les graphs, tuiles KPI, tableau par zone
  (réseau / solaire / consommation / autoproduction / HT / TVA / TTC /
  état), 4 graphs (consommation mensuelle, répartition par zone, devenir de
  la production solaire, les deux taux), panneau tarifs, et un dépliant
  "compteurs de contrôle + correspondance des séries".
- `scripts/seed_demo_data.py` -- compteur de contrôle par zone, et surtout
  la MÊME sémantique que le réel : "Solaire" d'une zone = autoconsommé
  (donc conso = grid + solaire exactement), "Solaire" du bâtiment =
  production totale, PV des appartements compris.
- `tests/test_billing.py` -- 27 tests.

### Décisions prises AVEC l'utilisateur (ne pas les redéfaire seul)

- Découpage **mensuel** (le bimestriel a été abandonné).
- Facturation sur **Grid + Solaire**, le compteur `Appartement N` en
  contrôle uniquement.
- Tarifs stockés **en base** avec une date de prise d'effet : un mois déjà
  facturé reste reproductible à l'identique après un changement de prix.
- Structure tarifaire : prix kWh réseau + prix kWh solaire + TVA. **Pas
  d'abonnement fixe** (présent dans `docs/notes.md` mais écarté à ce stade).
- Bornes de période en Europe/Zurich.
- Les Communs restent une **ligne séparée**, non répartie.
- Taux de TVA pré-rempli à 8,1 % mais éditable -- pas confirmé par
  l'utilisateur.

### Reste à faire

- Génération de la facture elle-même (PDF / impression) par zone et par mois.
- Décider du sort de la charge non sous-comptée au niveau de l'immeuble.
- Compteurs `Chauffage App 2` (4167) et `Chauffage App 3` (2896) figés
  depuis 10 mois -- hors périmètre du décompte électrique actuel, mais à
  signaler si le chauffage doit être facturé un jour.

## Prochaine étape prévue

Module de génération de factures / décomptes de charges par appartement,
côté MCP-Loxone. Point d'entrée naturel : `/api/series/<id>/data` (agrégats
horaires disponibles sur le long terme) combiné aux champs `apartment` /
`resource_type` de `series_meta`, pour calculer une consommation par
appartement et par type de charge sur une période de facturation.

## Commandes utiles

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp config.example.yaml config.yaml && cp .env.example .env   # puis éditer

# Diagnostic connexion Loxone
python3 scripts/diagnose_auth.py config.yaml

# Diagnostic websocket (lecture live à distance)
python3 scripts/diagnose_websocket.py config.external.yaml MS-Arlopi 8

# Diagnostic historique Statistics (SD card Loxone)
python3 scripts/check_statistics.py config.yaml maison

# Backfill de l'historique Statistics (dry-run d'abord, puis sans --dry-run)
python3 scripts/backfill_statistics.py config.external.yaml MS-Arlopi --dry-run
python3 scripts/backfill_statistics.py config.external.yaml MS-Arlopi

# Lancer (prod ou config alternative)
python3 app.py                     # config.yaml
python3 app.py config.demo.yaml    # dashboard de démo, données synthétiques

# Démo / test dashboard sans Loxone réel
python3 scripts/seed_demo_data.py config.demo.yaml && python3 app.py config.demo.yaml

# Maintenance DB (mensuel, manuel)
python3 scripts/vacuum_db.py config.yaml

# Déploiement (systemd, PC Ubuntu Server -- anciennement Pi)
sudo cp scripts/loxone-collector.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now loxone-collector
journalctl -u loxone-collector -f
```
