"""
État de la collecte (`/api/collecte`, `/health`) : un site qui ne remonte
plus doit se voir. Horizon est resté muet 11 jours (16-27.10.2026) sans que
rien ne le signale.

Tests de route avec le client de test Flask, sur une base temporaire et une
config construite ici (pas de poller, pas de Miniserver).
"""
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
from config import AppConfig, MiniserverConfig  # noqa: E402

MOT_DE_PASSE = "secret123"


def _miniserver(name):
    return MiniserverConfig(name=name, host="localhost", username="u", password="p")


class _AppDeTest:
    """Base temporaire, trois sites (Vif, Muet, Neuf), un admin et un
    user qui n'a accès qu'à Muet. Pas un TestCase : chaque classe de tests
    l'hérite avec unittest.TestCase."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg = AppConfig(
            db_path=str(Path(cls.tmp.name) / "test.db"),
            miniservers=[_miniserver("Vif"), _miniserver("Muet"), _miniserver("Neuf")],
            stale_alert_minutes=60,
        )
        flask_app = app_module.app
        flask_app.config["LOXONE_CFG"] = cls.cfg
        flask_app.config["TESTING"] = True
        flask_app.secret_key = "test"
        if not getattr(flask_app, "login_manager", None):
            auth.login_manager.init_app(flask_app)
        cls.flask_app = flask_app

        now = int(time.time())
        with closing(db.get_connection(cls.cfg.db_path)) as conn:
            for ms in ("Vif", "Muet"):
                db.upsert_series_meta(conn, f"{ms}:u1:total", ms, "u1", "total", "Compteur (total)",
                                      "", "", "Meter", "kWh")
            db.insert_readings_batch(conn, [
                ("Vif:u1:total", now - 300, 1.0, None),
                ("Muet:u1:total", now - 3 * 86400, 1.0, None),
            ])
            hash_ = generate_password_hash(MOT_DE_PASSE, method="pbkdf2:sha256")
            db.create_user(conn, "admin", hash_, role="admin")
            db.create_user(conn, "client", hash_, role="user")
            client_id = db.get_user_by_username(conn, "client")["id"]
            db.grant_miniserver(conn, client_id, "Muet")
            conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _client(self, username):
        client = self.flask_app.test_client()
        res = client.post("/api/login", json={"username": username, "password": MOT_DE_PASSE})
        self.assertEqual(res.status_code, 200)
        return client


class TestCollecte(_AppDeTest, unittest.TestCase):
    def test_admin_voit_tous_les_sites_et_leur_retard(self):
        data = self._client("admin").get("/api/collecte").get_json()
        self.assertEqual(data["seuil_minutes"], 60)
        sites = {s["miniserver"]: s for s in data["sites"]}
        self.assertEqual(list(sites), ["Vif", "Muet", "Neuf"])
        self.assertFalse(sites["Vif"]["en_retard"])
        self.assertTrue(sites["Muet"]["en_retard"])
        # Site configuré mais jamais relevé : en retard, sans date.
        self.assertTrue(sites["Neuf"]["en_retard"])
        self.assertIsNone(sites["Neuf"]["dernier_releve_ts"])
        self.assertNotIn("derniere_erreur", sites["Vif"])

    def test_user_ne_voit_que_ses_sites(self):
        data = self._client("client").get("/api/collecte").get_json()
        self.assertEqual([s["miniserver"] for s in data["sites"]], ["Muet"])

    def test_collecte_demande_une_session(self):
        res = self.flask_app.test_client().get("/api/collecte")
        self.assertEqual(res.status_code, 401)

    def test_health_degrade_si_un_site_ne_remonte_plus(self):
        res = self.flask_app.test_client().get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "degrade")
        self.assertEqual(
            {s["miniserver"]: s["en_retard"] for s in data["collecte"]},
            {"Vif": False, "Muet": True, "Neuf": True},
        )
        # Public : pas les messages d'erreur des polls.
        self.assertNotIn("last_error", data)


class TestErreurStatistiques(_AppDeTest, unittest.TestCase):
    """Miniserver injoignable pendant /api/decompte : l'utilisateur lit une
    phrase, jamais l'exception (adresse, port, pile réseau)."""

    ERREUR = "[Muet] échec de connexion à https://10.0.0.1:443/jdev/sps/getStatistic: refused"

    def _decompte(self, username):
        retour = {"ok": False, "erreur": self.ERREUR, "series": 0, "points": 0}
        with mock.patch.object(app_module.miniserver_statistics, "sync_decompte", return_value=retour):
            res = self._client(username).get("/api/decompte?miniserver=Muet")
        self.assertEqual(res.status_code, 200)
        return res.get_json()

    def test_message_court_sans_detail_technique(self):
        for compte in ("client", "admin"):
            data = self._decompte(compte)
            self.assertEqual(data["statistiques_erreur"], app_module.MESSAGE_STATISTIQUES_INJOIGNABLES)
            self.assertNotIn("10.0.0.1", str(data))


class TestDernierReleve(unittest.TestCase):
    def test_repli_sur_lhoraire_quand_le_brut_est_purge(self):
        conn = db.get_connection(":memory:")
        db.upsert_series_meta(conn, "A:u:total", "A", "u", "total", "X", "", "", "Meter", "kWh")
        db.upsert_series_meta(conn, "A:v:total", "A", "v", "total", "Y", "", "", "Meter", "kWh")
        db.insert_readings_batch(conn, [("A:u:total", 100, 1.0, None)])
        conn.execute(
            "INSERT INTO readings_hourly (series_id, ts, avg_value) VALUES ('A:v:total', 7200, 1.0)"
        )
        self.assertEqual(db.last_reading_by_miniserver(conn), {"A": 7200})
        conn.close()


if __name__ == "__main__":
    unittest.main()
