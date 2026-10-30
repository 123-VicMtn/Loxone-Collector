"""
app.py
------
Point d'entrée de l'application : enregistre les routes de l'API JSON
(`api/routes/`, un Blueprint par domaine) et lance les tâches de fond
(`workers/poller.py`). L'API est consommée par le frontend Vue
(`frontend/`, app autonome -- voir CLAUDE.md "Backend 100% API") ; Flask
ne sert aucune page HTML lui-même.

Lancement (dev), depuis backend/ (config, .env et data/ y sont lus) :
    python app.py config.yaml
ou, sans passer par le lanceur backend/app.py :
    python -m loxone_collector.app config.yaml

Lancement (prod) : `python -m loxone_collector.serve config.yaml`
(waitress, un seul process : voir serve.py).
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from flask import Flask

from loxone_collector import db
from loxone_collector.api import auth
from loxone_collector.api.errors import register_error_handlers
from loxone_collector.api.routes import admin as routes_admin
from loxone_collector.api.routes import auth as routes_auth
from loxone_collector.api.routes import decompte as routes_decompte
from loxone_collector.api.routes import health as routes_health
from loxone_collector.api.routes import series as routes_series
from loxone_collector.config import ConfigError, load_config
from loxone_collector.workers import poller

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app")

# Une session reste ouverte 7 jours sans activité (décision de l'utilisateur,
# 2026-10-29) ; chaque requête repousse l'échéance.
SESSION_DUREE = timedelta(days=7)

app = Flask(__name__)
app.config.update(
    PERMANENT_SESSION_LIFETIME=SESSION_DUREE,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
for _bp in (routes_auth.bp, routes_health.bp, routes_series.bp, routes_decompte.bp, routes_admin.bp):
    app.register_blueprint(_bp)
register_error_handlers(app)


def create_app(config_path: str = "config.yaml") -> Flask:
    cfg = load_config(config_path)  # charge aussi .env (voir config.load_config)
    app.config["LOXONE_CFG"] = cfg
    # Crée la base + le schéma tout de suite, y compris si le poller n'a pas
    # encore tourné (évite une erreur 500 sur un dashboard vide au premier
    # démarrage) -- crée aussi la table `users` au passage (voir db.SCHEMA).
    db.get_connection(cfg.db_path).close()

    if "SECRET_KEY" not in os.environ:
        raise ConfigError(
            "Variable d'environnement 'SECRET_KEY' absente (nécessaire pour "
            "signer les cookies de session -- vérifie ton fichier .env, voir "
            ".env.example). Générer une valeur : "
            "python3 -c \"import secrets; print(secrets.token_hex(32))\""
        )
    app.secret_key = os.environ["SECRET_KEY"]
    # Derrière HTTPS (prod) : le cookie ne part jamais en clair.
    app.config["SESSION_COOKIE_SECURE"] = cfg.cookie_secure
    auth.login_manager.init_app(app)

    if cfg.collecte_active:
        poller.start_background_poller(cfg)
    else:
        logger.warning("Collecte désactivée (collecte_active: false) : les Miniservers ne sont pas interrogés.")
    return app


def main(argv: list[str] | None = None) -> None:
    import sys
    argv = sys.argv[1:] if argv is None else argv
    config_path = argv[0] if argv else "config.yaml"
    flask_app = create_app(config_path)
    cfg = flask_app.config["LOXONE_CFG"]
    flask_app.run(host=cfg.host_bind, port=cfg.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
