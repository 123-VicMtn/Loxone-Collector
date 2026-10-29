"""
Fixtures communes des tests de route et de caractérisation.

- `base_reference` : la base du site synthétique (tests/site_reference.py),
  construite une fois par session ;
- `app_test` : l'application Flask sur une copie de cette base, sans poller
  ni Miniserver ; l'heure des routes (`access.maintenant`) est figée à
  `site_reference.NOW` et la synchro Statistics ne fait rien (les
  Statistics sont déjà en base) ;
- `client_admin`, `client_user` (accès au seul site « Ref »), `client_anonyme`.
"""
import shutil
from contextlib import closing
from unittest import mock

import pytest
from werkzeug.security import generate_password_hash

import site_reference as ref
from loxone_collector import app as app_module
from loxone_collector import db
from loxone_collector.api import access, auth, tentatives
from loxone_collector.config import AppConfig, MiniserverConfig
from loxone_collector.services import statistics_sync

MOT_DE_PASSE = "secret123"


def _miniserver(name):
    return MiniserverConfig(name=name, host="localhost", username="u", password="p")


@pytest.fixture(scope="session")
def base_reference(tmp_path_factory):
    chemin = tmp_path_factory.mktemp("reference") / "reference.db"
    with closing(db.get_connection(chemin)) as conn:
        ref.construire(conn)
        # Peu d'itérations : chaque test se reconnecte, et ce n'est pas la
        # robustesse du hachage qui est testée ici.
        hash_ = generate_password_hash(MOT_DE_PASSE, method="pbkdf2:sha256:1000")
        db.create_user(conn, "admin", hash_, role="admin")
        db.create_user(conn, "client", hash_, role="user")
        db.grant_miniserver(conn, db.get_user_by_username(conn, "client")["id"], ref.SITE)
        db.checkpoint_wal(conn)
    return chemin


@pytest.fixture
def app_test(base_reference, tmp_path):
    chemin = tmp_path / "test.db"
    shutil.copy(base_reference, chemin)
    cfg = AppConfig(
        db_path=str(chemin),
        miniservers=[_miniserver(ref.SITE), _miniserver(ref.AUTRE)],
        statistics_sync_hours=0,
    )
    flask_app = app_module.app
    flask_app.config["LOXONE_CFG"] = cfg
    flask_app.config["TESTING"] = True
    flask_app.secret_key = "test"
    if not getattr(flask_app, "login_manager", None):
        auth.login_manager.init_app(flask_app)
    tentatives.limiteur.vider()

    sync_ok = {"ok": True, "erreur": None, "series": 0, "points": 0}
    with mock.patch.object(statistics_sync, "sync_decompte", return_value=sync_ok), \
            mock.patch.dict(statistics_sync.derniers_resultats, clear=True), \
            mock.patch.object(access, "maintenant", return_value=ref.NOW):
        yield flask_app


def _connecte(flask_app, username):
    client = flask_app.test_client()
    res = client.post("/api/login", json={"username": username, "password": MOT_DE_PASSE})
    assert res.status_code == 200, res.get_data(as_text=True)
    return client


@pytest.fixture
def client_admin(app_test):
    return _connecte(app_test, "admin")


@pytest.fixture
def client_user(app_test):
    return _connecte(app_test, "client")


@pytest.fixture
def client_anonyme(app_test):
    return app_test.test_client()
