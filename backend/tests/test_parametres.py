"""Paramètres du décompte : stockage versionné, validation, valeur en
vigueur, frais accessoires (services/parametres.py, docs/plan-page-parametres.md)."""
import sqlite3
from contextlib import closing

import pytest

from loxone_collector import db
from loxone_collector.services import parametres as par

SITE = "La Calbasse"


@pytest.fixture
def conn(tmp_path):
    with closing(db.get_connection(tmp_path / "p.db")) as c:
        yield c


# --- Validation -----------------------------------------------------------

def test_virgule_decimale_et_apostrophe_acceptees():
    assert par.lire_valeur("prix_reseau", "0,25") == 0.25
    assert par.lire_valeur("location_compteurs_an", "1'200") == 1200.0
    assert par.lire_valeur("prix_solaire", 0.17) == 0.17


@pytest.mark.parametrize("brut", ["abc", "nan", "inf", True, "-0,1"])
def test_prix_invalide_refuse(brut):
    with pytest.raises(ValueError):
        par.lire_valeur("prix_reseau", brut)


def test_pourcentage_borne():
    assert par.lire_valeur("pac_part_chauffage", "80") == 80.0
    with pytest.raises(ValueError, match="au plus 100"):
        par.lire_valeur("pac_part_chauffage", "120")


def test_vide_refuse_sauf_parametre_facultatif():
    with pytest.raises(ValueError, match="obligatoire"):
        par.lire_valeur("prix_reseau", "")
    assert par.lire_valeur("location_compteurs_an", "") is None


def test_oui_non():
    assert par.lire_valeur("tva_solaire", "oui") is True
    assert par.lire_valeur("tva_solaire", False) is False
    with pytest.raises(ValueError):
        par.lire_valeur("tva_solaire", "peut-être")


def test_cle_inconnue_et_date_invalide(conn):
    with pytest.raises(ValueError, match="inconnu"):
        par.ajouter_version(conn, SITE, "prix_borne", "2026-11-01", "0,5")
    with pytest.raises(ValueError, match="date invalide"):
        par.ajouter_version(conn, SITE, "prix_reseau", "01.11.2026", "0,25")


def test_valeur_globale_reservee_aux_parametres_globaux(conn):
    par.ajouter_version(conn, par.GLOBAL, "taux_tva", "2024-01-01", "8,1")
    with pytest.raises(ValueError, match="pas de valeur globale"):
        par.ajouter_version(conn, par.GLOBAL, "prix_reseau", "2024-01-01", "0,25")


# --- Valeur en vigueur ----------------------------------------------------

def test_prise_d_effet_veille_et_jour_meme(conn):
    par.ajouter_version(conn, SITE, "prix_reseau", "2026-01-01", "0,24")
    par.ajouter_version(conn, SITE, "prix_reseau", "2026-11-01", "0,25")
    assert par.valeurs_au(conn, SITE, "2026-10-31")["prix_reseau"]["valeur"] == 0.24
    nov = par.valeurs_au(conn, SITE, "2026-11-01")["prix_reseau"]
    assert nov["valeur"] == 0.25 and nov["depuis"] == "2026-11-01" and nov["origine"] == "site"


def test_avant_toute_version_valeur_par_defaut(conn):
    par.ajouter_version(conn, SITE, "prix_reseau", "2026-11-01", "0,25")
    v = par.valeurs_au(conn, SITE, "2026-10-31")
    assert v["prix_reseau"] == {"valeur": None, "depuis": None, "origine": "defaut", "id": None}
    assert v["tva_solaire"]["valeur"] is False


def test_tva_du_site_l_emporte_sur_la_globale(conn):
    par.ajouter_version(conn, par.GLOBAL, "taux_tva", "2024-01-01", "8,1")
    assert par.valeurs_au(conn, SITE, "2026-11-01")["taux_tva"]["origine"] == "global"
    par.ajouter_version(conn, SITE, "taux_tva", "2026-06-01", "7,7")
    tva = par.valeurs_au(conn, SITE, "2026-11-01")["taux_tva"]
    assert tva["valeur"] == 7.7 and tva["origine"] == "site"
    assert par.valeurs_au(conn, "Sequoia", "2026-11-01")["taux_tva"]["valeur"] == 8.1


def test_meme_date_corrige_la_version(conn):
    par.ajouter_version(conn, SITE, "prix_solaire", "2026-11-01", "0,176", updated_by="client")
    par.ajouter_version(conn, SITE, "prix_solaire", "2026-11-01", "0,17", updated_by="client")
    versions = db.list_parametres(conn, [SITE])
    assert len(versions) == 1 and versions[0]["valeur"] == "0.17"
    assert versions[0]["updated_by"] == "client"


def test_location_retiree_a_partir_d_une_date(conn):
    par.ajouter_version(conn, SITE, "location_compteurs_an", "2026-01-01", "120")
    par.ajouter_version(conn, SITE, "location_compteurs_an", "2026-07-01", "")
    assert par.valeurs_au(conn, SITE, "2026-06-30")["location_compteurs_an"]["valeur"] == 120.0
    loc = par.valeurs_au(conn, SITE, "2026-07-01")["location_compteurs_an"]
    assert loc["valeur"] is None and loc["origine"] == "site"


