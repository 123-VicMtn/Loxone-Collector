"""
Tests de caractérisation de l'API : pour chaque route, le cas nominal, le
401 sans session, le 403 d'un compte `user` hors de son site ou d'une
action d'admin, le 400 sur un paramètre invalide. Ils figent le
comportement actuel avant le découpage d'app.py en Blueprints
(docs/analyse-maintenabilite-2026-10.md, étape 2).

Le compte `client` n'a accès qu'au site « Ref » ; « Autre » existe mais ne
lui est pas accordé (fixtures dans conftest.py).
"""
from unittest import mock

import pytest

import site_reference as ref
from loxone_collector import db
from loxone_collector.api import errors

IDS = ref.ids_par_cle()
SERIE_REF = IDS["APP1:c"]
SERIE_AUTRE = f"{ref.AUTRE}:u1:total"
OCTOBRE = (ref.ts(2026, 10, 1), ref.ts(2026, 11, 1))

ROUTES_CONNECTEES = [
    ("get", "/api/miniservers"),
    ("get", "/api/resource-types"),
    ("get", "/api/collecte"),
    ("get", "/api/series"),
    ("get", "/api/releves"),
    ("get", f"/api/series/{SERIE_REF}/data"),
    ("get", f"/api/series/{SERIE_REF}/latest"),
    ("get", f"/api/series/{SERIE_REF}/daily"),
    ("get", f"/api/series/{SERIE_REF}/range?from={OCTOBRE[0]}&to={OCTOBRE[1]}"),
    ("post", "/api/apercu/puissance"),
    ("get", "/api/decompte"),
    ("get", "/api/decompte/export.csv?mois=2026-10"),
    ("get", "/api/decompte/export.xlsx?mois=2026-10"),
    ("get", "/api/tarifs"),
    ("post", "/api/tarifs"),
    ("delete", "/api/tarifs/1"),
    ("post", f"/api/series/{SERIE_REF}/classify"),
    ("post", "/api/logout"),
]


@pytest.mark.parametrize("methode,url", ROUTES_CONNECTEES)
def test_sans_session_401(client_anonyme, methode, url):
    res = getattr(client_anonyme, methode)(url)
    assert res.status_code == 401
    assert res.get_json() == {"error": "unauthorized"}


# --- Authentification -------------------------------------------------------

def test_login_refuse_401(client_anonyme):
    res = client_anonyme.post("/api/login", json={"username": "admin", "password": "faux"})
    assert res.status_code == 401
    assert res.get_json() == {"error": "identifiants invalides"}


def test_login_sans_champs_400(client_anonyme):
    assert client_anonyme.post("/api/login", json={}).status_code == 400


def test_me(client_anonyme, client_user, client_admin):
    assert client_anonyme.get("/api/me").status_code == 401
    assert client_user.get("/api/me").get_json() == {
        "username": "client", "role": "user", "miniservers": [ref.SITE],
    }
    assert client_admin.get("/api/me").get_json()["miniservers"] == [ref.SITE, ref.AUTRE]


def test_logout_coupe_la_session(client_user):
    assert client_user.post("/api/logout").get_json() == {"ok": True}
    assert client_user.get("/api/me").status_code == 401


def test_health_public(client_anonyme):
    data = client_anonyme.get("/health").get_json()
    # « Autre » : dernier relevé 2 h avant NOW, au-delà du seuil de 60 min.
    assert data["status"] == "degrade"
    assert {s["miniserver"]: s["en_retard"] for s in data["collecte"]} == {
        ref.SITE: False, ref.AUTRE: True,
    }
    assert "last_error" not in data


# --- Sites et séries ---------------------------------------------------------

def test_miniservers_scopes_au_compte(client_user, client_admin):
    assert client_user.get("/api/miniservers").get_json() == [ref.SITE]
    assert client_admin.get("/api/miniservers").get_json() == [ref.SITE, ref.AUTRE]


def test_resource_types(client_user):
    assert "energie_reseau" in client_user.get("/api/resource-types").get_json()


def test_series_et_releves_scopes_au_compte(client_user, client_admin):
    for url in ("/api/series", "/api/releves"):
        sites_user = {s["miniserver"] for s in client_user.get(url).get_json()}
        sites_admin = {s["miniserver"] for s in client_admin.get(url).get_json()}
        assert sites_user == {ref.SITE}
        assert sites_admin == {ref.SITE, ref.AUTRE}


def test_releves_dernier_index(client_user):
    rows = {r["series_id"]: r for r in client_user.get("/api/releves").get_json()}
    assert rows[SERIE_REF]["value"] == 2003.0
    assert rows[SERIE_REF]["ts"] == ref.NOW


