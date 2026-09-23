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

## Séparation multi-site (miniserver comme regroupement racine) — 2026-09-02

Un deuxième miniserver ("MS-PPE-Horizon") a été ajouté à côté de
"MS-Arlopi" dans `config.external.yaml` -- deux immeubles distincts suivis
par le même collecteur. `series_meta.miniserver` existait déjà, mais
partout où le code groupait par `apartment` seul (sidebar, onglets
Énergie/Consommations par zone, `billing.py`, `scripts/export_mesures_xlsx.py`),
deux sites ayant chacun un "App 1" auraient mélangé leurs données sous une
même entrée -- un vrai bug de facturation, pas seulement d'affichage,
corrigé avant tout dégât réel (aucun des deux sites n'avait encore de tarif
enregistré en base au moment du correctif).

**Décisions prises avec l'utilisateur** :
- Le site (miniserver) est le PREMIER niveau de regroupement partout,
  au-dessus d'appartement/pièce -- c'est la séparation "lieu physique"
  demandée explicitement.
- `/decompte` gagne un sélecteur de site qui scope TOUTE la page (pas de
  vue "tous sites mélangés") -- persiste le choix en `localStorage`.
- Tarifs (`db.tarifs`) scopés par site (colonne `miniserver`, contrainte
  `UNIQUE(miniserver, valid_from)`) -- deux sites peuvent avoir des prix
  différents et un tarif prenant effet à la même date.
- Libellé de site affiché = nom brut du miniserver (`config.yaml`), pas de
  libellé personnalisé ajouté à la config.

**Changements** :
- `db.py` : `tarifs` gagne la colonne `miniserver` ; migration dans
  `_migrate_schema` (rename+recreate+copy -- SQLite n'a pas d'ALTER TABLE
  pour changer une contrainte UNIQUE, seule exception documentée à la règle
  "jamais de DROP/recreate" du fichier -- data-preserving, testée sur une
  base migrée à la volée). `list_series` trié par `miniserver, room, label`
  (au lieu de `room, label`), pour que l'admin affiche les sites groupés.
- `app.py` : `GET /api/miniservers` (sites configurés) ; `/api/decompte` et
  `/api/tarifs` (GET/POST/DELETE) prennent un paramètre `miniserver`,
  validé contre la config (`_resolve_miniserver`), et filtrent les séries
  AVANT d'appeler `billing.*` -- `billing.py` lui-même n'a pas eu besoin de
  changer (il reste "un site à la fois" par construction, donc aucune
  logique de facturation modifiée).
- `static/js/sidebar.js` : niveau `.site-group` ajouté au-dessus
  d'appartement/pièce, affiché seulement s'il y a plus d'un site dans les
  données chargées (une seule installation garde l'arbre à plat, pas de
  nesting inutile).
- `static/js/core/format.js` : `zoneKey`/`parseZoneKey`/`matchesZone` --
  clé composite (site, appartement) utilisée comme valeur d'`<option>`
  dans les sélecteurs de zone (avant : `apartment` seul, collision
  possible entre sites).
- `static/js/tabs/energy-tab.js` / `zone-tab.js` : sélecteur de zone
  groupé par site via `<optgroup>`.
- `static/js/decompte/*.js` + `templates/decompte.html` : sélecteur de
  site (`#site-select`), caché automatiquement s'il n'y a qu'un seul site
  configuré. `tarifs.js` : `initTarifs()` ne se lie qu'une fois (sinon un
  changement de site empilerait des listeners "submit" en double) --
  expose `setTarifs(tarifs, miniserver)`, rappelé par `main.js` à chaque
  changement de site.
- `scripts/export_mesures_xlsx.py` : nouvel argument `--miniserver`
  (requis dès que la config en liste plusieurs) -- le script mélangeait
  auparavant tous les sites de la config sous un titre fixe "Immeuble
  Arlopi", ce qui aurait cassé l'export de "Mesure PPE Horizon T2 2026.xlsx"
  que l'utilisateur s'apprêtait à générer.

**Validé avant livraison** : suite de tests (`pytest tests/`, 38 tests,
aucune régression -- `billing.py` inchangé). Vérification bout-en-bout par
appels HTTP réels (pas de navigateur Playwright disponible dans CETTE
session, contrairement aux validations précédentes en sandbox cloud -- à
refaire visuellement si possible) : base de démo dupliquée sous un second
miniserver avec les MÊMES codes d'appartement (APP1/APP2/APP3, le scénario
de collision exact), serveur Flask lancé dessus, confirmé que
`/api/series`, `/api/decompte?miniserver=...` et `/api/tarifs` restent bien
isolés par site (y compris deux tarifs à la même `valid_from` sur deux
sites différents), et que `/`, `/admin`, `/decompte` rendent sans erreur
avec les nouveaux éléments (`#site-select`, arbre sidebar). **Pas encore vu
par l'utilisateur dans un vrai navigateur** -- nécessite un redémarrage de
`app.py` pour charger le nouveau code Python.

## Classification MS-PPE-Horizon (naming différent d'Arlopi) — 2026-09-02

Après le passage à 2 sites (voir section précédente), les points de
MS-PPE-Horizon se sont révélés nommés différemment de MS-Arlopi :
"Compteur <nom de la zone> <numéro>" (ex: "Compteur Appartement 1",
"Compteur Bureau 17"), et la topologie de mesure est différente aussi --
établi avec l'utilisateur, pas supposé :