def test_changement_pendant_la_periode_signale(conn):
    par.ajouter_version(conn, SITE, "prix_reseau", "2026-01-01", "0,24")
    par.ajouter_version(conn, SITE, "prix_reseau", "2026-11-15", "0,25")
    par.ajouter_version(conn, SITE, "pac_part_chauffage", "2026-11-15", "80")
    p = par.parametres_periode(conn, SITE, "2026-11-01", "2026-11-30")
    assert p["valeurs"]["prix_reseau"]["valeur"] == 0.24
    assert p["changent"] == ["prix_reseau", "pac_part_chauffage"]
    assert par.parametres_periode(conn, SITE, "2026-12-01", "2026-12-31")["changent"] == []


def test_valeur_globale_masquee_par_le_site_ne_change_rien(conn):
    par.ajouter_version(conn, SITE, "taux_tva", "2024-01-01", "8,1")
    par.ajouter_version(conn, par.GLOBAL, "taux_tva", "2026-11-15", "8,5")
    assert par.parametres_periode(conn, SITE, "2026-11-01", "2026-11-30")["changent"] == []


# --- Frais accessoires ----------------------------------------------------

def test_frais_compris_dans_la_periode_seulement(conn):
    par.ajouter_frais(conn, SITE, "2026-11-01", "2026-11-30", "APP35", "Entretien", "45,50", "oui")
    par.ajouter_frais(conn, SITE, "2026-11-20", "2026-12-10", "Commun", "Nettoyage", 80, False)
    par.ajouter_frais(conn, "Sequoia", "2026-11-01", "2026-11-30", "APP01", "Autre site", 10, True)
    p = par.parametres_periode(conn, SITE, "2026-11-01", "2026-11-30")
    assert [(f["zone"], f["montant_ht"], f["tva"]) for f in p["frais"]] == [("APP35", 45.5, True)]
    assert [f["libelle"] for f in p["frais_hors_periode"]] == ["Nettoyage"]


def test_frais_invalides(conn):
    with pytest.raises(ValueError, match="avant son début"):
        par.ajouter_frais(conn, SITE, "2026-11-30", "2026-11-01", "APP35", "X", 1, True)
    with pytest.raises(ValueError, match="zone"):
        par.ajouter_frais(conn, SITE, "2026-11-01", "2026-11-30", " ", "X", 1, True)
    with pytest.raises(ValueError, match="montant"):
        par.ajouter_frais(conn, SITE, "2026-11-01", "2026-11-30", "APP35", "X", "dix", True)


def test_frais_supprime_seulement_sur_son_site(conn):
    fid = par.ajouter_frais(conn, SITE, "2026-11-01", "2026-11-30", "APP35", "X", 1, True)
    assert not db.delete_frais(conn, "Sequoia", fid)
    assert db.delete_frais(conn, SITE, fid)
    assert db.list_frais(conn, SITE) == []


# --- Migration ------------------------------------------------------------

def _base_avant_parametres(chemin):
    """Une base telle qu'avant cette version : `tarifs` rempli, pas de
    table `parametres`."""
    with closing(db.get_connection(chemin)) as c:
        c.execute("DROP TABLE parametres")
        c.execute(
            "INSERT INTO tarifs (miniserver, valid_from, prix_reseau, prix_solaire, taux_tva, note) "
            "VALUES ('Sequoia', '2026-01-01', 0.25, 0.17, 8.1, 'contrat')"
        )
        c.commit()


def test_migration_recopie_les_tarifs_une_seule_fois(tmp_path):
    chemin = tmp_path / "ancienne.db"
    _base_avant_parametres(chemin)
    with closing(db.get_connection(chemin)) as c:
        v = par.valeurs_au(c, "Sequoia", "2026-03-01")
        assert (v["prix_reseau"]["valeur"], v["prix_solaire"]["valeur"], v["taux_tva"]["valeur"]) \
            == (0.25, 0.17, 8.1)
        assert v["taux_tva"]["origine"] == "site"
        # Une version supprimée depuis la page ne revient pas au démarrage suivant.
        db.delete_parametre(c, v["prix_reseau"]["id"])
    with closing(db.get_connection(chemin)) as c:
        assert par.valeurs_au(c, "Sequoia", "2026-03-01")["prix_reseau"]["valeur"] is None
        assert c.execute("SELECT COUNT(*) FROM tarifs").fetchone()[0] == 1


def test_unicite_par_site_cle_et_date(conn):
    db.upsert_parametre(conn, SITE, "prix_reseau", "2026-11-01", "0.25")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO parametres (miniserver, cle, valid_from, valeur) VALUES (?, ?, ?, ?)",
            (SITE, "prix_reseau", "2026-11-01", "0.26"),
        )
