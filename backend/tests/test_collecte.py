"""
État de la collecte (`/api/collecte`, `/health`) : un site qui ne remonte
plus doit se voir. Horizon est resté muet 11 jours (16-27.10.2026) sans que
rien ne le signale. Et aucune erreur système ne doit atteindre l'interface.

Fixtures dans conftest.py : « Ref » relevé jusqu'à NOW, « Autre » 2 h avant.
"""
from unittest import mock

import app as app_module
import db
import site_reference as ref


def test_admin_voit_tous_les_sites_et_leur_retard(client_admin):
    data = client_admin.get("/api/collecte").get_json()
    assert data["seuil_minutes"] == 60
    assert data["sites"] == [
        {"miniserver": ref.SITE, "dernier_releve_ts": ref.NOW, "en_retard": False},
        {"miniserver": ref.AUTRE, "dernier_releve_ts": ref.NOW - 7200, "en_retard": True},
    ]


def test_user_ne_voit_que_ses_sites(client_user):
    data = client_user.get("/api/collecte").get_json()
    assert [s["miniserver"] for s in data["sites"]] == [ref.SITE]


def test_site_jamais_releve_en_retard(app_test, client_admin):
    cfg = app_test.config["LOXONE_CFG"]
    cfg.miniservers.append(type(cfg.miniservers[0])(name="Neuf", host="h", username="u", password="p"))
    sites = {s["miniserver"]: s for s in client_admin.get("/api/collecte").get_json()["sites"]}
    assert sites["Neuf"] == {"miniserver": "Neuf", "dernier_releve_ts": None, "en_retard": True}


def test_message_statistiques_court_sans_detail_technique(client_user, client_admin):
    erreur = "[Ref] échec de connexion à https://10.0.0.1:443/jdev/sps/getStatistic: refused"
    retour = {"ok": False, "erreur": erreur, "series": 0, "points": 0}
    with mock.patch.object(app_module.miniserver_statistics, "sync_decompte", return_value=retour):
        for client in (client_user, client_admin):
            data = client.get(f"/api/decompte?miniserver={ref.SITE}").get_json()
            assert data["statistiques_erreur"] == app_module.MESSAGE_STATISTIQUES_INJOIGNABLES
            assert "10.0.0.1" not in str(data)


def test_repli_sur_lhoraire_quand_le_brut_est_purge():
    conn = db.get_connection(":memory:")
    db.upsert_series_meta(conn, "A:u:total", "A", "u", "total", "X", "", "", "Meter", "kWh")
    db.upsert_series_meta(conn, "A:v:total", "A", "v", "total", "Y", "", "", "Meter", "kWh")
    db.insert_readings_batch(conn, [("A:u:total", 100, 1.0, None)])
    conn.execute(
        "INSERT INTO readings_hourly (series_id, ts, avg_value) VALUES ('A:v:total', 7200, 1.0)"
    )
    assert db.last_reading_by_miniserver(conn) == {"A": 7200}
    conn.close()