- **Pas de split réseau/solaire par zone à PPE-Horizon** : contrairement à
  Arlopi (un bloc EFM Grid+Solaire PAR zone), PPE-Horizon n'a qu'un seul
  `Compteur Appartement N` par lot -- **consommation totale non scindée**
  (confirmé par l'utilisateur), plus un EFM et des compteurs PV au niveau
  du BÂTIMENT seulement, pas par zone. `resource_type="energie_consommee"`
  est donc la classification CORRECTE pour ces compteurs, pas une
  détection à corriger.
- **Décision prise avec l'utilisateur** : l'onglet "Énergie" reste dédié à
  la comparaison réseau/solaire (qui n'existe que pour Arlopi) -- pas
  adapté pour afficher une "consommation totale" seule. L'onglet
  "Consommations par zone" (déjà générique, ne suppose aucun split) reste
  le bon endroit pour PPE-Horizon -- vérifié fonctionnel sur les données
  réelles (17 zones listées correctement) sans changement de code.
- **`classification.DEFAULT_APARTMENT_PATTERN`** étendu pour reconnaître
  "Bureau <n>" comme zone (lot non résidentiel numéroté, même principe que
  "Commerce" à Arlopi) -- sans ça, "Compteur Bureau 17" atterrissait dans
  "Sans appartement".
- **Bug trouvé en testant ce changement** (pas visible avant, parce
  qu'aucun nom de zone existant ne contenait "eau" comme sous-chaîne) :
  la règle de repli générique `(eau|water)` de
  `DEFAULT_RESOURCE_TYPE_RULES` n'avait pas de frontière de mot -- "eau"
  matchait comme sous-chaîne de "Bur**eau**", ce qui aurait classé
  silencieusement un compteur électrique en eau froide. Corrigé avec
  `\beau\b`/`\bwater\b`. Les autres règles de la liste n'ont pas ce
  problème réutilisent des mots plus longs/spécifiques (`eau chaude`,
  `réseau`...), gardées telles quelles faute de cas réel prouvant un souci.
- Un seul `apartment_pattern`/`resource_type_rules` reste appliqué à TOUS
  les miniservers (pas de config de classification par site) -- suffisant
  tant que les conventions de nommage des sites restent compatibles entre
  elles (aucun conflit de mot-clé rencontré à ce jour) ; à revisiter si un
  futur site utilise un vocabulaire qui entre en collision.

Pas de changement nécessaire à `db`/`app.py`/`billing.py` pour cette
partie -- uniquement `classification.py` (règles) et `billing.py::_zone_label`
(affichage "Bureau 17" au lieu de "Bureau17" bruts dans `/decompte`, même
traitement que "App N"). La reclassification des séries déjà en base est
automatique au prochain cycle de poll après redémarrage de `app.py` (pas
de migration manuelle : `apartment_manual=0` sur ces séries, donc le
poller réécrit `apartment`/`resource_type` normalement).

## Topologie MS-PPE-Sequoia + répartition solaire recalculée — 2026-09-09

Troisième site ajouté à `config.external.yaml` (21 lots : App 01 à 43,
répartis en étages). L'accès a d'abord échoué en 401 : `config.external.yaml`
référençait `${LOXONE_PROD_USER}`/`${LOXONE_PROD_PASSWORD}` au lieu des
variables spécifiques `${LOXONE_PROD_SEQUOIA_*}` (les bonnes valeurs étaient
bien dans `.env`). Rappel : `app.py` ne lit la config qu'au démarrage, donc
un correctif de config demande un redémarrage.

### Les séries `App XX Grid`/`App XX Solaire` sont INEXPLOITABLES ici

À ne pas re-supposer : ce sont les homonymes des séries de facturation
d'Arlopi, mais **elles ne mesurent pas ce que leur nom indique**. Établi sur
janvier-mai 2026 :

- sur **881 heures où le bâtiment n'a rien importé du réseau**, les 881
  voient malgré tout les compteurs "Grid" des lots augmenter -- **4 999 kWh
  d'achat au réseau physiquement impossible** ;
- **575 heures** de production > 1 kWh ont **tous** les "Solaire" de lot à
  zéro (le 21 mai à 14h : 27,58 kWh produits, part solaire de chaque lot à
  0,000) ;
- l'excédent est **synchronisé sur les 21 lots** (rapport Grid / compteur du
  lot entre 7,7 et 10,0 sur la même heure) : une répartition calculée, pas
  une mesure ;
- la somme des Grid+Solaire de toutes les zones (36 000 kWh) correspond à
  l'énergie **entrée** dans le bâtiment (15 681 importés + 21 679 produits),
  pas à l'énergie consommée (27 435) : les 9 926 kWh **réinjectés** sont
  redistribués aux lots comme s'ils avaient été consommés.

Les catégories Loxone le laissaient deviner : ces séries sont en catégorie
"Répartition Solaire" (UUID frères `1fc0ea6f-0364-5130`/`…-5140`), alors que
le compteur du lot est en catégorie "Energie" avec un UUID indépendant.
Le bloc de répartition de Sequoia est donc **mal configuré** -- à signaler à
l'installateur ; en l'état aucune facturation ne peut s'appuyer sur ses
sorties.

### Ce qui EST fiable à Sequoia

- **`Appartement N` = la consommation électrique réelle du lot.** La nuit,
  quand le bloc de répartition n'a rien à distribuer, il concorde à 1-3 %
  près avec Grid+Solaire sur tous les lots testés (ratio 1,004 à 1,026 sur
  ~1 330 heures chacun). C'est le compteur de facturation. Il était appelé
  "compteur de contrôle" dans une première version de l'export, par
  analogie avec Arlopi -- **le rôle est inversé entre les deux sites**.
- **Les compteurs de bâtiment** (`Réseau Général Oiken` total/totalNeg,
  `Production Solaire`) sont posés sur l'alimentation réelle, contrairement
  à ceux d'Arlopi : le bilan `import + production - injection` boucle à
  **-0,64 %** contre la somme des compteurs de consommation (21 lots +
  `Communs` + `Salles Communes`). C'est ce bouclage qui autorise le calcul.
- `PAC déjà mesuré` (8 101 kWh) est **inclus dans `Communs`** -- son nom le
  dit, et l'ajouter fait sauter le bouclage. Idem `Boiler ecs mesuré aussi ?`.
- Pas de batterie active sur la période (série `Batterie` vide).
- Eau et chauffage : compteurs dédiés, monotones, sans rapport avec le bloc
  de répartition -- jamais concernés par ce problème.

### `repartition.py` (nouveau) — le modèle RCP, reconstruit

Pour chaque heure : `part_solaire = (production - injection) / (import +
production - injection)`, appliquée à la consommation mesurée du lot. C'est
le modèle des prestataires du marché (Climkit). Deux propriétés à connaître :

- **`réseau + solaire = consommation mesurée`, exactement.** Le total
  facturé reste le relevé de compteur (via `billing._reading_delta`) ; seule
  la PROPORTION vient de l'agrégation horaire. Les valeurs sont arrondies à
  la précision affichée avant de déduire la part réseau, sinon le classeur
  affiche `11,76 + 2,34 = 14,10` face à un total de `14,09`.
- au niveau du bâtiment, la conservation n'est vraie qu'à l'erreur de
  bouclage près (-0,64 %) : le dénominateur vient des compteurs de bâtiment,
  pas de la somme des zones.

**Le garde-fou décide seul quelle source utiliser** (`evaluer_site`), et
l'ordre des tests compte -- une première version accusait Arlopi de 1 064
heures "impossibles" alors que ses séries sont valides :

1. `qualite_bilan` d'abord. Sans bouclage, le compteur "réseau" du bâtiment
   ne mesure pas l'alimentation (Arlopi : posé à l'onduleur, **+138,7 %**
   d'écart) -- l'audit se déclare alors **non testable** au lieu d'accuser à
   tort, et aucune répartition ne peut être recalculée non plus.
2. `audit_series_loxone` ensuite, seulement si le bilan boucle (Sequoia :
   -6,3 %). S'il condamne les séries -> répartition recalculée.
3. Sinon on garde les séries du Miniserver, avec `confiance_verifiee=False`
   quand le bilan n'a pas permis de les revérifier (cas Arlopi, validé par
   ailleurs contre les sorties du bloc EFM).

Seuil : `BILAN_TOLERANCE_PCT = 15` -- il sépare deux situations qui diffèrent
d'un ordre de grandeur (-6 % contre +138 %), pas deux tolérances de compteur.

### Mécanisme exact de l'anomalie (établi 2026-09-09, pour l'installateur)

Le bloc n'est pas globalement faux : il a UN défaut. À ne pas re-diagnostiquer
plus largement qu'il ne l'est.

- **La nuit et les jours SANS réinjection, il est juste.** Total distribué
  contre consommation réelle du bâtiment : 0,98 la nuit (1 239 h), 1,00 le
  jour sans réinjection (1 182 h). Et sur les 661 heures les plus propres,
  la part solaire attribuée à chaque APPARTEMENT est correcte (45-64 % pour
  52,8 % attendus) : **la clé de répartition entre lots fonctionne.**
- **Le défaut : il répartit la production BRUTE au lieu de
  l'autoconsommation.** Sur les 1 133 h avec réinjection, il a distribué
  17 267 kWh quand l'immeuble n'en consommait que 8 468 (rapport 2,18), soit
  94 % de `import + production` (18 384). Les kWh réinjectés sont donc
  redistribués aux zones comme s'ils avaient été consommés, **et ils
  atterrissent sur la sortie « Grid »** (achat au réseau).
- **Correctif à demander** : l'énergie à répartir doit être
  `production - réinjection`. La mesure existe déjà : c'est le `totalNeg` du
  compteur de raccordement (`Réseau Général Oiken`).
- **Anomalie distincte, pas forcément un bug** : la ligne `Communs` reçoit
  73,8 % de solaire (pour 87,3 % attendus) quand la PAC est à l'arrêt, mais
  seulement 17,7 % (pour 44,9 %) quand elle tourne -- la consommation de la
  PAC est versée au réseau. Peut être volontaire (raccordement en amont du
  point d'injection, ou exclusion délibérée du partage). Enjeu chiffré sur
  l'App 35 : part solaire 49,7 % (PAC dans le partage, hypothèse retenue)
  contre 53,7 % (PAC hors partage), soit 2,69 kWh sur la part réseau -- le
  total consommé ne change pas.
- Nommage à faire corriger tant qu'on y est : 4 sorties sur 44 s'appellent
  `Sol` au lieu de `Solaire` (`Communs Sol`, `App 13/21/32 Sol`), ce qui
  suffit à les faire classer en `energie_consommee` au lieu de
  `energie_solaire`.

`scripts/audit_repartition.py` (nouveau) produit le dossier de preuve remis
à l'installateur -- `docs/Anomalie repartition solaire <site> <de> a <à>.xlsx`,
4 onglets (Constat, les 877 heures ligne à ligne avec index de début/fin,
une heure détaillée zone par zone, correspondance des UUID Loxone).

L'argument est construit pour ne dépendre d'AUCUN modèle de notre part :
on isole les **877 heures où l'index du compteur d'achat de l'immeuble est
identique en début et en fin d'heure** (donc zéro kWh acheté), et on montre
que les sorties « Grid » y totalisent **11 404,9 kWh**. Sur ces heures :
production 12 507,1, réinjection 8 413,7, donc 4 093,5 consommés -- et les
compteurs de consommation en mesurent 4 138,4 (**+1,1 %**, ce qui prouve au
passage que les compteurs, eux, sont bons). Total distribué / production
brute = 0,953.

Deux pièges rencontrés en écrivant ce script, à ne pas refaire :
- l'inventaire des consommateurs ne peut pas être `resolve_zones` (un seul
  compteur par zone -> `Salles Communes` perdu, bilan à -8,3 % au lieu de
  +1,1 %). `resolve_consommateurs()` prend tous les compteurs de la
  catégorie « Energie » sauf les doublons que l'installateur signale
  lui-même dans le nom (`DOUBLON_RE` : "déjà mesuré", "mesuré aussi",
  plus la buanderie `CEnergie`/`CNrMachine`) ;
- exiger que TOUS ces compteurs aient une donnée à chaque heure faisait
  tomber 3 519 heures exploitables à 154, à cause des `M1-M6 Zähler` et de
  la Wallbox qui n'existent que depuis le démarrage du collecteur. Filtre
  sur la COUVERTURE des données (>= 95 % de la période), pas sur les noms.

### Correctif dans `billing.py` (touche aussi `/decompte`)

`CONTROLE_EXCLUDE_RE` excluait "chauffage" mais pas "chaleur" : le compteur
de contrôle d'un lot de Sequoia était son compteur de **chaleur**
("APP 35 compteur chaleur kWh [50]" passe avant "Appartement 35" dans
l'ordre alphabétique de `_pick`, majuscules avant minuscules) -- des kWh
thermiques dans une colonne électrique. Ajouté : `c[ch]aleur` (la coquille
"Ccaleur" de l'App 34 est réelle, côté Loxone Config), `cenergie` et
`nrmachine` (catégorie Loxone "Lessive" = part de buanderie commune du lot,
déjà comprise dans le compteur des communs -- l'additionner
double-compterait).

### Livré et validé

`repartition.py`, `tests/test_repartition.py` (20 tests, suite à 58, aucune
régression), `scripts/export_appartement.py` branché dessus,
`billing.py::CONTROLE_EXCLUDE_RE`. Vérifié sur les données réelles : App 35
sur janvier-mai 2026 = **66,84 kWh** (33,60 réseau + 33,24 solaire), avec une
part solaire mensuelle de 16,6 % -> 75,8 % qui suit la courbe du bâtiment
(19,0 % -> 72,9 %), légèrement au-dessus en avril-mai (consommation diurne).
Non-régression confirmée sur Arlopi (App 1 : mêmes séries qu'avant).
Le classeur `docs/Consommations App 35 2026-01 a 2026-05.xlsx` explique la
méthode au client dans son onglet "Lisez-moi", et les lignes calculées sont
marquées comme telles dans le CSV.

**Pas encore vu par l'utilisateur dans un navigateur** : `/decompte` continue
d'utiliser les séries du Miniserver sans passer par `repartition.py` -- pour
Sequoia, la page affichera donc encore la répartition fausse. Brancher
`evaluer_site` dans `app.py`/`billing.py` est la suite logique.

## Décompte consolidé tous appartements (`--tous`) — 2026-09-10

L'installateur a corrigé la répartition solaire côté Loxone Config. **Ça ne
change rien à l'historique** : les valeurs déjà enregistrées par la
fonction Statistics restent celles que le bloc sortait à l'époque, et
l'audit voit toujours l'anomalie jusqu'au 9 septembre inclus (63 heures,
945 kWh impossibles sur les 9 premiers jours de septembre). Tout décompte
portant sur une période passée doit donc continuer à passer par
`repartition.py`. Ce sera vérifiable dans quelques jours de données neuves.

`scripts/export_appartement.py` gagne un mode multi-zones :

```bash
# décompte consolidé, un seul fichier, prêt à facturer
python3 scripts/export_appartement.py config.external.yaml \
  --miniserver MS-PPE-Sequoia --tous --recap-seul \
  --from-mois 2026-01 --to-mois 2026-08
# + un classeur détaillé par appartement : enlever --recap-seul
```

Sorties : `docs/Decompte <site> <de> a <à>.xlsx` (3 onglets) + le CSV plat.
L'onglet « Décompte » a une ligne par appartement, une colonne par poste, et
un bloc de **prix unitaires modifiables** (cases jaunes) que des formules
Excel appliquent aux quantités -> montants HT / TVA / TTC calculés dans le
classeur, sans repasser par le script. Volontairement sans détail horaire ;
le détail mensuel est dans le 2e onglet. `--tva` change le taux pré-rempli.

### Trois bugs trouvés en généralisant à toutes les zones

Ils étaient invisibles tant que le script ne tournait que sur un
appartement d'Arlopi ou l'App 35 de Sequoia.

- **Collision de clés = chauffage commun sous-compté.** `resolve_compteurs`
  utilisait le nom de la RÈGLE comme clé (`chauffage_energie`), donc les
  trois compteurs de chaleur des communs de Sequoia écrasaient leur entrée
  dans `data` : le classeur affichait 484,00 kWh trois fois et n'en comptait
  qu'un, au lieu de 1 228,25 + 1 783,00 + 484,00 = **3 495,25 kWh**. La clé
  est désormais `<rôle>:<series_id>`, et le champ `role` (nouveau) porte la
  logique métier (`grid`, `solaire`, `eau_chaude`...). Le commentaire du
  code évoquait déjà « deux compteurs d'eau froide sur un lot » sans voir
  qu'ils auraient collisionné.
- **Ordre des étapes.** Écarter les compteurs de contrôle inexploitables
  AVANT `appliquer_repartition_calculee` supprimait le compteur du lot
  lui-même dès qu'il était muet sur un mois de la plage (décembre 2025), et
  la répartition devenait impossible sur les 21 lots. Le tri passe après la
  promotion du compteur en poste facturable.
- **Zone fantôme APP100.** Née du nommage des points de buanderie
  (`CEnergieApp100`, `Log Machine App 100`), pas un logement. Écartée
  automatiquement : plus aucun compteur exploitable après filtrage.

Aussi : les sorties du bloc « Répartition Solaire » (`Communs Sol`) ne sont
plus affichées comme compteurs de contrôle -- ce sont des valeurs calculées,
justement celles dont l'incohérence motive `repartition.py`.

### Période exploitable à Sequoia

L'historique Statistics ne commence pas à la même date selon le type :
électricité et sorties du bloc au **01.12.2025**, mais eau au **28.12.2025**
et chaleur au **29.12.2025**. Le premier mois complet pour TOUS les types
est donc **janvier 2026**. Un décompte lancé depuis décembre 2025 sort une
alerte « aucune donnée exploitable pour Décembre 2025 » sur l'eau et le
chauffage de chaque lot -- ce n'est pas un bug.

### Les « pertes » : signature systématique, pas un compteur en panne

Écart de bouclage mois par mois (compteurs de bâtiment contre somme des
compteurs de zone), décembre 2025 -> septembre 2026 :
**-5,8 / -5,8 / -6,2 / -6,2 / -7,4 / -7,4 / -7,8 / -8,4 / -8,4 / -8,0 %**.

Une dérive aussi faible et aussi stable d'une saison à l'autre oriente vers
une cause systématique (consommateur non compté proportionnel à la charge,
ou facteur d'échelle sur un compteur), pas vers un compteur défaillant qui
donnerait des écarts erratiques. À croiser avec la vérification physique
prévue par l'utilisateur.

**RÉSOLU le 2026-09-10 : les « pertes » étaient `Salles Communes`.** Ce
consommateur réel (catégorie « Energie », 1 602,9 kWh sur janvier-mai) était
rattaché à la zone COMMUN mais facturé nulle part, parce que
`billing.resolve_zones` ne retient qu'UN compteur de contrôle par zone. En
l'ajoutant, le bouclage passe de **-6,5 % à -0,6 %** sur janvier-mai : il ne
reste que 175 kWh sur 27 441, soit de la tolérance de compteur ordinaire.
**Aucune vérification physique des compteurs n'est nécessaire** -- c'était
une omission de comptage, pas un problème d'installation.

### `Salles Communes` érigée en zone facturable — décisions de l'utilisateur

Prises explicitement avec lui, **ne pas les redéfaire seul** :

- `Salles Communes` est une **ligne à part** du décompte, pas une fusion
  dans « Communs » (réparti réseau/solaire comme n'importe quelle zone).
- Le compteur de chaleur **de la salle de réunion suit** (1 229 kWh sur
  janvier-mai) ; ceux du **séjour (1 783 kWh) et de la cuisine (484 kWh)
  restent aux Communs**, malgré leur préfixe Loxone commun « Commun - ».
- `M1` à `M6 Zähler` (compteurs physiques des 6 machines, pièce Buanderie)
  restent **hors du décompte** (décision 2026-09-10, confirmée 2026-09-18 :
  seules les kWh `CEnergieAppXX` par lot).

Mise en œuvre : pas de code spécial, une **reclassification en base** via
`db.set_series_classification(..., apartment="SALLESCOMMUNES")`, donc avec
`apartment_manual=1` -- le poller ne la réécrira jamais (règle stricte du
projet) et la correction est réversible depuis `/admin`. 21 séries
concernées : les 7 états du compteur électrique `Salles Communes` (UUID
`1fc21501-00df-8f54`) et les 14 du compteur de chaleur de la salle de
réunion (`1ff6ad9c-031e-d1d5` pour les kWh, `1ff6bfbf-038d-872c` pour le
volume -- deux sorties du même compteur physique). Seul ajout de code :
`SALLESCOMMUNES` dans `billing.ZONE_LABEL_OVERRIDES`.

Conséquence à connaître : la zone existe désormais partout (sidebar,
onglets Énergie / Consommations par zone, `/decompte`), pas seulement dans
l'export -- c'est voulu.

### Buanderie par badge (`CEnergieAppXX`) — 2026-09-18

Décision de l'utilisateur : colonnes **Buanderie (kWh)** et **Buanderie --
cycles** par appartement (`CEnergieAppXX` / `CNrMachineAppXX`). Pas les
`M1`-`M6`. Non déduit des Communs.

Note installateur « livré depuis le 17 juillet 2026 », vérifiée sur les
relevés : avant cette date CEnergie incrémentait tous les lots (~37 kWh)
avec 0 cycle (les cycles partaient sur APP100). À partir du 17.07, 0 cycle
= 0 kWh. Le décompte buanderie est donc borné à cette date
(`BUANDERIE_VALID_FROM` dans `export_appartement.py`).

Un décompte janvier-mai n'a pas ces colonnes. Juin d'un décompte juin-août
vaut 0. Lots jamais utilisés depuis le 17.07 : App 14, 25, 34, 35, 41, 42,
43.

### Wallbox (borne de recharge) — 2026-09-18

Décision de l'utilisateur : ligne à part du décompte, pas rattachée à un
lot (aucun badge NFC par appartement côté énergie). Mise en œuvre identique
à Salles Communes : `apartment="WALLBOX"` + `apartment_manual=1` sur les
12 états du compteur `Wallbox Energizähler` (UUID `204c74ad-02fe-30ba`,
pièce Garage, catégorie Energie), et `WALLBOX` dans
`billing.ZONE_LABEL_OVERRIDES`. Historique Statistics à partir du
04.03.2026 (`WALLBOX_VALID_FROM` dans `export_appartement.py`) : les mois
antérieurs sont à 0 kWh.

Deux compteurs kWh existent ; un seul est utilisé :

- **`Wallbox Energizähler (total)`** -- compteur d'énergie dédié, classé
  `energie_consommee`. C'est la série de facturation. Index à 0,000 kWh
  du 04.03.2026 au 09.09.2026 (aucune charge mesurée dans le sens
  consommation ; `actual` toujours à 0).
- `Wallbox 22kW 32A Tree (total)` -- compteur interne du bloc Loxone,
  mal classé `eau_froide`, également figé à 0. **Non utilisé** (doublon).
- `Wallbox Energizähler (totalNeg)` -- ~3,67 kWh de 03.2026 à 09.2026
  (~0,55 kWh/mois, sans pic de puissance). Ce n'est pas une session de
  recharge ; **non utilisé**.
- `NFC Code Touch Tree Wallbox` -- accès badge, pas d'énergie, pas
  d'historique d'utilisateur. Pas de répartition par appartement possible.

La répartition réseau/solaire de cette ligne passe par `repartition.py`
comme n'importe quelle zone (le Miniserver n'a pas de Grid/Solaire
Wallbox).

### Limite connue du garde-fou (non corrigée, hors périmètre demandé)

`bilan_exploitable` juge sur UN pourcentage agrégé sur toute la fenêtre, ce
qui peut masquer une variation saisonnière. Sur MS-Arlopi (compteur réseau
à l'onduleur) l'écart mensuel va de -25,7 % en novembre à +182,0 % en juin :
la moyenne tombe à +7,5 % sur janvier-mai (donc « exploitable », et le
site bascule à tort en répartition recalculée) mais à +32,1 % sur
janvier-août (« non exploitable », comportement attendu). Le verdict
dépend donc de la fenêtre demandée. Sequoia n'est pas concerné (-5,8 à
-8,4 %, stable). Correctif naturel si le sujet revient : exiger que la
majorité des sous-périodes bouclent, pas seulement la moyenne.

## Choix de stack figé : backend Flask/Python inchangé, frontend Vue 3 — 2026-09-23

Suite à `docs/analyse-stack-ts-collecte-frontend-excel.md` (2026-09-21,
recherche comparant TS/Node vs Python brique par brique). **Décisions
prises avec l'utilisateur, à ne pas rouvrir sans nouvel élément :**

- **Backend : Flask/Python reste l'unique backend, aucun framework backend
  ne remplace ni ne s'ajoute.** Ce n'était pas vraiment une question de
  "quel framework choisir" -- l'analyse conclut que les deux actifs qui
  coûteraient le plus cher à reconstruire (le client HTTP/Websocket Loxone
  `loxone_client.py`/`loxone_ws_client.py`, et le moteur de facturation
  `billing.py`/`repartition.py`, 58 tests, calibré sur 3 sites réels avec
  des anomalies découvertes empiriquement -- voir sections Sequoia/Horizon/
  Arlopi ci-dessus) doivent rester en Python. Flask garde exactement son
  rôle actuel de fournisseur d'API JSON (`/api/series`, `/api/decompte`,
  `/api/tarifs`, `/api/miniservers`, à étendre avec une route d'export
  Excel si besoin) -- **aucun changement de framework backend à faire.**
- **Frontend : Vue 3 + Vite + TypeScript + Tailwind CSS**, confirmé par
  l'utilisateur ("je vais choisir Vue3 et Tailwind car je connais" --
  courbe d'apprentissage nulle pour lui, l'un des deux critères demandés
  avec "simplifier le travail"). Tailwind ne figurait pas dans le
  comparatif du doc (qui ne traitait que le choix de framework JS) mais
  s'intègre sans friction à Vite/Vue -- aucune tension avec la
  recommandation.
- **Migration incrémentale, page par page**, pas de big-bang : `/decompte`
  (la page la plus récente, la plus autonome, "pas un onglet" -- voir plus
  haut) est le candidat naturel pour la première conversion. `/` (Explorer
  + onglets Énergie/zone) et `/admin` restent en JS vanilla (modules ES
  natifs, voir "Frontend modulaire" plus haut) jusqu'à ce que le besoin
  d'interactivité ou la duplication déjà notée (palettes, formatage de
  dates, options Chart.js) le justifie -- c'était déjà la position de
  `CLAUDE.md` avant ce doc, l'analyse ne fait que la confirmer avec un choix
  de framework concret.
- **Le frontend reste un pur consommateur de `/api/decompte`** -- jamais de
  logique de facturation/répartition recalculée côté client (Vue ou JS),
  même partiellement. Rappel volontaire : la distinction taux
  d'autoproduction/autoconsommation a déjà été inversée une fois par
  erreur (voir "/decompte" plus haut), et `evaluer_site()` (ordre bilan
  -> audit -> confiance) est une logique métier calibrée empiriquement,
  pas un calcul générique portable sans risque.
- **Export Excel : reste côté serveur, `openpyxl`.** Si le nouveau frontend
  Vue doit déclencher un téléchargement, ajouter une route Flask dédiée
  (ex: `GET /api/decompte/export.xlsx`) plutôt que régénérer les classeurs
  (formules HT/TVA/TTC vivantes, cellules de prix modifiables) côté
  navigateur.

**Pas encore fait à ce stade** : aucun scaffolding npm/Vite n'existe
encore dans le dépôt -- cette section fige le choix, l'implémentation
(structure de dossier, intégration dev/build avec Flask, migration de
`/decompte`) reste à faire dans une prochaine étape.

## Scaffolding + première implémentation Vue de `/decompte` — 2026-09-23

Scaffold Vite+Vue3+TS+Tailwind créé dans `frontend/` (`npm create vite@latest
frontend -- --template vue-ts`, `@tailwindcss/vite`, proxy dev `/api`+`/health`
-> Flask), commandes détaillées données à l'utilisateur pour qu'il les lance
lui-même dans son Terminal natif (pas via l'outil de la session).

Puis port complet de la page `/decompte` (JS vanilla `static/js/decompte/*`
+ `templates/decompte.html`) en composants Vue -- toute la logique
d'orchestration de `main.js` (renderPeriod/renderRange/renderAll) disparaît :
c'est la réactivité Vue (computed sur `payload`/`periodeKey`/`historiqueKey`)
qui la remplace, "bonus gratuit" déjà observé lors du refactor sidebar.

**Structure livrée** (`frontend/src/`) :
- `types/decompte.ts` -- types stricts du payload `/api/decompte` (Period,
  Zone, ZonePeriod, ReadingDelta, Montants, Batiment, Tarif), reconstruits
  depuis `billing.py::compute_decompte`/`_zone_period`/`_batiment_period`
  (source de vérité, jamais l'inverse).
- `api/http.ts` + `api/decompte.ts` + `api/health.ts` -- un seul point de
  fetch, même principe que `core/api.js`.
- `utils/format.ts` (fmtNumber/fmtKwh/fmtCHF/fmtPct/fmtDay/fmtPeriodBounds)
  et `utils/charts.ts` (palette + options Chart.js) -- ports directs de
  `decompte/format.js`+`core/format.js` et `core/charts.js`.
- `composables/useHealthFooter.ts` -- port de `core/health.js`.
- `components/decompte/*.vue` -- un composant par section : KpiTile, Card,
  StatusBadge, GlobalBanner, PeriodeKpis, ZoneTable, BatimentTable,
  ControleTable, SourcesTable, TarifsPanel, et les 4 graphs
  (Evolution/Zones/Solaire/Taux, via `vue-chartjs`).
- `views/DecomptePage.vue` -- orchestration (chargement des sites, sélection
  période/historique, logique de mois par défaut -- port fidèle de
  `loadSite()`).

**Décisions prises pendant l'implémentation** (à ne pas rouvrir sans
raison) :
- **Rendu en Tailwind pur, pas d'import de `static/css/style.css`.** La
  page legacy et la page Vue peuvent donc diverger visuellement pendant la
  transition -- acceptable, `/decompte` est déjà "une page autonome, pas un
  onglet". Pas de dark mode (l'app n'en a pas ailleurs).
- **Chart.js installé en dépendance npm** (`chart.js` + `vue-chartjs`,
  `Chart.register(...registerables)` dans `main.ts`), et non plus le bundle
  vendored `vendor/chart.umd.min.js` -- cohérent avec la philosophie "pas
  de dépendance internet au runtime" (npm résout au build, aucun appel
  réseau en prod) sans dupliquer le vendoring manuel.
- **Événements de tarifs = "changed" + re-fetch complet** (`TarifsPanel`
  n'a pas d'état interne, `DecomptePage` refetch decompte+tarifs sur
  `@changed`) plutôt que d'utiliser la liste retournée par
  `saveTarif`/`deleteTarif` directement (comme le faisait `tarifs.js`) --
  flux de données unidirectionnel plus simple, coût négligeable (2 requêtes
  au lieu d'1).
- **Proxy dev configurable** (`vite.config.ts` lit `VITE_API_PROXY_TARGET`,
  défaut `http://localhost:8082` = le port de `config.demo.yaml` ; `config.yaml`
  prod utilise le port 5000) -- à surcharger via `frontend/.env.local`
  (gitignored) plutôt que modifier `vite.config.ts`.

**Validé avant de rendre la main** : `vue-tsc -b` propre (aucune erreur de
type), `npm run build` réussi (bundle ~300 kB, ~105 kB gzip), et **données
réelles vérifiées de bout en bout** via le serveur Flask démo déjà lancé par
l'utilisateur (`config.demo.yaml`, port 8082) : `/api/miniservers`,
`/api/decompte?miniserver=demo` (3 zones, 3 mois) et `/api/tarifs` répondent
correctement à travers le proxy Vite (port 5174 -- 5173 déjà pris par une
autre instance).

**PAS validé visuellement** : l'extension Claude in Chrome n'est pas
connectée dans cette session (installation commencée puis abandonnée par
l'utilisateur) -- aucune vérification dans un vrai navigateur n'a été
possible. **À faire par l'utilisateur avant de considérer cette étape
terminée** : ouvrir `http://localhost:5174` (le serveur `npm run dev` tourne
déjà en arrière-plan) et comparer visuellement à `http://localhost:8082/decompte`
(page legacy, toujours intacte).

**Pas encore fait** (volontairement hors scope de cette étape) :
- Wiring production : comment `app.py`/`templates/decompte.html` serviront
  le résultat de `vite build` (fichiers hashés + manifest, ou noms fixes) --
  la page `/decompte` Flask actuelle n'a pas été touchée, elle reste la
  version servie en prod tant que ce câblage n'est pas fait.
- Aucun commit : `frontend/src/**` est untracked, à review et committer par
  l'utilisateur.

**Commité par l'utilisateur** (2026-09-23, avant la suite ci-dessous) :
3 commits séparés -- outillage frontend (chart.js/vue-chartjs, proxy dev
configurable), la page `/decompte` en Vue elle-même, puis CLAUDE.md.

## Câblage production (route de prévisualisation `/decompte-vue`) — 2026-09-23

Toujours pas de vérification visuelle possible dans cette session (l'
extension Claude in Chrome n'a pas été connectée -- voir plus haut) : au
lieu de basculer directement `/decompte` sur le build Vue (irréversible sans
un revert), le câblage production est implémenté derrière une route
**temporaire** qui coexiste avec la page legacy, pour que l'utilisateur
puisse comparer les deux dans un vrai navigateur avant qu'on bascule pour
de vrai.

**Changements** :
- `frontend/vite.config.ts` : `build.outDir` -> `static/decompte-app/`
  (à la racine du dépôt, hors de `frontend/`), `base: '/static/decompte-app/'`,
  noms de fichiers FIXES (`app.js`/`app.css`, pas de hash de cache-busting)
  -- plus simple à servir depuis Flask pour un projet solo, cohérent avec
  la décision déjà actée dans "Choix de stack figé" ; à revoir seulement si
  le cache navigateur devient un problème réel constaté. `preview.proxy`
  ajouté en miroir de `server.proxy` (utile pour tester le build avec
  `vite preview` sans passer par Flask).
- `app.py` : route `GET /decompte-vue` (`send_from_directory`) qui sert
  `static/decompte-app/index.html` tel quel -- aucune logique Jinja, le
  HTML est déjà complet après `npm run build`. Les assets
  (`static/decompte-app/app.js`/`app.css`) sont servis automatiquement par
  la route `/static/<path>` par défaut de Flask, aucun code supplémentaire
  nécessaire. `/api/*` fonctionne en same-origin (pas besoin du proxy Vite
  une fois servi par Flask).
- `.gitignore` : `static/decompte-app/` ignoré (artefact de build, comme
  `frontend/dist/`) -- généré par `npm run build`, jamais committé.

**Validé avant de rendre la main** : build (`npm run build`) réussi avec
les noms de fichiers fixes attendus, puis vérification de bout en bout sur
le VRAI serveur Flask démo (pas seulement `vite dev`) : `GET /decompte-vue`
-> 200, `GET /static/decompte-app/app.js` -> 200, `GET
/static/decompte-app/app.css` -> 200, `GET /api/decompte?miniserver=demo`
-> données réelles (3 zones, 3 mois) en same-origin, et `GET /decompte`
(page legacy) toujours à 200 et inchangée.

**PAS validé visuellement, encore une fois** -- toujours pas d'extension
Chrome connectée. **À faire par l'utilisateur** : lancer
`.venv/bin/python3 app.py config.demo.yaml` (ou pointer `VITE_API_PROXY_TARGET`
vers sa propre config) et ouvrir `http://localhost:8082/decompte-vue` dans
un vrai navigateur, à comparer à `http://localhost:8082/decompte`.

## Bascule `/decompte` sur le build Vue + nettoyage du code mort — 2026-09-23

Fait dans la foulée, sur instruction explicite de l'utilisateur de
continuer sans attendre un nouveau retour visuel (la validation de
`/decompte-vue` demandée dans la section précédente n'a pas été confirmée
explicitement avant cette bascule -- **si quelque chose cloche visuellement,
c'est ici qu'il faut regarder en premier**, un `git revert` du commit
correspondant restaure instantanément l'ancienne page).

- `app.py` : le contenu de `decompte_vue()` a remplacé celui de
  `decompte()` (même route `/decompte`, même nom de fonction/endpoint --
  aucun `url_for('decompte')` à changer ailleurs) ; la route
  `/decompte-vue` a été supprimée.
- Supprimés : `templates/decompte.html` et les 6 fichiers de
  `static/js/decompte/` (`main.js`/`api.js`/`table.js`/`charts.js`/
  `tarifs.js`/`format.js`) -- plus aucune référence après vérification par
  `grep` (`static/js/core/*.js`, partagé avec `/` et `/admin`, non touché).

**Validé avant de rendre la main** : Flask redémarré avec le nouveau
`app.py`, `GET /decompte` -> 200 (sert désormais le build Vue), `GET
/decompte-vue` -> 404 (supprimée), assets `static/decompte-app/app.{js,css}`
-> 200, et non-régression de `/` et `/admin` (200 tous les deux, aucune
référence cassée au code supprimé).

**Reste à faire** : ajouter `npm --prefix frontend run build` à la
procédure de déploiement -- fait juste en dessous, dans "Commandes
utiles".

## Vérification headless Playwright + bug trouvé (name manquants) — 2026-09-23

L'extension Claude in Chrome n'a jamais pu se connecter dans cette session,
donc aucune vérification humaine directe n'a eu lieu -- mais un navigateur
headless restait possible sans elle : Playwright + Chromium installés à la
volée dans le scratchpad (pas dans `frontend/`, pas de dépendance ajoutée au
projet) et lancés contre le VRAI serveur Flask démo (`localhost:8082`, pas
`vite dev`), avec une interaction complète plutôt qu'un simple chargement de
page.

**Bug trouvé et corrigé avant validation** : `TarifsPanel.vue` n'avait pas
d'attributs `name` sur les `<input>` du formulaire -- oubli du portage
depuis `tarifs.js`, qui lisait le formulaire via `new FormData(form)` (donc
`name` obligatoire), alors que la version Vue lit `form.*` par `v-model` et
n'en a fonctionnellement pas besoin. Sans impact fonctionnel réel (le
formulaire marchait), mais mauvaise pratique HTML (autofill, accessibilité,
sémantique de formulaire) -- corrigé, les 5 champs ont maintenant leurs
`name` d'origine.

**Résultat de la vérification** (script + captures dans le scratchpad de la
session, non conservées dans le dépôt) :
- Chargement de `/decompte` : titre, h1, 5 tableaux, 4 canvas (les 4
  graphs), 2 selects (site masqué -- un seul site en démo), **aucune
  erreur console, aucune erreur JS, aucune requête réseau échouée**.
- Tuiles KPI peuplées de vraies valeurs (1740 kWh conso totale, 446 réseau,
  1295 solaire, 74 % autoproduction), tableau par zone correct (App 1/2/3),
  bandeau d'alerte "2 mois facturables sur 3" affiché correctement.
- **Round-trip tarifs testé en conditions réelles** : formulaire rempli et
  soumis (0,25 CHF/kWh réseau, 0,15 solaire, TVA 8,1 %) -> `POST
  /api/tarifs` -> message de succès -> tableau par zone recalculé
  immédiatement (App 1 : 78.24 CHF HT / 6.34 TVA / 84.57 TTC, calcul vérifié
  à la main, correct) -> tarif visible dans le panneau avec bouton
  Supprimer. Le tarif de test a été supprimé après coup
  (`DELETE /api/tarifs/1`) pour ne pas polluer `data/demo.db`.
- Captures d'écran (avant/après tarif) inspectées visuellement par moi :
  mise en page cohérente, cartes/tableaux/graphs bien alignés, palette
  Tailwind (ambre/vert/bleu) cohérente avec la charte réseau/solaire du
  reste du dashboard.

**Ce que ça ne remplace pas** : un œil humain reste utile pour le jugement
esthétique fin (espacements, choix de couleurs, responsive mobile -- non
testé) que ce script ne peut pas juger à ma place. Recommandé mais plus
urgent au sens "est-ce que ça marche" -- désormais répondu par du réel, pas
seulement par `vue-tsc`/`npm run build`.

## Restructuration multi-pages (préparation `/` et `/admin`) — 2026-09-23

Sur demande explicite de l'utilisateur de continuer la migration Vue au-delà
de `/decompte` (revient sur la position plus prudente actée le 2026-08-26 --
"un framework frontend redevient une option à évaluer si le besoin
d'interactivité grandit" : le besoin est désormais confirmé explicitement
par l'utilisateur, pas seulement déduit). `/` (dashboard 3 onglets +
sidebar, ~900 lignes JS) et `/admin` (~115 lignes) restent à faire -- gros
chantier, donc découpé en commits vérifiables, en commençant par
l'infrastructure plutôt que par du contenu visible.

### Pourquoi une restructuration d'abord

`frontend/` était câblé pour UNE SEULE app (un `index.html`, un
`vite.config.ts`, un `src/`). Ajouter une deuxième page pose un problème
concret : Vite regroupe par défaut le code partagé entre plusieurs entrées
d'un même build en chunks -- très bien avec des noms de fichiers hashés,
mais on utilise volontairement des noms FIXES (`app.js`/`app.css`, voir
"Choix de stack figé") pour rester simple à servir depuis Flask. Sans hash,
un chunk partagé nommé de façon stable devient fragile (une évolution du
graphe de modules peut le faire disparaître/réapparaître sous un autre nom,
avec un vieux fichier caché par le navigateur qui ne correspond plus à rien).

**Décision : chaque page reste un build Vite totalement indépendant**
(aucun chunk partagé entre pages, Vue/Chart.js dupliqués par page plutôt que
mutualisés) -- accepté sciemment : les bundles sont petits (~100 kB gzip
pour decompte), et un utilisateur ne charge jamais deux pages à la fois.
Le code partagé (l'équivalent de `static/js/core/*.js`) vit dans
`frontend/shared/`, importé via un alias `@shared/*` (TS + Vite), mais reste
une dépendance SOURCE, jamais un chunk de build partagé.

### Structure livrée

```
frontend/
  package.json          scripts par page : dev:<page>, build:<page> ; "build" = tous
  tsconfig.base.json     options communes (ex tsconfig.app.json)
  tsconfig.node.json      type-check des vite.config.ts (pages/*/vite.config.ts)
  shared/                 équivalent TS de static/js/core/*.js
    format.ts             fmtNumber, dates, zoneKey/parseZoneKey/matchesZone,
                           resourceLabel, apartmentSortKey/compareApartments
                           (nouveau -- portait déjà en double dans app.py ET
                           sidebar.js, maintenant UNE seule source TS)
    charts.ts              PALETTE_*, baseLineOptions/baseBarOptions, aggregateMonthly
    api/http.ts             fetchJSON/postJSON/deleteJSON
    api/health.ts
    composables/useHealthFooter.ts
  pages/
    decompte/               déplacé tel quel depuis frontend/src/ (ancien
      index.html             emplacement), + composants aplatis
      vite.config.ts          (components/decompte/*.vue -> components/*.vue,
      tsconfig.json            redondant maintenant que "decompte" est déjà
      src/                     le dossier racine de la page)
```

`vite.config.ts` de chaque page : `root` fixé explicitement à son propre
dossier (`import.meta.dirname`) -- le root par défaut de Vite est
`process.cwd()`, pas le dossier du fichier de config, ce qui aurait cassé
dès que les scripts npm invoquent `vite --config pages/X/vite.config.ts`
depuis `frontend/`. `envDir`/`publicDir` recalés vers `frontend/` (fichiers
partagés entre pages). `outDir` calculé via `path.resolve()` depuis la
racine du dépôt plutôt qu'un chemin relatif en dur (`../../static/...`) --
voir bug ci-dessous.

### Bug réel trouvé et corrigé : le build écrivait au mauvais endroit

En déplaçant `vite.config.ts` de `frontend/` vers `frontend/pages/decompte/`
(un niveau de nesting de plus), le `outDir` relatif (`'../../static/decompte-app'`,
mis à jour à la main pour compenser UN niveau de plus) était encore faux
de UN niveau : il fallait trois `../` (decompte -> pages -> frontend ->
racine du dépôt), pas deux. Résultat : `npm run build:decompte` écrivait
dans `frontend/static/decompte-app/` (nouveau dossier, jamais servi par
Flask) **sans toucher** à l'ancien `static/decompte-app/` à la racine --
qui restait donc avec le build D'AVANT la restructuration, silencieusement
périmé. Un premier tour de vérification (Playwright contre le vrai Flask)
a montré une page qui fonctionnait parfaitement... parce qu'elle servait
l'ancien code, pas le nouveau : **une vérification qui teste le mauvais
fichier ne prouve rien**, même si son résultat a l'air bon.

Repéré en comparant la taille de `app.css` générée (12,78 kB vs 14,45 kB
attendus) plutôt que de faire confiance à un "build réussi" + "page
identique à l'écran". Corrigé en calculant `outDir` par
`path.resolve(repoRoot, 'static/decompte-app')` avec `repoRoot` lui-même
dérivé de `frontendRoot` (jamais de `../../../...` en dur) -- un niveau de
nesting supplémentaire à l'avenir (ex: une sous-catégorie sous `pages/`) ne
peut plus décaler silencieusement la sortie.

**Leçon retenue pour la suite** : après tout changement touchant
`outDir`/`base`/`root`, vérifier l'EMPLACEMENT réel du fichier de sortie
(taille, timestamp, `ls` du dossier cible) avant de faire confiance à un
rendu visuel qui peut très bien tester du contenu périmé resté en place.

### Validé avant de rendre la main

`npm run build:decompte` propre (type-check + build), sortie confirmée au
bon endroit (`static/decompte-app/`, absent de `frontend/static/`),
`pytest` 59/59, puis **re-vérification complète Playwright** contre le vrai
Flask (voir méthode dans la section précédente) : chargement propre, KPI et
tableau corrects, **round-trip tarifs réel** (soumission -> recalcul ->
suppression), aucune erreur console -- cette fois contre le bundle
effectivement reconstruit. Tarifs de test nettoyés de `data/demo.db` après
coup (au moins 3 lignes créées/supprimées pendant cette session de
vérification, à cause d'une hypothèse erronée sur la réutilisation des
`id` SQLite après suppression -- pas un bug applicatif, juste une hygiène
de nettoyage à refaire par ID vérifié plutôt que supposé).

Comportement et rendu strictement identiques à avant la restructuration
(captures d'écran comparées, aucune classe Tailwind manquante malgré la
taille de bundle CSS différente -- écart dû au tree-shaking de Tailwind
v4, pas à du contenu perdu).

### Reste à faire

- `pages/admin/` -- la page la plus petite et la plus autonome des deux
  restantes (~115 lignes JS+Jinja, aucune dépendance à `@shared/charts` ou
  au health footer dans la version legacy), candidate naturelle pour la
  prochaine étape.
- Nouvelle route API nécessaire : `GET /api/resource-types` (n'existe pas
  encore) -- `window.RESOURCE_TYPE_LABELS` est aujourd'hui injecté par
  Jinja dans `templates/index.html`, impossible une fois la page servie en
  statique pur. Nécessaire pour `/admin` ET pour le futur `/` (sidebar,
  onglets Énergie/zone).
- `pages/dashboard/` (le plus gros morceau : sidebar + 3 onglets) --
  après `/admin`.
- `package.json::"build"` à étendre (`build:decompte && build:admin && ...`)
  au fur et à mesure que les pages s'ajoutent.

## `/admin` en Vue, derrière une route de prévisualisation — 2026-09-23

Deuxième page migrée, même méthode que `/decompte` en son temps : nouvelle
brique construite et vérifiée derrière `/admin-vue` (temporaire), `/admin`
(Jinja + `static/js/admin.js`) intact en attendant la bascule + nettoyage
du code mort dans une prochaine étape.

**Backend** : `GET /api/resource-types` (nouveau, `app.py`) -- renvoie
`_cfg().resource_type_labels` en JSON. Manquait pour toute page servie en
statique pur (avant, seul `window.RESOURCE_TYPE_LABELS` injecté par Jinja
existait) ; sert `/admin` aujourd'hui, servira le futur dashboard (sidebar,
onglets Énergie/zone) aussi.

**Frontend** (`frontend/pages/admin/`, même schéma que `pages/decompte/`) :
- `types/series.ts` -- `Series` (miroir de `db.list_series`) +
  `EditableRow` (Series + état d'édition local : `editApartment`/
  `editResourceType`/`status`, jamais envoyé au serveur avant le clic sur
  Enregistrer -- contrairement à un `v-model` direct sur les données
  serveur, qui enverrait implicitement l'impression d'un état déjà
  sauvegardé).
- `api/admin.ts` -- `fetchSeries`, `fetchResourceTypeLabels`, `classify()`
  (retourne un simple booléen, comme `static/js/admin.js`, la page n'a pas
  besoin de plus pour flasher un statut de ligne).
- `components/ManualBadge.vue`, `components/ClassificationTable.vue` (une
  seule table -- pas de découpage en sous-composants par ligne, ~140 lignes
  en tout, pas justifié pour une page de cette taille).
- `views/AdminPage.vue` -- charge `/api/series` + `/api/resource-types` en
  parallèle, construit les `EditableRow`, calcule `knownApartments` (tri
  naturel via `compareApartments` de `@shared/format` -- **seule source
  du tri désormais**, avant dupliqué entre `app.py::_apartment_sort_key`
  et `static/js/sidebar.js::apartmentSortKey`, maintenant partagé par toute
  page Vue via `@shared/`, `_apartment_sort_key` restant la version Python
  utilisée par `/admin` Jinja tant qu'il existe).

**Comportement porté à l'identique** : correction manuelle => plus jamais
écrasée par le poller (`apartment_manual`/`resource_type_manual`) ; le
bouton ↺ ne fait QUE réinitialiser les flags manuels côté serveur -- la
valeur affichée ne change pas tant qu'un vrai cycle de poll n'a pas eu lieu
(comportement déjà documenté, pas une régression -- vérifié explicitement
pendant les tests, voir plus bas).

**Validé avant de rendre la main** : `npm run build:admin` propre (type-
check + build), sortie confirmée à `static/admin-app/` (pas
`frontend/static/`, leçon du bug précédent appliquée -- `outDir` calculé de
la même façon robuste que pour decompte). Puis vérification Playwright
contre le vrai Flask démo (143 lignes -- correspond aux séries de la base
démo), **round-trip réel or, pas juste visuel** :
1. édition d'une ligne (nouvel appartement + type), clic Enregistrer,
   statut "✓ enregistré", badges "manuel" apparus ;
2. confirmation via un second appel `GET /api/series` (pas juste l'état du
   DOM) que le changement a bien été persisté en base ;
3. clic ↺, confirmation que `apartment_manual`/`resource_type_manual`
   repassent à 0 -- **et que la valeur affichée NE CHANGE PAS** (comportement
   attendu, pas un bug : le recalcul n'a lieu qu'au prochain poll réel, qui
   n'arrive jamais sur le miniserver démo, volontairement inatteignable).
4. Remise à la main de la ligne modifiée à sa valeur d'origine
   (`APP1`/`eau_chaude`) pour ne pas laisser `data/demo.db` dans un état
   confus -- marquée manuelle au passage (cosmétique, sans conséquence sur
   des données de démo synthétiques).

Zéro erreur console, `pytest` 59/59 (aucun changement Python autre que le
nouvel endpoint, additif).

**Pas encore fait** : bascule `/admin` + suppression de
`templates/admin.html`/`static/js/admin.js` (prochaine étape, même méthode
que pour `/decompte`) ; vérification visuelle humaine (toujours aucun outil
de navigateur disponible dans cette session -- capture d'écran inspectée
par moi, mais un œil humain reste recommandé, non bloquant).

## Bascule `/admin` sur le build Vue + nettoyage du code mort — 2026-09-23

Même méthode que la bascule `/decompte` : le contenu de `admin_vue()` a
remplacé celui de `admin()` (même route/endpoint `/admin`, `url_for('admin')`
dans `templates/index.html` inchangé), `/admin-vue` supprimée. Supprimés :
`templates/admin.html`, `static/js/admin.js`, et la fonction Python
`_apartment_sort_key` (devenue morte -- son seul appelant était le rendu
Jinja de `/admin` ; l'équivalent client `compareApartments` de
`@shared/format` la remplace désormais, `static/js/sidebar.js` gardant sa
propre copie JS tant que le dashboard n'est pas migré).

**Validé avant de rendre la main** : Flask redémarré sans erreur (pas de
`NameError` sur la fonction supprimée -- `grep` confirmé aucun autre
appelant avant suppression), `GET /admin` -> 200 (nouvelle version),
`GET /admin-vue` -> 404, `/` et `/decompte` non régressés, `pytest` 59/59.
Puis **re-vérification Playwright complète contre la vraie route `/admin`**
(pas seulement `/admin-vue`) : 143 lignes, édition + sauvegarde + lecture
`/api/series` de confirmation + reset, zéro erreur console -- résultats
identiques à la vérification précédente contre `/admin-vue`, confirmant que
la bascule n'a rien changé au comportement. Ligne de test remise à sa
valeur d'origine après coup (même procédure qu'avant).

**Deux pages sur trois migrées.** Reste `/` (dashboard : sidebar + onglets
Explorer/Énergie/Consommations par zone) -- le plus gros morceau, à traiter
dans une prochaine étape, probablement sur plusieurs commits (sidebar
d'abord, puis un onglet à la fois).

## `pages/dashboard/` : sidebar + Explorer, derrière une route de prévisualisation — 2026-09-23

Premier morceau du plus gros chantier restant. Même méthode que
`/decompte-vue`/`/admin-vue` : `/dashboard-vue` (temporaire) à côté de `/`
(intact). **Seul l'onglet Explorer est fonctionnel** -- Énergie et
Consommations par zone affichent un placeholder "en cours de migration",
la coquille à 3 onglets étant posée maintenant pour ne pas la retoucher à
chaque futur onglet.

### Nouveautés partagées (`frontend/shared/`)

- `types/series.ts` -- `Series` déplacé ici depuis `pages/admin/` (même
  forme, utilisée par `/admin` ET le dashboard ; `pages/admin/src/types/series.ts`
  ré-exporte désormais depuis `@shared` plutôt que de la dupliquer).
- `api/series.ts` -- `loadAllSeries`/`findSeries` (cache mémoire, un seul
  `GET /api/series` par chargement de page, port de `core/api.js`) +
  `fetchSeriesData`/`fetchLatest`/`fetchDaily`.
- `config.ts` -- `loadResourceTypeLabels()` (`GET /api/resource-types`,
  mis en cache) -- remplace `core/config.js::getResourceTypeLabels()` qui
  lisait `window.RESOURCE_TYPE_LABELS` (injection Jinja, impossible sur une
  page servie en statique pur).
- `ranges.ts` -- `RANGE_PRESETS` (`1h`/`24h`/`7d`/`30d`/`1y`), miroir de la
  constante Python `app.py::RANGE_PRESETS` -- pas d'endpoint dédié, c'est
  une constante fixe du code, pas une valeur de config.yaml.

### Sidebar (`pages/dashboard/src/components/`)

Port de `static/js/sidebar.js`, décomposé en 3 composants plutôt qu'un
seul : `Sidebar.vue` (calcule les groupes site > appartement/pièce >
[type]), `SidebarGroupList.vue` (rendu récursif `<details>`, ne connaît que
la structure), `SeriesCheckboxList.vue` (les cases à cocher, feuille de
l'arbre). **`isSelected`/`onToggle` passés en provide/inject**
(`sidebarSelection.ts`) plutôt qu'en props sur 3 niveaux : seul
`SeriesCheckboxList.vue` en a besoin, les deux composants intermédiaires
sont purement structurels.

**Différence de structure importante par rapport au portage naïf** : dans
`templates/index.html` d'origine, `.sidebar` est un FRÈRE de `.content`
(qui contient les 3 onglets), pas un enfant de l'onglet Explorer -- la
sidebar reste affichée quel que soit l'onglet actif. `DashboardPage.vue`
respecte cette structure (sidebar dans une `<aside>` toujours rendue) ; la
sélection multi-capteurs de l'Explorer vit dans un composable dédié
(`useExplorerSelection.ts`) possédé par `DashboardPage.vue`, pas par
`Sidebar.vue` ni par `ExplorerTab.vue` (qui sont frères, ni l'un ni l'autre
ne peut posséder un état dont l'autre a besoin).

### Explorer (`pages/dashboard/src/tabs/ExplorerTab.vue`)

Port de `tabs/explorer-tab.js`. Reçoit `selected` (le `Map` réactif du
composable) en prop, `watch(() => props.selected, ..., { deep: true })`
pour re-render le graph à chaque coche -- un Map réactif muté en place
(`.set()`/`.delete()`) ne change jamais de référence, donc pas de watch
profond = jamais de re-render.

### Validé avant de rendre la main

`npm run build:dashboard` propre, sortie confirmée à `static/dashboard-app/`
(pas `frontend/static/`), `pytest` 59/59. Vérification Playwright contre le
vrai Flask démo : 143 cases à cocher (= nombre de séries), sélection de 2
capteurs -> graph affiché avec 2 datasets, changement de plage (24h -> 7d),
**bascule appartement/pièce avec sélection préservée** (le "bonus gratuit"
du refactor sidebar d'origine, revérifié après portage), bouton "Tout
désélectionner" fonctionnel, bascule vers les onglets Énergie/zone sans
crash (placeholder affiché), zéro erreur console.

**Pas de vérification visuelle humaine** (toujours aucun outil de
navigateur dans cette session) -- capture d'écran inspectée par moi,
mise en page cohérente avec l'original.

## Onglet Énergie porté — 2026-09-23

Le plus gros morceau du chantier dashboard (`tabs/energy-tab.js`, 442
lignes). Toujours derrière `/dashboard-vue`, `/` intact.

**Découpage en modules** (`pages/dashboard/src/tabs/energy/`), plutôt qu'un
seul gros composant comme le fichier JS d'origine -- chaque fonction pure
de `energy-tab.js` devient un module TS testable indépendamment du rendu :
- `seriesFor.ts` -- résolution des ~25 séries d'une zone (grid/solaire/
  batterie/EFM), port direct.
- `periodGroup.ts` -- tuiles jour/semaine/mois/année avec repli sur le
  relevé brut, **retourne des données plutôt que de manipuler le DOM**
  (différence structurelle avec l'original : `renderPeriodGroup` prenait un
  `container` et y poussait des éléments ; ici un objet `{tiles, any, dayV}`
  que `EnergyTab.vue` rend avec `<KpiTile v-for>`).
- `autoconso.ts`, `battery.ts` -- même principe, logique métier pure,
  aucune référence DOM.
- `charts.ts` -- 4 constructeurs de données Chart.js (`buildDailyGridSolarChart`,
  `buildMonthlyGridSolarChart`, `buildBatteryChart`, `buildPowerChart`) ;
  `dailyPairChart()` factorise la fusion de deux séries de points
  journaliers par date (dupliquée à l'identique entre `renderDailyChart` et
  `renderBatteryChart` dans le JS d'origine -- une seule version ici, avec
  un paramètre de signe pour la décharge batterie qui doit s'afficher en
  négatif).

**Nouveaux composants partagés par les futurs onglets** (`components/`) :
`ZoneSelect.vue` + `utils/zoneOptions.ts` (select groupé par site, factorisé
depuis `energy-tab.js`/`zone-tab.js` qui avaient CHACUN leur propre
`buildZoneOptions` identique), `KpiTile.vue`, `NoteText.vue`.

**`EnergyTab.vue`** orchestre : un seul `refresh()` async (zone + range
changés ensemble déclenchent un `watch([zone, range], refresh)`, comme
l'original où `setupRangeButtons` rappelait `refresh()` en entier, pas
seulement le graph de puissance).

**Validé avant de rendre la main** : `npm run build:dashboard` propre,
sortie confirmée à `static/dashboard-app/`, `pytest` 59/59. Playwright
contre le vrai Flask démo : les 6 sections rendues (Réseau&solaire,
Autoconsommation, Batterie avec son graph, Puissance instantanée,
journalière, mensuelle), tuiles peuplées de vraies valeurs, **changement de
zone recalcule bien toutes les tuiles** (valeurs différentes vérifiées),
changement de plage fonctionnel, 4 canvas, zéro erreur console. Écart
repéré et vérifié comme non-bug : le graph "Puissance instantanée" reste
vide sur la démo (12h/24h) -- confirmé via `GET /api/series/.../data` que
la série `actual` correspondante a 0 point bruts sur cette fenêtre dans
`data/demo.db`, caractéristique du jeu de données démo, pas une régression
du portage (même API, même donnée que la version legacy).

**Pas de vérification visuelle humaine** (toujours aucun outil de
navigateur dans cette session).

## Prochaine étape prévue

`pages/dashboard/` suite -- Consommations par zone (`zone-tab.js`,
158 lignes, le plus simple des 3 onglets -- réutilise `ZoneSelect.vue`,
`KpiTile.vue`, `NoteText.vue`, `@shared/charts` déjà en place). Puis
bascule `/` + suppression du code mort (`templates/index.html`,
`static/js/{main,tabs,sidebar}.js`, `static/js/tabs/*.js`,
`static/js/core/*.js` -- ce dernier à vérifier qu'aucune autre page ne le
sert encore avant suppression, mais `/`, `/admin` et `/decompte` étant
toutes les trois déjà en Vue à ce stade, ce sera le cas).

Ensuite : module de génération de factures / décomptes de charges par
appartement, côté MCP-Loxone. Point d'entrée naturel : `/api/series/<id>/data`
(agrégats horaires disponibles sur le long terme) combiné aux champs
`apartment` / `resource_type` de `series_meta`, pour calculer une
consommation par appartement et par type de charge sur une période de
facturation.

## Commandes utiles

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp config.example.yaml config.yaml && cp .env.example .env   # puis éditer
npm --prefix frontend install    # une fois, pour le frontend /decompte

# Diagnostic connexion Loxone
python3 scripts/diagnose_auth.py config.yaml

# Diagnostic websocket (lecture live à distance)
python3 scripts/diagnose_websocket.py config.external.yaml MS-Arlopi 8

# Diagnostic historique Statistics (SD card Loxone)
python3 scripts/check_statistics.py config.yaml maison

# Backfill de l'historique Statistics (dry-run d'abord, puis sans --dry-run)
python3 scripts/backfill_statistics.py config.external.yaml MS-Arlopi --dry-run
python3 scripts/backfill_statistics.py config.external.yaml MS-Arlopi

# Build du frontend /decompte (Vue) -- requis avant de lancer app.py, sinon
# /decompte répond 404 (static/decompte-app/ n'existe pas encore)
npm --prefix frontend run build

# Lancer (prod ou config alternative)
python3 app.py                     # config.yaml
python3 app.py config.demo.yaml    # dashboard de démo, données synthétiques

# Démo / test dashboard sans Loxone réel
python3 scripts/seed_demo_data.py config.demo.yaml && python3 app.py config.demo.yaml

# Dev du frontend /decompte avec rechargement à chaud (utilise le proxy
# Vite vers Flask -- voir frontend/vite.config.ts, VITE_API_PROXY_TARGET)
npm --prefix frontend run dev

# Maintenance DB (mensuel, manuel)
python3 scripts/vacuum_db.py config.yaml

# Déploiement (systemd, PC Ubuntu Server -- anciennement Pi)
npm --prefix frontend run build    # AVANT le redémarrage du service
sudo cp scripts/loxone-collector.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now loxone-collector
journalctl -u loxone-collector -f
```