@pytest.mark.parametrize("suffixe", [
    "data", "latest", "daily", f"range?from={OCTOBRE[0]}&to={OCTOBRE[1]}",
])
def test_serie_dun_autre_site_403(client_user, client_admin, suffixe):
    assert client_user.get(f"/api/series/{SERIE_AUTRE}/{suffixe}").status_code == 403
    assert client_admin.get(f"/api/series/{SERIE_AUTRE}/{suffixe}").status_code == 200


def test_serie_data(client_user):
    data = client_user.get(f"/api/series/{SERIE_REF}/data?range=24h").get_json()
    assert [p["value"] for p in data["points"]] == [2000.0, 2001.0, 2002.0, 2003.0]


@pytest.mark.parametrize("query", ["range=2h", "start=a&end=b"])
def test_serie_data_parametres_invalides_400(client_user, query):
    assert client_user.get(f"/api/series/{SERIE_REF}/data?{query}").status_code == 400


def test_serie_latest(client_user):
    assert client_user.get(f"/api/series/{SERIE_REF}/latest").get_json() == {
        "series_id": SERIE_REF, "ts": ref.NOW, "value": 2003.0,
    }


def test_serie_daily_parametre_invalide_400(client_user):
    assert client_user.get(f"/api/series/{SERIE_REF}/daily?days=x").status_code == 400


def test_serie_range(client_user):
    data = client_user.get(
        f"/api/series/{SERIE_REF}/range?from={OCTOBRE[0]}&to={OCTOBRE[1]}"
    ).get_json()
    assert data["series_id"] == SERIE_REF
    assert set(data) >= {"from", "to", "kwh"}


@pytest.mark.parametrize("query", ["", "from=1&to=1", "from=a&to=2"])
def test_serie_range_parametres_invalides_400(client_user, query):
    assert client_user.get(f"/api/series/{SERIE_REF}/range?{query}").status_code == 400


# --- Puissance de l'aperçu (validation seulement : la lecture va au Miniserver)

@pytest.mark.parametrize("corps", [
    {"miniserver": ref.SITE},
    {"miniserver": ref.SITE, "from": 10, "to": 5, "series_ids": []},
    {"miniserver": ref.SITE, "from": 0, "to": 49 * 3600, "series_ids": []},
    {"miniserver": ref.SITE, "from": 0, "to": 10, "series_ids": "x"},
    {"miniserver": ref.SITE, "from": 0, "to": 10, "series_ids": ["inconnue"]},
    {"miniserver": ref.SITE, "from": 0, "to": 10, "series_ids": [SERIE_REF]},
])
def test_puissance_parametres_invalides_400(client_user, corps):
    assert client_user.post("/api/apercu/puissance", json=corps).status_code == 400


def test_puissance_site_non_accorde_403(client_user):
    corps = {"miniserver": ref.AUTRE, "from": 0, "to": 10, "series_ids": []}
    assert client_user.post("/api/apercu/puissance", json=corps).status_code == 403


def test_puissance_sans_serie(client_user):
    corps = {"miniserver": ref.SITE, "from": 0, "to": 10, "series_ids": []}
    assert client_user.post("/api/apercu/puissance", json=corps).get_json() == {
        "miniserver": ref.SITE, "from": 0, "to": 10, "points": {}, "missing": [],
    }


# --- Décompte et exports -------------------------------------------------------

@pytest.mark.parametrize("url", [
    "/api/decompte",
    "/api/decompte/export.csv?mois=2026-10",
    "/api/decompte/export.xlsx?mois=2026-10",
    "/api/tarifs",
])
def test_decompte_site_non_accorde_ou_inconnu_meme_reponse(client_user, client_admin, url):
    # Un user ne doit pas pouvoir distinguer un site inexistant d'un site
    # d'un autre client ; l'admin, si (faute de frappe).
    sep = "&" if "?" in url else "?"
    for site in (ref.AUTRE, "Inconnu"):
        res = client_user.get(f"{url}{sep}miniserver={site}")
        assert res.status_code == 403
        assert res.get_json() == {"error": f"accès refusé au site {site!r}"}
    res = client_admin.get(f"{url}{sep}miniserver=Inconnu")
    assert res.status_code == 400
    assert res.get_json() == {"error": "site inconnu : 'Inconnu'"}
    assert ref.SITE not in str(res.get_json())


def test_decompte_site_par_defaut(client_user):
    assert client_user.get("/api/decompte").get_json()["miniserver"] == ref.SITE


