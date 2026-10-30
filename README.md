# Loxone Collector

Collecte les compteurs de plusieurs Miniservers Loxone (électricité, eau,
chauffage), les garde en SQLite et établit les décomptes de charges par
appartement : part réseau et part solaire autoconsommée, chauffage, eau
chaude, buanderie. Export Excel et CSV, sur un mois ou des dates libres.

Deux parties indépendantes :

- **`backend/`** : API JSON en Python (Flask). Lit les Miniservers, stocke
  les relevés, calcule les décomptes.
- **`frontend/`** : application web (Vue 3, TypeScript, Tailwind) qui
  consomme l'API : graphiques, liste des compteurs, décompte, classification.

L'historique des décisions et le détail du fonctionnement sont dans
[`CLAUDE.md`](CLAUDE.md).

## Prérequis

- Python 3.9 ou plus récent
- Node.js 20.19 ou 22.12 et plus récent (pour le frontend)
- Un accès aux Miniservers : en réseau local, ou à distance par le Cloud DNS
  Loxone. Idéalement avec un utilisateur Loxone en lecture seule.

## Installation

Depuis la racine du dépôt :

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-websocket.txt
pip install --no-deps pyloxone-api==0.2.4   # voir backend/requirements-websocket.txt
pip install -e backend

npm --prefix frontend install
```

Configuration, dans `backend/` :

```bash
cd backend
cp config.example.yaml config.yaml   # sites à suivre
cp .env.example .env                 # identifiants et SECRET_KEY
python3 -c "import secrets; print(secrets.token_hex(32))"   # valeur de SECRET_KEY
```

Les mots de passe ne vont jamais dans `config.yaml` : on y écrit `${NOM}`,
et la valeur va dans `.env` (non versionné).

Créer un compte pour se connecter (pas d'inscription en ligne) :

```bash
python3 scripts/create_admin_user.py config.yaml
```

Deux rôles : `admin` (tous les sites, modifications) et `user` (lecture
seule, sites accordés). Le même script liste, supprime et donne accès aux
sites (`--help`).

## Lancer en développement

Deux terminaux :

```bash
# 1. API, depuis backend/
python3 app.py config.yaml

# 2. Frontend, depuis la racine
npm --prefix frontend run dev        # http://localhost:5173
```

Le frontend redirige `/api` et `/health` vers l'API. L'adresse se règle
dans `frontend/.env.local` (`VITE_API_PROXY_TARGET`, par défaut
`http://localhost:5050`).

L'API ne recharge pas le code toute seule : la redémarrer après chaque
modification Python ou de configuration.

### Sans Miniserver : la démo

```bash
cd backend
python3 scripts/seed_demo_data.py config.demo.yaml   # données synthétiques
python3 scripts/create_admin_user.py config.demo.yaml
python3 app.py config.demo.yaml                      # API sur le port 8082
```

## Tests

```bash
cd backend
../.venv/bin/ruff check .
../.venv/bin/python3 -m pytest -q

npm --prefix ../frontend run build   # vérifie aussi les types
```

## Scripts utiles (`backend/scripts/`)

| Script | Usage |
|---|---|
| `create_admin_user.py` | Gérer les comptes et leurs sites |
| `backfill_statistics.py` | Importer l'historique d'un Miniserver (combler un trou de collecte : `--since AAAA-MM-JJ`) |
| `diagnose_auth.py`, `diagnose_websocket.py` | Tester la connexion à un Miniserver |
| `check_statistics.py` | Voir quels compteurs ont un historique sur le Miniserver |
| `audit_repartition.py` | Dossier de preuve quand la répartition solaire de Loxone est fausse |
| `vacuum_db.py` | Compacter la base (manuel, une fois par mois) |

## Production

- **API** : `python -m loxone_collector.serve config.yaml` (waitress, un
  seul process : le poller tourne dedans), derrière un reverse proxy HTTPS
  (`trusted_proxy` et `cookie_secure: true` dans `config.yaml`).
- **Frontend** : `npm --prefix frontend run build`, puis servir
  `frontend/dist/` comme un site statique. Le serveur doit renvoyer
  `index.html` pour toute route inconnue, et transmettre `/api` et
  `/health` à l'API.
- `GET /health` (public) répond `{"status": "ok"}` ou `"degrade"` si un
  site ne remonte plus, pour une supervision externe.

Le déploiement sur un VPS avec Docker et Caddy est en préparation.
