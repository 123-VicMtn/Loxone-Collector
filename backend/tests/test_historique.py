"""
Historique mensuel des graphiques (GET /api/historique,
services/historique.py) : mêmes chiffres que le décompte pour un mois
complet ; premier mois rogné au début des relevés plutôt que vide ; site
sans part réseau / solaire par lot (Horizon) lu sur ses compteurs de lot.
"""
from contextlib import closing

import site_reference as ref
from loxone_collector import db


def _historique(client):
    res = client.get(f"/api/historique?miniserver={ref.SITE}")
    assert res.status_code == 200
    return {m["key"]: m for m in res.get_json()["mois"]}


def _sql(app_test, requete, *params):
    with closing(db.get_connection(app_test.config["LOXONE_CFG"].db_path)) as conn:
        conn.execute(requete, params)
        conn.commit()


def test_memes_chiffres_que_le_decompte(client_user):
    mois = _historique(client_user)
    decompte = client_user.get(f"/api/decompte?miniserver={ref.SITE}").get_json()
    # Août : relevés depuis le 31.08 à 22 h, aucun jour mesuré. Novembre :
    # deux heures de relevés, signalé incomplet.
    assert [p["key"] for p in decompte["periodes"]] == ["2026-08", "2026-09", "2026-10", "2026-11"]
    assert list(mois) == ["2026-09", "2026-10", "2026-11"]
    assert mois["2026-11"]["partiel"] and mois["2026-11"]["releves_arretes"]
    assert not mois["2026-10"]["releves_arretes"]
    for cle in ("2026-09", "2026-10"):
        m = mois[cle]
        b = decompte["batiment"]["periodes"][cle]
        assert not m["partiel"]
        assert m["consommation"] == b["consommation_totale"]
        assert m["production"] == b["production"]["kwh"]
        assert m["autoconsommation"] == b["autoconsommation"]
        assert m["injection"] == b["injection"]


def test_premier_mois_rogne_au_debut_des_releves(app_test, client_user):
    """Relevés à partir du 10.09 : septembre part du 11.09 (un relevé doit
    précéder le début) et a des chiffres ; le décompte, lui, n'en a pas."""
    _sql(app_test, "DELETE FROM statistic_samples WHERE ts < ?", ref.ts(2026, 9, 10))
    septembre = _historique(client_user)["2026-09"]
    assert septembre["partiel"]
    assert septembre["debut_ts"] == ref.ts(2026, 9, 11)
    assert septembre["consommation"] > 0
    assert septembre["production"] > 0

    decompte = client_user.get(f"/api/decompte?miniserver={ref.SITE}").get_json()
    assert decompte["batiment"]["periodes"]["2026-09"]["consommation_totale"] is None


def test_site_sans_part_reseau_solaire(app_test, client_user):
    """Un seul compteur par lot (Horizon) : consommation = somme des
    compteurs de lot, pas d'autoconsommation ni d'injection."""
    _sql(app_test, "DELETE FROM series_meta WHERE apartment != '' "
                   "AND (label LIKE '%Grid%' OR label LIKE '%Solaire%')")
    mois = _historique(client_user)
    decompte = client_user.get(f"/api/decompte?miniserver={ref.SITE}").get_json()
    septembre = mois["2026-09"]
    lots = [z["periodes"]["2026-09"]["controle"]["kwh"] for z in decompte["zones"]]
    assert len(lots) == len(ref.ZONES)
    assert septembre["consommation"] == sum(lots)
    assert septembre["autoconsommation"] is None and septembre["injection"] is None
    assert septembre["production"] is not None
    # Octobre : le compteur de l'APP2 est remplacé le 20 (rupture), le total
    # reste vide plutôt que trop bas.
    assert mois["2026-10"]["consommation"] is None


def test_historique_site_non_accorde_403(client_user):
    assert client_user.get(f"/api/historique?miniserver={ref.AUTRE}").status_code == 403
