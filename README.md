# Loxone Collector

Petit serveur web autonome pour Raspberry Pi qui interroge périodiquement un
ou plusieurs Miniservers Loxone en local, stocke l'historique en SQLite, et
propose un dashboard web avec graphs (Chart.js) pour consulter les données.

Pensé pour tourner en continu sur un **Raspberry Pi 4B (2 Go RAM) avec une
carte SD 32 Go** : un seul process Python (Flask + thread de poll), pas de
base de données externe, écritures SQLite limitées et historique
auto-résumé pour ne pas remplir la carte SD.

Ce projet est la brique de collecte de données du projet plus large
"MCP-Loxone" (récupération des valeurs de plusieurs miniservers pour ensuite
générer des factures / décomptes de charges) : les données historisées ici
(notamment les compteurs d'énergie) pourront être exploitées plus tard pour
calculer ces décomptes.

## Fonctionnement en un coup d'œil

1. Au démarrage, pour chaque miniserver configuré, le poller télécharge le
   fichier de structure de l'installation (`/data/LoxAPP3.json`) et en
   extrait tous les points mesurables (capteurs, compteurs, températures de
   pièce, ...).
2. Toutes les `poll_interval_seconds` (60s par défaut), il relit la valeur
   courante de chaque point via l'API locale (`/jdev/sps/io/<uuid>`) et
   l'écrit en SQLite.
3. Une fois par jour, les données brutes plus vieilles que
   `raw_retention_days` (30 jours par défaut) sont résumées en moyennes
   horaires puis supprimées, pour borner la taille de la base.
4. Le dashboard web (Flask) lit cette base SQLite et affiche des graphs
   (Chart.js, servi en local, aucune dépendance internet nécessaire une
   fois installé).

## Installation sur le Raspberry Pi

Système recommandé : Raspberry Pi OS Lite (64 bits), sans interface
graphique, pour économiser la RAM disponible sur les 2 Go.

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip git

# Récupère le projet sur le Pi (copie ce dossier, ou clone ton propre dépôt git)
cd /home/pi
# ... copier/cloner loxone-collector ici ...
cd loxone-collector

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config.example.yaml config.yaml
cp .env.example .env
nano .env          # renseigne les identifiants Loxone
nano config.yaml   # adapte host/port/nom du ou des miniservers
```

### Créer un utilisateur Loxone dédié (recommandé)

Dans Loxone Config, crée un utilisateur avec le droit "Visualisation"
uniquement (lecture seule), plutôt que d'utiliser le compte admin dans ce
service. Cela limite les dégâts en cas de fuite des identifiants stockés sur
le Pi.

### Test manuel

```bash
source .venv/bin/activate
python app.py
```

Puis ouvre `http://<ip-du-pi>:8080` depuis un navigateur sur le même réseau.
`http://<ip-du-pi>:8080/health` donne l'état du dernier cycle de poll par
miniserver (utile pour diagnostiquer un souci d'identifiants/réseau).

### Démarrage automatique (systemd)

```bash
sudo cp scripts/loxone-collector.service /etc/systemd/system/
sudo nano /etc/systemd/system/loxone-collector.service   # vérifie User/chemins
sudo systemctl daemon-reload
sudo systemctl enable --now loxone-collector
sudo systemctl status loxone-collector
journalctl -u loxone-collector -f     # logs en direct
```

## Authentification avancée / si l'API refuse le Basic Auth

Ce projet utilise l'API locale HTTP documentée par Loxone (Basic Auth +
`/jdev/sps/io/<uuid>`), qui fonctionne sur la plupart des installations en
réseau local. Si ton Miniserver renvoie une 401 malgré des identifiants
corrects (certains firmwares récents peuvent imposer une authentification
Websocket chiffrée par token pour un accès "sécurisé"), deux options :

- Vérifie dans Loxone Config (Miniserver > Général > options réseau/sécurité)
  qu'un accès local non chiffré n'est pas explicitement désactivé.
- Si nécessaire, remplace `loxone_client.py` par une implémentation basée
  sur la librairie `pyloxone-api` (utilisée par l'intégration Loxone de
  Home Assistant), qui gère l'échange de clé RSA/AES et les tokens. La
  structure du reste du projet (base SQLite, dashboard, API `/api/series`)
  reste inchangée : seule la façon d'obtenir `structure` et `values` change.

## Ménager la carte SD (32 Go)

Quelques principes déjà appliqués par défaut, à connaître si tu ajustes la
config :

- **Mode WAL + `synchronous=NORMAL`** : moins de `fsync` qu'en mode SQLite
  par défaut.
- **Résumé horaire automatique** (`raw_retention_days`) : les données brutes
  minute par minute ne s'accumulent pas indéfiniment ; l'historique long
  terme (utile pour les décomptes de charges) est conservé sous forme de
  moyennes horaires, qui pèsent beaucoup moins lourd.
- **`checkpoint_wal` quotidien** : évite que le fichier `loxone.db-wal` ne
  grossisse indéfiniment entre deux VACUUM.
- **`scripts/vacuum_db.py`** : à lancer une fois par mois (cron) pour
  compacter le fichier `.db` — pas plus souvent, car c'est une opération
  qui réécrit toute la base.
- Ordres de grandeur : avec ~50 capteurs suivis, un poll toutes les 60s
  produit environ 50 × 1440 = 72 000 lignes brutes par jour, résumées après
  30 jours en 50 × 24 = 1 200 lignes horaires/jour. Sur plusieurs années,
  la base reste de l'ordre de quelques centaines de Mo, largement gérable
  sur une carte 32 Go.
- Si tu veux aller plus loin : déplacer `db_path` vers une clé USB ou un
  SSD externe (via `config.yaml`) réduit encore l'usure de la carte SD sur
  le très long terme — pas indispensable au démarrage.

## API JSON

- `GET /api/series` — liste des capteurs suivis (id, label, pièce,
  catégorie, unité).
- `GET /api/series/<series_id>/data?range=24h` — points `{ts, value}` pour
  une série. `range` accepte `1h`, `24h`, `7d`, `30d`, `1y`, ou bien
  `start`/`end` en timestamps Unix explicites.
- `GET /health` — dernier statut de poll par miniserver.

Cette API peut être réutilisée plus tard par le module de génération de
factures/décomptes de charges du projet MCP-Loxone (ex: agréger les valeurs
d'un compteur d'énergie sur une période de facturation).

## Étendre / prochaines étapes possibles

- Export CSV/Excel d'une plage de données pour construire les décomptes de
  charges.
- Alertes (email/notification) si un miniserver ne répond plus
  (`last_poll_ok` dans `/health`).
- Authentification sur le dashboard web si le Pi est exposé au-delà du
  réseau local (actuellement pensé pour un usage strictement LAN).
