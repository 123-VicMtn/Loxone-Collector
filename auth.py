"""
auth.py
-------
Authentification par cookie de session (Flask-Login) -- voir
docs/plan-installation-auth-frontend-docker.md, "Étape 1". Le backend
étant 100% API (voir CLAUDE.md), il n'y a pas de page de login Jinja ici :
`app.py` expose `/api/login`/`/api/logout`/`/api/me` en JSON, consommés par
la page de connexion du frontend Vue. `unauthorized_handler` renvoie donc
un 401 JSON plutôt que la redirection HTTP par défaut de Flask-Login (faite
pour des pages, pas une API).

Pas de gestion de rôles : tous les comptes créés via
scripts/create_admin_user.py ont les mêmes droits -- suffisant pour 1-3
utilisateurs connus (voir "Ce qu'on ne fait pas" dans le plan).
"""

from __future__ import annotations

from contextlib import closing

from flask import jsonify
from flask_login import LoginManager, UserMixin
from werkzeug.security import check_password_hash

import db

login_manager = LoginManager()


@login_manager.unauthorized_handler
def _unauthorized():
    return jsonify({"error": "unauthorized"}), 401


class User(UserMixin):
    def __init__(self, id_: int, username: str):
        self.id = id_
        self.username = username


def _read_conn():
    # Import différé : évite un cycle (app.py importe auth, auth importerait
    # app pour sa config sinon). login_manager.init_app(app) dans app.py
    # rend la config accessible via flask.current_app.
    from flask import current_app
    return db.get_connection(current_app.config["LOXONE_CFG"].db_path)


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    with closing(_read_conn()) as conn:
        row = db.get_user_by_id(conn, int(user_id))
    return User(row["id"], row["username"]) if row else None


def verify_login(username: str, password: str) -> User | None:
    with closing(_read_conn()) as conn:
        row = db.get_user_by_username(conn, username)
    if row and check_password_hash(row["password_hash"], password):
        return User(row["id"], row["username"])
    return None