def test_decompte_bornes(client_user):
    data = client_user.get(
        f"/api/decompte?miniserver={ref.SITE}&from=2026-09&to=2026-10"
    ).get_json()
    assert [p["key"] for p in data["periodes"]] == ["2026-09", "2026-10"]


@pytest.mark.parametrize("query", ["from=2026-13", "to=x", "from=2026-10&to=2026-09"])
def test_decompte_bornes_invalides_400(client_user, query):
    assert client_user.get(f"/api/decompte?miniserver={ref.SITE}&{query}").status_code == 400


@pytest.mark.parametrize("fmt", ["csv", "xlsx"])
def test_export_mois_invalide_400(client_user, fmt):
    assert client_user.get(f"/api/decompte/export.{fmt}?miniserver={ref.SITE}").status_code == 400
    assert client_user.get(
        f"/api/decompte/export.{fmt}?miniserver={ref.SITE}&mois=2026-13"
    ).status_code == 400


@pytest.mark.parametrize("fmt,mime", [
    ("csv", "text/csv"),
    ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
])
def test_export_ouvert_a_un_user(client_user, fmt, mime):
    res = client_user.get(f"/api/decompte/export.{fmt}?miniserver={ref.SITE}&mois=2026-10")
    assert res.status_code == 200
    assert res.mimetype == mime
    assert f"decompte-{ref.SITE}-2026-10.{fmt}" in res.headers["Content-Disposition"]


# --- Écritures : admin seulement ------------------------------------------------

TARIF = {"miniserver": ref.SITE, "valid_from": "2026-01-01",
         "prix_reseau": 0.3, "prix_solaire": 0.2, "taux_tva": 8.1}


def test_tarifs_lecture_user_ecriture_admin(client_user, client_admin):
    assert client_user.get(f"/api/tarifs?miniserver={ref.SITE}").get_json() == []
    assert client_user.post("/api/tarifs", json=TARIF).status_code == 403
    tarifs = client_admin.post("/api/tarifs", json=TARIF).get_json()
    assert [(t["valid_from"], t["prix_reseau"]) for t in tarifs] == [("2026-01-01", 0.3)]
    tarif_id = tarifs[0]["id"]
    assert client_user.delete(f"/api/tarifs/{tarif_id}?miniserver={ref.SITE}").status_code == 403
    assert client_admin.delete(f"/api/tarifs/{tarif_id}?miniserver={ref.SITE}").get_json() == []


@pytest.mark.parametrize("modif", [
    {"valid_from": "01.01.2026"},
    {"prix_reseau": "cher"},
    {"taux_tva": -1},
])
def test_tarifs_invalides_400(client_admin, modif):
    assert client_admin.post("/api/tarifs", json={**TARIF, **modif}).status_code == 400


def test_classification_admin_seulement(client_user, client_admin):
    url = f"/api/series/{SERIE_REF}/classify"
    assert client_user.post(url, json={"apartment": "APP9"}).status_code == 403
    assert client_admin.post(url, json={}).status_code == 400
    assert client_admin.post(url, json={"apartment": "APP9"}).get_json() == {"ok": True}
    serie = next(s for s in client_admin.get("/api/series").get_json() if s["series_id"] == SERIE_REF)
    assert (serie["apartment"], serie["apartment_manual"]) == ("APP9", 1)
    assert client_admin.post(url, json={"reset": True}).get_json() == {"ok": True}
    serie = next(s for s in client_admin.get("/api/series").get_json() if s["series_id"] == SERIE_REF)
    assert serie["apartment_manual"] == 0


# --- Erreurs en JSON ------------------------------------------------------------

def test_erreur_de_parametre_en_json(client_user):
    res = client_user.get(f"/api/series/{SERIE_REF}/daily?days=x")
    assert res.status_code == 400
    assert res.get_json() == {"error": "days doit être un entier"}


def test_route_inconnue_et_methode_en_json(client_user):
    res = client_user.get("/api/nexiste-pas")
    assert (res.status_code, res.get_json()) == (404, {"error": "ressource introuvable"})
    res = client_user.put("/api/series")
    assert (res.status_code, res.get_json()) == (405, {"error": "méthode non autorisée"})


def test_erreur_imprevue_sans_detail(client_user):
    with mock.patch.object(db, "list_series", side_effect=RuntimeError("/srv/secret.db verrouillée")):
        res = client_user.get("/api/series")
    assert res.status_code == 500
    assert res.get_json() == {"error": errors.MESSAGE_ERREUR_INTERNE}
