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

**Structure du dépôt (mise à jour 2026-09-24)** : tout le code Python
backend (`app.py`, `db.py`, `config.py`, etc.), ses scripts CLI, ses tests,
sa config et ses données vivent sous `backend/` (voir "Nettoyage de la
structure du backend" plus bas). Le venv Python reste à la racine du repo.
`frontend/` (SPA Vue) est resté un dossier séparé, inchangé par ce
nettoyage. **Partout dans ce fichier, une mention nue de `app.py`,
`db.py`, `scripts/...` etc. (y compris dans les sections historiques
datées ci-dessous, écrites avant ce déplacement) désigne le fichier sous
`backend/`** -- pas la peine de préfixer chaque occurrence individuellement.

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
- **Backend 100% API** : `app.py` ne sert plus aucune page HTML, uniquement
  du JSON (`/api/*`, `/health`). Le **frontend est une SPA Vue 3 +
  TypeScript + Tailwind autonome** (`frontend/`, un seul build, `vue-router`
  en mode history pour les 3 routes `/`, `/admin`, `/decompte`), qui tourne
  sur sa propre origine/hébergement et ne parle au backend que via HTTP.
  Une barre de navigation commune (`shared/components/AppNavbar.vue`,
  montée dans `App.vue`, absente de `/login`) : Dashboard, Décompte,
  Classification (admin seulement), et le compte connecté.
  Dashboard (`/`) : **Aperçu** (bilan depuis minuit + courbe) et
  **Relevés** (dernier index de chaque compteur). **Mode avancé**
  (l'ancien Explorer, confirmation avant d'entrer ; seule vue qui affiche
  la sidebar capteurs) est masqué provisoirement depuis le 2026-10-02
  (`SHOW_ADVANCED_TAB` dans `DashboardPage.vue`). Les onglets Énergie et
  Consommations par zone ont été retirés le 2026-09-25.
- **Authentification + rôles** (Flask-Login) : app complètement fermée,
  accès donné au cas par cas. Deux rôles : `admin` (tous les sites,
  lecture/écriture) et `user` (lecture seule, uniquement les sites
  explicitement accordés -- un compte peut couvrir plusieurs sites). Toutes
  les routes API protégées (`@login_required`/`@admin_required` selon
  l'action) sauf `/health`, `/api/login`, `/api/me`. Page de connexion Vue
  (`/login`) + garde de route + redirection automatique sur session
  expirée en cours de navigation + UI adaptée au rôle (liens/formulaires
  d'admin masqués pour un `user`) -- end-to-end complet et utilisable.
  Comptes gérés via `scripts/create_admin_user.py` (créer/lister/révoquer/
  accorder-retirer un site/changer de rôle), pas d'interface web de
  gestion (voir sections "Authentification backend" / "Authentification
  frontend" / "Rôles utilisateurs", 2026-09-24).
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

## Fichiers de config

Exactement 2 configs utilisables + 1 template (voir "Nettoyage des configs"
plus bas pour l'historique -- il y en avait 4 avant le 2026-09-24) :

- `config.yaml` (gitignored, jamais commité) — **production**, les 3 sites
  clients réels (MS-Arlopi, MS-PPE-Horizon, MS-PPE-Sequoia), accédés en
  websocket via URL DynDNS Loxone.
- `config.demo.yaml` (commité -- pas de secret réel dedans) +
  `scripts/seed_demo_data.py` — génère une base synthétique mais réaliste
  (3 appartements, tous les types de ressource, 14 jours d'historique) pour
  valider le dashboard **sans dépendre d'un accès Loxone réel**. Utile pour
  isoler un bug d'affichage d'un bug de connexion.
- `config.example.yaml` (commité) — template unique pour créer un nouveau
  `config.yaml` (`cp config.example.yaml config.yaml`), documente les deux
  styles de connexion (miniserver LAN en HTTP simple, miniserver distant en
  websocket).

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

## Synthèse de l'historique (août → 24 septembre 2026)

Les sections datées d'avant le 2026-09-25 ont été condensées ici le
2026-10-15 : ne restent que les faits établis, les décisions prises avec
l'utilisateur et les pièges à ne pas refaire. Le récit complet (étapes
intermédiaires, détail des validations) reste dans l'historique git de ce
fichier (`git show efa272e:CLAUDE.md`). Les sections à partir du
2026-09-25, plus bas, sont intactes et font foi en cas de contradiction
avec cette synthèse. Quand le backlog ou une section récente renvoie à une
ancienne section par son titre daté (« Topologie MS-PPE-Sequoia »,
« Authentification backend »...), le contenu est dans la sous-section
thématique correspondante ci-dessous.

### Accès distant Loxone (DynDNS Cloud) — résolu

- `/jdev/sps/io/<uuid>` (lecture d'un point) renvoie **404 à travers le
  relais « Remote Connect »**, en HTTP comme en commande websocket chiffrée.
  C'est voulu par Loxone, pas un bug ni du rate-limiting.
- Ce qui marche : websocket chiffré + `jdev/sps/enablebinstatusupdate` ; le
  Miniserver pousse en moins de 2 s un burst de TOUTES les valeurs
  courantes. `loxone_ws_client.py` (dépendance optionnelle `pyloxone-api`,
  `requirements-websocket.txt`), activé par `protocol: "websocket"` dans la
  config du miniserver. Validé : 356/356 points sur MS-Arlopi.
- Les UUID des tables d'états Loxone sont au format **8-4-4-16 (3 tirets)**,
  pas le format standard à 4 tirets. Un filtre trop strict a déjà jeté tout
  le burst en silence.
- Des valeurs texte vides (flags `jLocked`) sont normales.
- `read_delay_seconds` (défaut 0) reste dans le code, inoffensif.
- Usage : valider un miniserver client avant de se déplacer, avec
  `scripts/diagnose_websocket.py <config> <miniserver> [secondes]`.
  `scripts/debug_ws_raw.py` / `debug_ws_listen.py` : debug bas niveau.
- Le port distant change : voir « Dashboard aperçu, relevés, port Cloud
  DNS » (2026-09-25).

### Historique « Statistics » du Miniserver

- Le poller ne lit que la valeur à l'instant T, depuis son démarrage. Le
  passé vient de la fonction Statistics de Loxone (opt-in par point dans
  Loxone Config, stockée sur la carte SD du Miniserver).
- `jdev/sps/getStatisticInfo/<uuid>` puis
  `jdev/sps/getStatistic/<uuid>/{raw|diff}/<de>/<à>/...` en HTTP Basic
  **fonctionnent à travers le relais distant** (contrairement à la lecture
  live). `activeSince` est un epoch Unix standard (pas l'epoch Loxone 2009).
  Réponse binaire : Uint32 ts (Unix UTC) + Float64 valeur, little-endian,
  résolution à peu près horaire.
- `statisticV2` est **à la racine du contrôle** dans `LoxAPP3.json`, pas
  sous `details` (erreur faite deux fois : détection à 0 point).
- `scripts/backfill_statistics.py` importe l'historique dans
  `readings_hourly` sans jamais écraser une heure écrite par le poller
  (`db.upsert_hourly_batch`, `ON CONFLICT DO NOTHING`). Options `--dry-run`
  et `--since YYYY-MM-DD`.
- Seuls les états `actual`, `total` et `totalNeg` ont un historique.
  `totalDay/Week/Month/Year` et les états EFM (`Gpwr`, `Ppwr`, `Spwr`,
  `selfConsumption`) n'en ont pas.
- LoxBerry / Stats4Lox s'appuient sur ce même mécanisme : rien à y gagner.
- `scripts/check_statistics.py`, `fetch_statistics_sample.py` : diagnostics.

### Lecture des consommations : une seule méthode

- **Consommation = relevé de fin − relevé de début sur `total`/`totalNeg`**
  (`billing.reading_delta`). Jamais une somme de deltas, jamais les
  `totalX` du Miniserver : écart mesuré de 13,5 % sur App 1 Grid d'Arlopi
  (142,42 kWh contre 161,60) à cause de bornes et de ruptures différentes.
  Le dashboard et `/decompte` utilisent tous deux cette méthode.
- Seuil de rupture de compteur : `billing.min_drop_for_resource_type()`,
  0,5 pour l'énergie (kWh), 0,05 pour l'eau (m³). Il part de
  `resource_type` (préfixe `eau_`), pas de `unit`, souvent vide sur les
  compteurs d'eau réels.
- `GET /api/series/<id>/range?from=<epoch>&to=<epoch>` renvoie la forme de
  `reading_delta` (type TS `ReadingDelta` dans `shared/types/series.ts`).
- Bornes de période en **Europe/Zurich** : `billing.period_bounds` côté
  Python, `frontend/shared/periods.ts` côté client (calcul DST-safe sans
  bibliothèque, semaine commençant le lundi). `db.query_daily_last` reste
  en jours UTC (MAX de l'index par jour), utilisé pour des séries
  journalières.
- `db.query_latest()` (dernière valeur sans scanner l'historique) et
  `db.query_value_at()` (dernier relevé à une date, requête indexée).
- Unité par état (kW pour `actual`, kWh pour `total`) lue dans
  `statisticV2` : `loxone_client._extract_statistic_output_units()`.

### Les trois sites — topologie établie empiriquement, ne pas re-supposer

Les mêmes noms de séries ne mesurent pas la même chose d'un site à l'autre.

**MS-Arlopi** (3 appartements, Commerce, Rez Jardin, Communs ; compteurs
EFM `1f90...` posés en octobre 2025) :

- `<Zone> Grid (total)` = énergie achetée au réseau par la zone (identique
  à `Gpwr` de l'EFM, jamais négative).
- `<Zone> Solaire (total)` = solaire **autoconsommé** par la zone (identique
  à `Ppwr`, et son cumul égale `selfConsumption` de l'EFM).
- **Consommation facturable d'une zone = Grid + Solaire**, à deux prix.
- `Appartement N / Commerce / Rez jardin (total)` = compteur plus ancien
  (UUID `1eb6...`), de périmètre différent. Contrôle seulement, jamais
  facturé.
- `Réseau (total/totalNeg)` du bâtiment est posé au raccordement de
  l'onduleur, pas sur l'alimentation des zones : non comparable, non
  utilisé. Injection = production − autoconsommation.
- Novembre 2025 → juillet 2026 : 9 mois, 6 zones facturables. Octobre 2025
  (pose des compteurs) est incomplet.
- `Chauffage App 2` et `Chauffage App 3` sont figés depuis des mois.

**MS-PPE-Horizon** (17 zones) :

- Points nommés « Compteur <zone> <n> » (`Compteur Appartement 1`,
  `Compteur Bureau 17`). Un seul compteur par lot, **consommation totale
  non scindée** : `resource_type="energie_consommee"` est correct. EFM et
  PV au niveau du bâtiment seulement.

**MS-PPE-Sequoia** (21 lots App 01 à 43, Communs, Salles Communes, Wallbox) :

- Les séries `App XX Grid` / `App XX Solaire` (catégorie Loxone
  « Répartition Solaire ») sont **inexploitables** : valeurs calculées par
  un bloc qui répartit la production BRUTE au lieu de l'autoconsommation.
  Les kWh réinjectés sont redistribués aux lots et atterrissent sur
  « Grid ». Chiffres à jour dans « Erreur de calcul dans Loxone Config »
  (2026-10-02). La clé de répartition entre lots, elle, est juste ; le
  bloc est correct la nuit et sans réinjection.
- L'installateur a annoncé une correction le 2026-09-10. Elle ne réécrit
  pas les Statistics déjà enregistrées.
- **`Appartement N` = la consommation réelle du lot**, c'est le compteur de
  facturation (rôle inversé par rapport à Arlopi).
- Les compteurs de bâtiment (`Réseau Général Oiken` total/totalNeg,
  production) sont sur l'alimentation réelle : le bilan
  `import + production − injection` boucle avec la somme des compteurs de
  consommation.
- `PAC déjà mesuré` et `Boiler ecs mesuré aussi ?` sont inclus dans
  `Communs` : ne pas les additionner.
- Les « pertes » de 6 à 8 % étaient `Salles Communes`, consommateur réel
  oublié. Avec lui le bouclage passe à −0,6 %. Aucun compteur en panne.
- Historique : électricité depuis le 01.12.2025, eau le 28.12, chaleur le
  29.12. Premier mois complet pour tous les types : **janvier 2026**.
- Zone fantôme `APP100` (points de buanderie), pas un logement.
- 4 sorties s'appellent `Sol` au lieu de `Solaire` (`Communs Sol`,
  `App 13/21/32 Sol`), d'où un classement en `energie_consommee`.
- Config : variables `${LOXONE_PROD_SEQUOIA_*}` dans `.env`, distinctes de
  `${LOXONE_PROD_*}` (un 401 est déjà venu de là).

**Classification** (`classification.py`, un seul jeu de règles pour tous
les sites) : `DEFAULT_APARTMENT_PATTERN` reconnaît `APPxx`, « Commerce »,
« Rez Jardin », « Commun(s) », « Bureau <n> ». La règle de repli eau est
`\beau\b` / `\bwater\b` : sans frontière de mot, « Bur**eau** » était
classé en eau froide. Une reclassification automatique se fait au poll
suivant, sauf sur les séries marquées manuelles.

### Multi-site (2026-09-02)

- Le miniserver est le **premier niveau de regroupement** partout. Deux
  sites peuvent avoir un « App 1 » : ne jamais grouper par `apartment` seul.
  Côté client, clé composite `zoneKey` / `parseZoneKey` / `matchesZone`
  (`shared/format.ts`).
- `/decompte` : sélecteur de site qui scope toute la page, choix gardé en
  `localStorage`, masqué s'il n'y a qu'un site. Libellé = nom brut du
  miniserver.
- `GET /api/miniservers` ; `/api/decompte` et `/api/tarifs` prennent
  `miniserver`, validé par `_resolve_miniserver`, et filtrent les séries
  AVANT d'appeler `billing.*` (qui reste « un site à la fois »).
- Table `tarifs` scopée par site, `UNIQUE(miniserver, valid_from)`. Sa
  migration (rename + recreate + copy) est la seule exception documentée à
  la règle « jamais de DROP/recreate ».
- `scripts/export_mesures_xlsx.py --miniserver` requis dès qu'il y a
  plusieurs sites.

### Décompte de charges : `billing.py` et `repartition.py`

**Décisions prises avec l'utilisateur, à ne pas redéfaire seul** :

- Découpage **mensuel** (le bimestriel a été abandonné).
- Arlopi facturé sur Grid + Solaire ; le compteur `Appartement N` en
  contrôle.
- Les Communs restent une ligne séparée, non répartie.
- Tarifs en base avec date de prise d'effet (prix kWh réseau + prix kWh
  solaire + TVA pré-remplie à 8,1 %, non confirmée ; pas d'abonnement fixe).
  La table et les routes `/api/tarifs` existent toujours ; le panneau a été
  retiré de la page le 2026-09-25.
- L'onglet « Énergie » d'origine était réservé au split réseau/solaire
  (Arlopi). Il a été supprimé le 2026-09-25.

**Les deux taux, à ne jamais confondre** (déjà inversés une fois) :

- Taux d'**autoproduction** = solaire autoconsommé / consommation. Monte en
  été. C'est l'indicateur mis en avant.
- Taux d'**autoconsommation** = solaire autoconsommé / production. Baisse
  en été. À n'afficher qu'avec son explication.
- `tests/test_billing.py` fige la distinction.

**`repartition.py`, modèle RCP reconstruit heure par heure** :
`part_solaire = (production − injection) / (import + production −
injection)`, appliquée à la consommation mesurée du lot.

- `réseau + solaire = consommation mesurée`, exactement : le total reste le
  relevé de compteur, seule la proportion vient de l'agrégation horaire.
  Arrondir à la précision affichée AVANT de déduire la part réseau.
- `evaluer_site` choisit seul la source, et l'ordre compte :
  1. `qualite_bilan` d'abord. Sans bouclage (Arlopi : +138,7 %, compteur à
     l'onduleur) l'audit est **non testable** et rien n'est recalculé.
  2. `audit_series_loxone` ensuite, si le bilan boucle. S'il condamne les
     séries (Sequoia), répartition recalculée.
  3. Sinon on garde les séries du Miniserver, avec
     `confiance_verifiee=False` si le bilan n'a pas permis de vérifier.
- Seuil `BILAN_TOLERANCE_PCT = 15`.
- **Limite connue, non corrigée** : `bilan_exploitable` juge un pourcentage
  agrégé sur toute la fenêtre. Sur Arlopi le verdict dépend de la fenêtre
  (+7,5 % sur janvier-mai, donc « exploitable » à tort ; +32,1 % sur
  janvier-août). Correctif naturel : exiger que la majorité des
  sous-périodes bouclent.
- `billing.CONTROLE_EXCLUDE_RE` exclut `chauffage`, `c[ch]aleur` (la
  coquille « Ccaleur » de l'App 34 est réelle), `cenergie`, `nrmachine` :
  sinon le compteur de contrôle d'un lot de Sequoia était son compteur de
  chaleur.

**Exports CLI** :

- `scripts/export_appartement.py` : un appartement, ou `--tous` (décompte
  consolidé, `--recap-seul` pour un seul fichier, `--from-mois`,
  `--to-mois`, `--tva`). Sorties `docs/Decompte <site> <de> a <à>.xlsx` +
  CSV. L'onglet « Décompte » a des prix unitaires modifiables et des
  formules HT/TVA/TTC vivantes.
- `scripts/audit_repartition.py` : dossier de preuve pour l'installateur
  (`docs/Anomalie repartition solaire <site> ...xlsx`), bâti sur les heures
  où l'index d'achat du bâtiment ne bouge pas, donc sans aucun modèle de
  notre part.
- Pièges déjà rencontrés dans ces scripts :
  - la clé d'un compteur est `<rôle>:<series_id>`, pas le nom de la règle
    (trois compteurs de chaleur des communs s'écrasaient : 484 kWh au lieu
    de 3 495,25) ;
  - écarter les compteurs de contrôle muets APRÈS la promotion du compteur
    du lot en poste facturable, pas avant ;
  - l'inventaire des consommateurs n'est pas `resolve_zones` (un compteur
    par zone, `Salles Communes` perdu) mais `resolve_consommateurs()` :
    tous les compteurs de la catégorie « Energie » sauf les doublons
    signalés dans le nom (`DOUBLON_RE`) ;
  - filtrer sur la couverture des données (≥ 95 % de la période), pas
    exiger tous les compteurs à chaque heure (`M1-M6 Zähler` et la Wallbox
    n'existent que depuis peu).

**Zones particulières de Sequoia (décisions de l'utilisateur)** :

- **Salles Communes** : ligne à part. Le compteur de chaleur de la salle
  de réunion suit ; ceux du séjour et de la cuisine restent aux Communs.
  Mise en œuvre par reclassification en base
  (`db.set_series_classification(..., apartment="SALLESCOMMUNES")`, donc
  `apartment_manual=1`, 21 séries) + `billing.ZONE_LABEL_OVERRIDES`.
- **Buanderie** : colonnes kWh et cycles par appartement
  (`CEnergieAppXX` / `CNrMachineAppXX`), non déduites des Communs. Valable
  seulement depuis le **17.07.2026** (`BUANDERIE_VALID_FROM`) ; avant,
  CEnergie incrémentait tous les lots avec 0 cycle. `M1` à `M6 Zähler`
  restent hors décompte.
- **Wallbox** : ligne à part (`apartment="WALLBOX"`, manuel, 12 états du
  compteur `Wallbox Energizähler`, UUID `204c74ad-02fe-30ba`). Série de
  facturation : `Wallbox Energizähler (total)`, à 0 kWh depuis le
  04.03.2026 (`WALLBOX_VALID_FROM`). `Wallbox 22kW 32A Tree (total)` (mal
  classé `eau_froide`), le `totalNeg` et le `NFC Code Touch` ne sont pas
  utilisés. Pas de répartition par appartement possible.

### Frontend : Vue 3, une seule SPA

**Choix figé avec l'utilisateur (2026-09-23), à ne pas rouvrir sans
élément nouveau** :

- Flask/Python reste l'unique backend. Le client Loxone et le moteur de
  facturation ne se réécrivent pas.
- Frontend **Vue 3 + Vite + TypeScript + Tailwind** (l'utilisateur les
  connaît). Rendu en Tailwind pur, pas de dark mode.
- **Le frontend ne recalcule jamais de logique métier** (facturation,
  répartition, classification) : il consomme les API JSON.
- Export Excel côté serveur (`openpyxl`), par une route Flask.
- Chart.js en dépendance npm (`chart.js` + `vue-chartjs`,
  `Chart.register(...registerables)` dans `main.ts`).

**Structure** (depuis la fusion du 2026-09-24 ; avant, trois builds
indépendants servis par Flask depuis `static/*-app/`, supprimés) :

```
frontend/
  index.html, vite.config.ts, package.json
  src/
    main.ts, App.vue, router.ts, style.css
    pages/{dashboard,admin,decompte,auth}/
  shared/          format.ts, charts.ts, periods.ts, ranges.ts, config.ts,
                   auth.ts, types/, api/{http,series,health}.ts,
                   composables/, components/
```

- `vue-router` en `createWebHistory()` : la prod devra servir `index.html`
  sur toute route inconnue (fallback SPA).
- Alias `@shared/*` (`tsconfig.app.json`, qui inclut aussi
  `shared/**/*.vue`).
- Build dans `frontend/dist/`, `base: '/'`. Proxy dev `/api` + `/health`
  vers Flask, cible dans `VITE_API_PROXY_TARGET` (`frontend/.env.local`,
  gitignored ; celui de l'utilisateur ne pointe pas sur le port de la démo).
- `shared/api/series.ts::loadAllSeries` met `/api/series` en cache mémoire
  pour la durée de la page. `shared/config.ts::loadResourceTypeLabels`
  lit `GET /api/resource-types`.
- `compareApartments` (`shared/format.ts`) est la seule source du tri
  naturel des appartements.
- Backend : plus aucune page ni template, `/`, `/admin`, `/decompte`
  répondent 404 côté Flask.

**Page `/admin`** : `EditableRow` garde un état d'édition local, envoyé
seulement au clic sur Enregistrer. Le bouton ↺ ne fait que remettre les
flags manuels à 0 : la valeur affichée ne change qu'au poll suivant.

**Mode avancé du dashboard** (ex-Explorer, masqué depuis le 2026-10-02) :
`Sidebar.vue` → `SidebarGroupList.vue` → `SeriesCheckboxList.vue`, avec
`isSelected` / `onToggle` en provide/inject. La sélection vit dans
`useExplorerSelection.ts`, possédé par `DashboardPage.vue`, et survit à la
bascule appartement/pièce. Le `Map` réactif est muté en place : il faut un
`watch` profond. Les onglets sont montés en `v-show`, donc tous présents
dans le DOM.

### Authentification et rôles (2026-09-24)

- `auth.py` (Flask-Login) : un accès non authentifié répond
  `{"error": "unauthorized"}` en 401, jamais une redirection.
  `app.secret_key` vient de `SECRET_KEY` dans `.env` ; l'app refuse de
  démarrer sans.
- `POST /api/login`, `POST /api/logout`, `GET /api/me` (sans
  `@login_required` : un 401 y est la réponse normale d'un visiteur). Les
  trois renvoient `{username, role, miniservers}`. `/health` reste public.
- **Rôles, décidés avec l'utilisateur** : `admin` voit et modifie tous les
  sites ; `user` est en lecture seule sur les sites qui lui sont accordés,
  possiblement plusieurs (table `user_miniservers`).
- `app.py` : `_allowed_miniservers()` (sites configurés ∩ accordés),
  `admin_required` (403 si connecté mais pas admin),
  `_check_series_access()` pour les routes par série,
  `_resolve_miniserver()` qui distingue 400 (site inexistant) et 403 (non
  accordé). `POST /api/tarifs` partage sa route avec GET : contrôle de rôle
  inline.
- `db.py` : la migration de `users.role` met `'admin'` sur les comptes
  existants, `'user'` étant le défaut des nouveaux. `create_user()` reçoit
  toujours `role` explicitement.
- Supprimer un compte coupe immédiatement sa session en cours.
- Frontend : `shared/auth.ts` (`authState`, pas de store), garde de route
  avec `/login?redirect=`, route `/admin` en `meta.adminOnly`. Dans
  `shared/api/http.ts`, un handler global redirige vers `/login` sur tout
  401, sauf avec `skipAuthRedirect` (`/api/me`, `/api/login`).
- Comptes en CLI uniquement, `scripts/create_admin_user.py config.yaml` :
  création interactive, `--list`, `--delete USER`, `--grant USER SITE`,
  `--revoke USER SITE`, `--set-role USER ROLE`.
- **Piège de cette machine** : Python est lié à LibreSSL, `hashlib.scrypt`
  n'existe pas. Hasher avec `method="pbkdf2:sha256"`.
- Python 3.9 : `from __future__ import annotations` obligatoire dès qu'on
  écrit `dict | None`.
- CORS non ajouté, volontairement : la cible est un reverse proxy unique.
- Compte de test de la démo : `demo` / `demo123` (`config.demo.yaml`).

### Nettoyage des configs et de la structure du backend (2026-09-24)

- **Nettoyage des configs** : `config.yaml` est l'ancien
  `config.external.yaml` (3 sites clients). L'ancien site local « maison »
  est abandonné. Un seul template, `config.example.yaml`.
- La base de prod `data/loxone_externe_test.db` a été renommée
  `data/loxone.db`, empreinte identique avant et après.
- **Nettoyage de la structure du backend** : tout le Python, ses scripts,
  tests, configs, `.env` et `data/` sont sous `backend/`. `.venv` reste à
  la racine (décision de l'utilisateur). Scripts et tests trouvent les
  modules par `sys.path.insert(0, parent.parent)`.
- **Déplacer ou renommer la base demande d'arrêter `app.py`** : Flask ouvre
  une nouvelle connexion SQLite à chaque requête sur un chemin relatif, et
  SQLite crée en silence un fichier vide s'il ne le trouve pas.
- `backend/scripts/loxone-collector.service` garde `User=pi` et
  `/home/pi/...`, à adapter par l'utilisateur au déploiement.

### Leçons de validation

- Sans navigateur piloté, valider en Playwright headless contre le vrai
  Flask de démo, avec une vraie interaction (round-trip), pas un simple
  chargement. Aucune de ces pages n'a eu de relecture visuelle humaine
  pendant la migration ; le responsive mobile n'a pas été testé.
- Après un changement de `outDir` / `base` / `root`, vérifier l'emplacement
  et la date du fichier produit : un build écrit ailleurs laisse l'ancien
  bundle en place, et la page « marche » en testant du code périmé.
- Pour tester une session expirée, déclencher une requête non mise en
  cache (`/api/series/<id>/data`), pas `/api/series`.
- Avec `curl`, un test de logout a besoin de `-c` en plus de `-b`.
- Un test de contrôle d'accès doit porter sur une vraie série du site, pas
  sur un site fictif.
- Nettoyer ce qu'un test écrit dans `data/demo.db` (tarifs,
  classifications, comptes), par identifiant vérifié.
- `app.py` ne lit config et code qu'au démarrage : tout changement Python
  ou de config demande un redémarrage.
- `scripts/seed_demo_data.py` reproduit la sémantique réelle (`actual` et
  `total` historisés sur 60 jours, « Solaire » d'une zone = autoconsommé,
  « Solaire » du bâtiment = production, compteur de contrôle par zone).

## Tâches à faire (backlog consolidé) — mis à jour 2026-09-25

Liste vivante, tenue à jour comme "Prochaine étape prévue"/"Commandes
utiles" (contrairement aux sections datées ci-dessus, qui ne sont jamais
réécrites) -- regroupe tout ce qui était jusqu'ici éparpillé en notes
"reste à faire"/"pas encore fait" dans les sections historiques. Cocher/
retirer une ligne d'ici quand elle est faite, plutôt que de laisser
plusieurs endroits du fichier se contredire sur ce qui reste à faire.

### Prochaine fonctionnalité

- **Décompte RCP simplifié** (`/decompte`) -- fait (2026-09-28). Le calcul
  passe par `evaluer_site`. Sequoia : parts recalculées sur le compteur du
  lot ; la page le dit. Août 2026 vérifié contre `export_appartement.py`
  (25 zones, mêmes kWh : 1 011,09 réseau + 3 102,03 solaire = 4 113,12 de
  consommation, production 6 016,04, écart de bilan +0,16 %). Le compteur
  de production sur une fenêtre passée est celui qui a des heures
  (« Production Solaire » jusqu'au 2026-09-09 ; « Solaire (total) » seulement
  depuis le 2026-09-24). Pas de PDF : l'export est Excel + CSV des kWh.
- Compteurs `Chauffage App 2` (4167) et `Chauffage App 3` (2896) figés
  depuis des mois (voir "Page de décompte de charges", 2026-08-28) -- hors
  périmètre du décompte électrique actuel, à signaler seulement si le
  chauffage doit être facturé un jour.

### Déploiement (explicitement différé -- "garde Caddy pour plus tard")

Tout regroupé dans `docs/plan-installation-auth-frontend-docker.md`, à
reprendre en bloc quand l'utilisateur relance le sujet :

- **Fallback SPA** côté serveur de prod pour `vue-router` en mode
  `createWebHistory()` -- toute route inconnue (`/admin`, `/decompte` en
  accès direct) doit renvoyer `index.html`, sinon 404 sur un serveur
  statique naïf (`vite preview` le fait automatiquement, un vrai Caddy/
  nginx de prod ne le fait pas sans configuration explicite -- voir
  "Backend 100% API + frontend fusionné en une seule SPA", 2026-09-24).
- **CORS / cookies cross-origin** si jamais frontend et backend finissent
  sur des domaines réellement différents sans reverse proxy unificateur
  (`flask-cors` + `SameSite=None; Secure`, donc HTTPS) -- volontairement
  pas ajouté tant que l'architecture de déploiement (Caddy en reverse
  proxy unique) n'est pas confirmée (voir "Authentification backend",
  2026-09-24).
- Caddy comme reverse proxy devant Flask + fichiers statiques du frontend
  (avec le fallback SPA ci-dessus) + Docker -- portée complète du plan,
  jamais commencée dans ce dépôt.
- `backend/scripts/loxone-collector.service` (systemd) garde son
  `User=pi`/chemin `/home/pi/...` d'origine -- à adapter par l'utilisateur
  au déploiement réel (PC Ubuntu Server) le moment venu, jamais fait à sa
  place (voir "Nettoyage de la structure du backend", 2026-09-24).

### Dette technique (structure du code -- pas avant que l'utilisateur ne le redemande)

Décidé explicitement avec l'utilisateur (2026-09-24) : se concentrer sur le
fonctionnement d'abord, revenir sur la structure ensuite.

- **Découper `backend/app.py` en Blueprints Flask par feature**
  (`routes/auth.py`, `routes/series.py` -- aperçu/relevés/mode avancé
  --, `routes/decompte.py`, `routes/admin.py` -- classification). Pattern
  Flask standard, risque faible.
- `backend/db.py` (~650 lignes, tous les accesseurs SQLite -- séries,
  tarifs, users -- mélangés) : candidat plus faible pour un découpage par
  feature (disperserait une connexion SQLite unique et son schéma à
  travers plusieurs fichiers pour un gain moins net à cette taille de
  projet) -- à revisiter seulement s'il continue à grossir.
- `README.md` reste partiellement daté (framing Raspberry Pi, ne mentionne
  pas le frontend Vue ni l'authentification) -- signalé lors du nettoyage
  de structure du 2026-09-24, pas repris.

### Terrain / installateur (hors code, à relancer si besoin)

- **MS-PPE-Sequoia** : 4 sorties du bloc de répartition solaire s'appellent
  `Sol` au lieu de `Solaire` (`Communs Sol`, `App 13/21/32 Sol`) -- les
  fait classer en `energie_consommee` au lieu de `energie_solaire` (voir
  "Topologie MS-PPE-Sequoia...", 2026-09-09). Sans impact fonctionnel
  (`repartition.py` ne dépend pas de ces séries pour recalculer), mais
  cosmétique à corriger côté Loxone Config si l'installateur y retourne.
  (Le vrai bug de répartition solaire de ce même chantier, lui, **a déjà
  été corrigé par l'installateur** le 2026-09-10 -- ne pas le re-signaler.)

### Nettoyage mineur, non bloquant

- `_to_delete/` (gitignored, racine du dépôt) accumule des artefacts
  d'anciens nettoyages jamais purgés pour de bon (ancienne base "maison"
  + wal/shm, `config.external.yaml`, `test_probe.db(-journal)`,
  `git-index.lock-leftover`, `_update.zip` -- ~230 Ko au total, rien de
  volumineux mais à vider une fois confirmé qu'aucun de ces fichiers n'est
  plus utile).
- Limite connue du garde-fou `repartition.py::bilan_exploitable` : juge sur
  UN pourcentage agrégé sur toute la fenêtre demandée, ce qui peut masquer
  une variation saisonnière (détail chiffré dans "Topologie MS-PPE-Sequoia
  + répartition solaire recalculée", 2026-09-09, sous-section "Limite
  connue du garde-fou") -- correctif naturel si le sujet revient : exiger
  que la majorité des sous-périodes bouclent, pas seulement la moyenne.

## Dashboard aperçu, relevés, port Cloud DNS — 2026-09-25

Journée passée sur le tableau de bord et sur la connexion Sequoia. Les
onglets Énergie et Consommations par zone n'existent plus.

### Onglets

- **Aperçu** (onglet par défaut). Tuiles **depuis minuit** (Europe/Zurich,
  index de fin − index de début, comme `/decompte`) : production,
  consommation totale, autoconsommation (solaire sur place / production)
  et autonomie (solaire sur place / consommation). Le relevé de début doit
  être à moins de 3 h de la borne : sinon le dernier index connu est celui
  d'avant un trou de collecte, et le delta compterait ces jours-là
  (constaté le 2026-09-28, collecte arrêtée du 25 au 28). Même distinction
  de taux que `/decompte`.
- Courbe : **Solaire** = puissance `actual` du compteur de production ;
  **Consommation totale** = somme des `actual` de tous les consommateurs
  électriques du bâtiment (Arlopi : Grid + Solaire de chaque zone ;
  Horizon et Sequoia : compteurs de lot). L'axe part de **00:00** même
  s'il n'y a pas encore de point ; le survol affiche l'heure, pas le
  timestamp. Les deux aires se superposent (pas empilées en addition)
  pour que le solaire puisse passer au-dessus de la consommation.
- **Relevés** remplace Énergie. `GET /api/releves` : dernier index de
  chaque compteur `Meter` en `total` / `totalNeg` (pas la puissance).
  Tableau par site puis par zone.
- **Mode avancé** = l'ancien Explorer, avec confirmation avant d'entrer.
  La sidebar des capteurs n'est affichée que là. Sur Aperçu et Relevés
  la page prend toute la largeur (plus de `max-w-7xl`, navigation dans
  l'en-tête).

### Compteur solaire Sequoia

`pickProduction` prenait **Production Solaire**, figé (0 kWh le
25.09.2026, dernier point ancien). Le compteur qui avance s'appelle
**Solaire** (`Solaire (total)`, +11,5 kWh ce jour-là, cohérent avec les
11,4 kWh lus sur place). Règle : s'il existe un total de bâtiment dont
le libellé est exactement `Solaire (total)`, c'est lui ; sinon
`Production` / `PV Production` (Arlopi, Horizon). `Solaire & Batterie`
d'Arlopi n'est pas concerné.

### Moniteur de flux d'énergie

Le moniteur **du bâtiment** (plusieurs nœuds Load/Group ; pas les
moniteurs d'une seule zone) est de nouveau collecté : `include_types`
vaut `["Meter", "EFM"]`, et seuls les `actualN` reliés à un nœud
Production / Grid / Load / Group sont gardés. La courbe de l'aperçu
**ne s'en sert pas** : ces sorties n'ont pas d'historique Statistics,
seulement les points depuis le démarrage du poll. La courbe reste sur
les `actual` des compteurs.

### Trou de collecte comblé par Statistics

Les `actual` du poller ne couvrent que les heures où le collecteur
tournait. `scripts/backfill_statistics.py --since 2026-09-24` a importé
l'historique horaire du Miniserver dans `readings_hourly` (sans écraser
une heure déjà écrite) pour les trois sites : Arlopi 78 séries, Horizon
51, Sequoia 429. `/api/series/<id>/data` mélange déjà brut et horaire,
donc l'aperçu montre ces heures.

### Port distant qui change (Sequoia, et les autres)

Le port écrit dans `config.yaml` n'est pas stable. Sequoia était en
**55061** (connexion refusée) ; le port annoncé à la main (**52581**)
était fermé aussi. L'annuaire Loxone
(`https://dns.loxonecloud.com/?getip&snr=<série>&json=true`, champ
`IPHTTPS`) publiait **53581**, seul port ouvert. Après redémarrage :
poll Sequoia OK, 437 points.

`loxone_client.resolve_cloud_endpoint` relit hôte et port HTTPS à
chaque construction de client pour un hôte `*.dyndns.loxonecloud.com`.
Le port du yaml n'est qu'un repli si l'annuaire ne répond pas. Le
websocket du poller utilise le hôte et le port résolus, pas ceux figés.
Arlopi et Horizon avaient aussi changé de port ; le même mécanisme les
a rejoints.

## Décompte RCP simplifié — demande du 2026-09-25

**Travail du jour, demandé le matin.** La spécification est ci-dessous ;
le réalisé et ce qui reste sont dans la section suivante (« état en fin
de session »). Ne pas reprendre cette liste comme si rien n'avait été
fait.
Revoir `/decompte` pour en faire un décompte RCP (regroupement pour
consommation propre) juste, sur les données actuelles, et simplifier la
page. Le chiffrage sort du périmètre : plus de prix, plus de HT/TVA/TTC,
plus de panneau tarifs. Ce qui reste, ce sont les kWh (réseau, solaire
autoconsommé, consommation, taux d'autoproduction) et deux graphiques.

La page actuelle (`DecomptePage.vue`) sert de référence pour « comme
actuellement » : sélecteur de site, `GlobalBanner`, tuiles `PeriodeKpis`,
tableau `ZoneTable` (ligne de total incluse). Le mois se choisit aujourd'hui
dans une liste déroulante (« Mois à facturer »), indépendante d'un second
sélecteur « Historique affiché » qui borne les graphiques.

### Page cible

- **Choisir le site** — sélecteur conservé (un site à la fois, comme
  aujourd'hui).
- **Messages** — garder le bandeau d'erreur ou d'information actuel
  (`GlobalBanner`, et le message de chargement / d'absence de données).
- **Choisir le mois avec un calendrier** (plus la liste déroulante). Si
  le mois choisi est le mois en cours, le décompte s'arrête au **dernier
  point de mesure**, pas à la fin du mois calendaire.
- **Bandeau KPI actuel** (`PeriodeKpis` : consommation totale, acheté au
  réseau, solaire autoconsommé, taux d'autoproduction) — le garder, et
  vérifier qu'il se recalcule bien sur le mois choisi dans le calendrier.
  La tuile « Montant TTC » part avec le chiffrage.
- **Décompte par zone, pour ce mois** — colonnes conservées : zone,
  réseau, solaire, consommation, autoproduction (calculée), état. Ligne
  de **total** sous le tableau, comme aujourd'hui. Colonnes HT / TVA /
  TTC retirées.
- **Consommation mensuelle de l'immeuble** — graphique conservé
  (`EvolutionChart`). Vérifier que les données affichées sont les bonnes.
  On doit y voir la **production solaire comparée à la consommation de
  l'immeuble**.
- **Devenir de la production solaire** — graphique conservé
  (`SolaireChart`). Vérifier aussi les données qu'il affiche.
- **Tous les autres graphiques retirés** : « Répartition par zone »
  (`ZonesChart`) et « Les deux taux d'autonomie » (`TauxChart`).
- **Panneau Tarifs retiré** (`TarifsPanel`).
- **Téléchargement** — pouvoir produire un Excel et un CSV de ces
  chiffres (le décompte du mois choisi : par zone + total) et les
  télécharger.

Hors de la liste demandée, donc à retirer dans la simplification (la
page doit être plus simple) : le sélecteur « Historique affiché », le
tableau bâtiment sous le graphique solaire (`BatimentTable`), et le
dépliant « Compteurs de contrôle et correspondance des séries ».

### RCP sur les données actuelles

Le but n'est pas seulement de retirer des blocs. Le décompte doit être
une RCP correcte avec les données déjà en base. Rappel utile pour
l'implémentation (sections 2026-08-28 et 2026-09-09) : consommation
facturable d'une zone = part réseau + part solaire autoconsommée ;
`/decompte` n'est pas encore branché sur `repartition.py` (Sequoia
affiche encore les séries du Miniserver, dont la répartition historique
est fausse). La vérification des deux graphiques conservés et du tableau
fait partie du travail, pas un à-côté.

Export : la décision déjà prise (« Choix de stack figé », 2026-09-23)
tient — Excel côté serveur avec `openpyxl`, déclenché par une route
Flask, pas régénéré dans le navigateur. Le CSV suit le même principe.

## Décompte RCP simplifié — état en fin de session, 2026-09-25

La page demandée le matin est en place et commitée (`51720b5` à
`3dcadc0`). Le collecteur Flask a été **arrêté** à la demande de
l'utilisateur en fin de session (plus rien sur le port 5050). Pour
reprendre : depuis `backend/`, `../.venv/bin/python3 app.py config.yaml`.
`app.py` ne recharge pas le code tout seul (`use_reloader=False`) : un
serveur lancé avant un changement répond 404 sur les routes nouvelles.
C'est ce qui a bloqué le premier téléchargement Excel.

### Page

- Sélecteur de site conservé. Mois choisi avec un calendrier
  (`input type="month"`). Mois en cours : la période affichée va jusqu'au
  dernier relevé (`reading_delta` s'arrête déjà au dernier point connu).
- Bandeau KPI : consommation, réseau, solaire autoconsommé, taux
  d'autoproduction. Plus de tuile TTC. Les totaux additionnent les
  relevés présents.
- Tableau par zone : zone, réseau, solaire, consommation, état, plus la
  ligne Total. **Pas** de colonne autoproduction (retirée ensuite, sur
  demande) ni de HT/TVA/TTC.
- Graphique « Consommation mensuelle de l'immeuble » : barres côte à
  côte, consommation des zones et production solaire. Elles ne
  s'additionnent pas.
- Graphique « Devenir de la production solaire » : autoconsommé (vert) et
  injecté (bleu), empilés.
- Retirés : répartition par zone, les deux taux, panneau tarifs,
  sélecteur « Historique affiché », tableau bâtiment, dépliant des
  compteurs de contrôle.

### Données incomplètes

Un lot sans relevé n'empêche plus le décompte. Le total et les deux
graphiques utilisent les lots qui ont une paire réseau + solaire. Sous
le tableau, la liste des relevés manquants. Excel et CSV ne partent
qu'après un modal « Établir le décompte en l'état » si cette liste n'est
pas vide.

Chiffre vérifié sur MS-PPE-Sequoia, août 2026 : 22 lots, **6 880,7 kWh**
de consommation et **6 016,0 kWh** de production. Autoconsommation
connue **921,1 kWh**, injection **5 094,9 kWh**. L'injection est haute :
le solaire des lots sans relevé est compté comme injecté
(`production - autoconsommation connue`).

### Export

`GET /api/decompte/export.xlsx` et `GET /api/decompte/export.csv`
(`mois=YYYY-MM`, `miniserver=`). Colonnes : Mois (libellé, ex.
« Août 2026 »), Zone, Réseau, Solaire, Consommation, État, plus le
total. CSV en point-virgule et virgule décimale. `openpyxl==3.1.5`
dans `backend/requirements.txt`.

### Fait le 2026-09-28

`/api/decompte` passe par `evaluer_site`. Sequoia est en répartition
recalculée ; la page le dit sous le tableau. Août 2026 colle à
`export_appartement.py` (voir le backlog). Sur un mois antérieur au
2026-09-24, la production est « Production Solaire » : « Solaire (total) »
n'a pas d'heures avant cette date et ferait déclarer le bilan incalculable.

## Erreur de calcul dans Loxone Config (Sequoia) — 2026-10-02

Le bloc « Répartition Solaire » de MS-PPE-Sequoia calcule faux. Ce n'est
pas le collecteur. Relevé au 2026-10-02 sur les Statistics du Miniserver :
**2 029 heures** où le compteur d'achat du bâtiment n'a pas bougé, et
pourtant les sorties « Grid » des lots ont augmenté, **26 119 kWh**
d'achat au réseau physiquement impossible. Le bloc répartit de l'énergie
qui a été réinjectée comme si elle avait été consommée, et ces kWh
atterrissent sur « Grid ». Une correction faite ensuite dans Loxone
Config ne réécrit pas les Statistics déjà enregistrées : tout décompte
d'une période passée reste sur le compteur du lot (`Appartement N`),
pas sur Grid + Solaire.

Le compteur de production a aussi été remplacé dans cette config.
« Production Solaire » s'arrête le 9 septembre 2026 (le contrôle n'est
plus dans la structure ; les Statistics déjà importées sont reprises).
« Solaire (total) » commence le 10 septembre. Un mois à cheval sur les
deux additionne les deux écarts d'index. Septembre 2026, appartement 1 :
**77,11 kWh** (671,702 − 594,594 au 30 septembre 23 h 45), pas les 70,06
du dernier point du collecteur (28 septembre 17 h 55).

Le bandeau « relevés manquants » au-dessus du décompte ne s'affiche que
pour le mois choisi dans le calendrier.

## Mode avancé masqué — 2026-10-02

L'onglet **Mode avancé** du dashboard (l'ancien Explorer : séries brutes,
sidebar des capteurs, confirmation avant d'entrer) est caché
provisoirement. Aperçu et Relevés restent affichés. Le code de l'onglet
n'est pas retiré : `SHOW_ADVANCED_TAB` dans
`frontend/src/pages/dashboard/views/DashboardPage.vue` vaut `false` ;
le remettre à `true` réaffiche l'onglet.

## Barre de navigation — 2026-10-02

Une vraie navbar en haut de toutes les pages, sauf `/login` :
`frontend/shared/components/AppNavbar.vue`, montée dans `App.vue`.
Liens **Dashboard** (`/`), **Décompte** (`/decompte`), **Classification**
(`/admin`, admin seulement), lien actif souligné, compte connecté et
déconnexion à droite. Sous 640 px, un bouton « Menu » ouvre la liste.
Les liens et `AuthStatus` que chaque page dessinait dans son propre
en-tête sont retirés : la règle « chaque page garde son propre
`<header>` sans chrome partagé » (2026-09-23) ne tient plus. Chaque page
garde son titre (`h1`). Le dashboard n'a que les onglets Aperçu et
Relevés.

Vérifié en navigateur headless sur `config.demo.yaml` (compte `demo`) :
liens et lien actif sur les trois pages, aucun rechargement de bundle
entre pages, Classification présente pour un admin, menu mobile qui se
referme après un clic, déconnexion vers `/login`.

## Prochaine étape prévue

Le décompte RCP est branché. La suite du backlog, seulement si on la
relance : compteurs de chauffage figés (hors périmètre électrique), ou le
déploiement (Caddy, fallback SPA). Relancer `app.py` pour que l'API serve
ce calcul (`use_reloader=False`).

## Commandes utiles

Backend et frontend sont deux process séparés (voir "Backend 100% API" --
Flask ne sert plus aucune page, `frontend/` est une SPA autonome qui parle
à l'API en HTTP). Depuis le "Nettoyage de la structure du backend"
(2026-09-24), tout le code Python + sa config + ses données vivent sous
`backend/` (le venv, lui, reste à la racine du repo -- voir cette section
pour le détail) : les commandes ci-dessous supposent qu'on est entré dans
`backend/` (`cd backend`), sauf mention contraire explicite.

```bash
# Setup backend (venv à la racine du repo, PAS dans backend/)
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
cp config.example.yaml config.yaml && cp .env.example .env   # puis éditer
# puis générer SECRET_KEY dans .env (voir .env.example) :
python3 -c "import secrets; print(secrets.token_hex(32))"

# À partir d'ici, on est dans backend/ (cd backend fait ci-dessus)

# Créer un compte (requis pour se connecter -- pas d'inscription en ligne)
python3 scripts/create_admin_user.py config.yaml

# Setup frontend (une fois -- depuis la racine du repo, pas backend/)
npm --prefix ../frontend install    # si on est dans backend/
# ou, depuis la racine : npm --prefix frontend install

# Diagnostic connexion Loxone
python3 scripts/diagnose_auth.py config.yaml

# Diagnostic websocket (lecture live à distance) -- MS-Arlopi/MS-PPE-Horizon/
# MS-PPE-Sequoia vivent tous dans le config.yaml unique de prod.
# Le collecteur (LoxoneClient) relit le port HTTPS via dns.loxonecloud.com
# pour tout hôte *.dyndns.loxonecloud.com ; le port du yaml n'est qu'un repli.
python3 scripts/diagnose_websocket.py config.yaml MS-Arlopi 8

# Diagnostic historique Statistics (SD card Loxone)
python3 scripts/check_statistics.py config.yaml MS-Arlopi

# Backfill de l'historique Statistics (dry-run d'abord, puis sans --dry-run).
# --since comble un trou de collecte (horaire, n'écrase pas le poller).
python3 scripts/backfill_statistics.py config.yaml MS-Arlopi --dry-run
python3 scripts/backfill_statistics.py config.yaml MS-Arlopi --since 2026-09-24

# Lancer le backend (API JSON seule, prod ou config alternative)
python3 app.py                     # config.yaml
python3 app.py config.demo.yaml    # API de démo, données synthétiques

# Démo / test sans Loxone réel
python3 scripts/seed_demo_data.py config.demo.yaml && python3 app.py config.demo.yaml

# Dev frontend avec rechargement à chaud, en parallèle du backend -- depuis
# la racine du repo (pas backend/) ; proxy /api + /health vers Flask (voir
# frontend/vite.config.ts, VITE_API_PROXY_TARGET dans frontend/.env.local
# pour changer la cible)
npm --prefix frontend run dev       # http://localhost:5173

# Build frontend (prod) -- produit frontend/dist/, à déployer comme un
# site statique (nginx/Caddy/CDN...), indépendamment du backend
npm --prefix frontend run build
npm --prefix frontend run preview   # tester ce build localement (avec proxy API)

# Maintenance DB (mensuel, manuel) -- depuis backend/
python3 scripts/vacuum_db.py config.yaml

# Déploiement backend (systemd, PC Ubuntu Server -- anciennement Pi) --
# depuis backend/ (le .service référence déjà les chemins backend/app.py
# et backend/data, voir backend/scripts/loxone-collector.service)
sudo cp scripts/loxone-collector.service /etc/systemd/system/
sudo nano /etc/systemd/system/loxone-collector.service   # adapte User/chemins
sudo systemctl daemon-reload && sudo systemctl enable --now loxone-collector
journalctl -u loxone-collector -f

# Déploiement frontend : servir frontend/dist/ (fichiers statiques) via
# n'importe quel serveur web, en configurant un fallback SPA (toute route
# inconnue -> index.html, nécessaire avec vue-router en mode history) et
# un reverse proxy de /api + /health vers le backend -- voir
# docs/plan-installation-auth-frontend-docker.md pour le détail (Caddy).
```
