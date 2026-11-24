"""Routes de la page Paramètres (api/routes/parametres.py) : lecture,
ajout et suppression de versions, frais accessoires, et droits (le `user`
modifie son site, l'admin seul les valeurs globales). Le compte `client`
n'a accès qu'au site « Ref » (conftest.py)."""
from contextlib import closing

import site_reference as ref
from loxone_collector import db

PRIX = {"miniserver": ref.SITE, "cle": "prix_reseau", "valid_from": "2026-11-01", "valeur": "0,25"}
TVA_GLOBALE = {"miniserver": ref.SITE, "global": True, "cle": "taux_tva",
               "valid_from": "2024-01-01", "valeur": "8,1"}
FRAIS = {"miniserver": ref.SITE, "du": "2026-10-01", "au": "2026-10-31", "zone": "APP1",
         "libelle": "Entretien", "montant_ht": "45,50", "tva": True}


def _version(etat, cle):
    """Version la plus récente de `cle` (le site « Ref » a déjà des
    paramètres depuis le 01.01.2026)."""
    return max((v for v in etat["versions"] + etat["globales"] if v["cle"] == cle),
               key=lambda v: v["valid_from"])


def test_catalogue(client_user):
    res = client_user.get("/api/parametres/catalogue")
    assert res.status_code == 200
    cles = [p["cle"] for p in res.get_json()]
    assert cles[:3] == ["prix_reseau", "prix_solaire", "taux_tva"]


def test_user_modifie_les_parametres_de_son_site(client_user):
    res = client_user.post("/api/parametres", json=PRIX)
    assert res.status_code == 200, res.get_json()
    etat = res.get_json()
    assert etat["miniserver"] == ref.SITE and etat["peut_modifier"] is True
    assert etat["peut_modifier_global"] is False
    v = _version(etat, "prix_reseau")
    assert v["valeur"] == 0.25 and v["valid_from"] == "2026-11-01" and v["updated_by"] == "client"
    # Lecture : valeur en vigueur au jour figé des tests.
    lu = client_user.get(f"/api/parametres?miniserver={ref.SITE}").get_json()
    assert lu["aujourdhui"] == "2026-11-05"
    assert lu["en_vigueur"]["prix_reseau"] == {
        "valeur": 0.25, "depuis": "2026-11-01", "origine": "site", "id": v["id"]}


def test_user_ne_modifie_pas_un_autre_site(client_user):
    corps = {**PRIX, "miniserver": ref.AUTRE}
    assert client_user.post("/api/parametres", json=corps).status_code == 403
    assert client_user.get(f"/api/parametres?miniserver={ref.AUTRE}").status_code == 403


def test_valeurs_globales_reservees_a_l_admin(client_user, client_admin):
    assert client_user.post("/api/parametres", json=TVA_GLOBALE).status_code == 403
    res = client_admin.post("/api/parametres", json=TVA_GLOBALE)
    assert res.status_code == 200
    gid = next(v["id"] for v in res.get_json()["globales"] if v["valid_from"] == "2024-01-01")
    # Le user voit la valeur globale héritée, mais ne peut pas la supprimer.
    etat = client_user.get(f"/api/parametres?miniserver={ref.SITE}").get_json()
    assert [v["valeur"] for v in etat["globales"] if v["id"] == gid] == [8.1]
    assert client_user.delete(f"/api/parametres/{gid}?miniserver={ref.SITE}").status_code == 403
    assert client_admin.delete(f"/api/parametres/{gid}?miniserver={ref.SITE}").status_code == 200


def test_valeur_invalide_400(client_user):
    for corps in ({**PRIX, "valeur": "abc"}, {**PRIX, "cle": "prix_borne"},
                  {**PRIX, "valid_from": "01.11.2026"},
                  {**PRIX, "cle": "pac_part_chauffage", "valeur": "120"}):
        res = client_user.post("/api/parametres", json=corps)
        assert res.status_code == 400
        assert res.get_json()["error"]


def test_pas_de_valeur_globale_pour_un_prix(client_admin):
    """Refusée même à l'admin : seule la TVA a une valeur globale."""
    res = client_admin.post("/api/parametres", json={**PRIX, "global": True})
    assert res.status_code == 400


def test_supprimer_une_version(client_user):
    vid = _version(client_user.post("/api/parametres", json=PRIX).get_json(), "prix_reseau")["id"]
    res = client_user.delete(f"/api/parametres/{vid}?miniserver={ref.SITE}")
    assert res.status_code == 200
    assert vid not in [v["id"] for v in res.get_json()["versions"]]
    assert client_user.delete(f"/api/parametres/{vid}?miniserver={ref.SITE}").status_code == 404


def test_version_d_un_autre_site_introuvable(app_test, client_user):
    with closing(db.get_connection(app_test.config["LOXONE_CFG"].db_path)) as conn:
        db.upsert_parametre(conn, ref.AUTRE, "prix_reseau", "2026-11-01", "0.3")
        vid = db.list_parametres(conn, [ref.AUTRE])[0]["id"]
    assert client_user.delete(f"/api/parametres/{vid}?miniserver={ref.SITE}").status_code == 404


# --- Frais accessoires ----------------------------------------------------

def test_frais_ajout_lecture_suppression(client_user):
    res = client_user.post("/api/frais", json=FRAIS)
    assert res.status_code == 200, res.get_json()
    (frais,) = res.get_json()
    assert (frais["zone"], frais["montant_ht"], frais["tva"], frais["updated_by"]) \
        == ("APP1", 45.5, True, "client")
    assert client_user.get(f"/api/frais?miniserver={ref.SITE}").get_json() == [frais]
    res = client_user.delete(f"/api/frais/{frais['id']}?miniserver={ref.SITE}")
    assert res.status_code == 200 and res.get_json() == []
    assert client_user.delete(f"/api/frais/{frais['id']}?miniserver={ref.SITE}").status_code == 404


def test_frais_d_un_autre_site_403(client_user):
    assert client_user.post("/api/frais", json={**FRAIS, "miniserver": ref.AUTRE}).status_code == 403
    assert client_user.get(f"/api/frais?miniserver={ref.AUTRE}").status_code == 403
    assert client_user.delete(f"/api/frais/1?miniserver={ref.AUTRE}").status_code == 403


def test_frais_invalide_400(client_user):
    res = client_user.post("/api/frais", json={**FRAIS, "au": "2026-09-01"})
    assert res.status_code == 400 and "avant son début" in res.get_json()["error"]


def test_site_inconnu_400_pour_l_admin(client_admin):
    assert client_admin.get("/api/parametres?miniserver=Inexistant").status_code == 400
