"""Montants du décompte (services/billing/chiffrage.py).

Cas de référence : La Calbasse (zones `APPxx`, `COMMUN`, `PAC` séparés,
décision du 2026-11-24), un mois entier, montants attendus calculés à la
main (voir le commentaire de chaque valeur). La buanderie, absente de
La Calbasse, est testée sur un cas synthétique."""
import pytest

from loxone_collector.services.billing import chiffrage as mt

VALEURS = {
    "prix_reseau": 0.25, "prix_solaire": 0.17, "taux_tva": 8.1, "tva_solaire": False,
    "location_compteurs_an": 120.0, "pac_part_chauffage": 80.0, "buanderie_prix_cycle": None,
}
UN_MOIS = 1 / 12


def calbasse():
    return {
        "zones": [
            {"zone": "APP35", "label": "App 35", "reseau": 45.25, "solaire": 35.84},
            {"zone": "APP36", "label": "App 36", "reseau": 30.00, "solaire": 20.00},
            {"zone": "APP37", "label": "App 37", "reseau": 60.40, "solaire": 40.10},
            {"zone": "COMMUN", "label": "Communs", "reseau": 151.32, "solaire": 52.96},
            {"zone": "PAC", "label": "PAC", "reseau": 964.94, "solaire": 323.84},
        ],
        "chauffage": {"APP35": 40.0, "APP36": 100.0, "APP37": 160.0},   # kWh th
        "eau_chaude": {"APP35": 1.5, "APP36": 2.0, "APP37": 0.5},       # m³
        "buanderie": {},
        "frais": [],
    }


def m(ht, tva):
    return {"ht": ht, "tva": tva, "ttc": round(ht + tva, 2)}


def ligne(res, zone):
    return next(x for x in res["recapitulatif"] if x["zone"] == zone)


def sans_quantite(d):
    return {k: d[k] for k in ("ht", "tva", "ttc")}


# --- Cas de référence -------------------------------------------------------

def test_electricite_d_un_lot():
    res = mt.calculer_montants(calbasse(), VALEURS, UN_MOIS)
    app35 = res["electricite"]["APP35"]
    assert app35["solaire"] == m(6.09, 0.0)        # 35,84 × 0,17 = 6,0928 ; sans TVA
    assert app35["reseau"] == m(11.31, 0.92)       # 45,25 × 0,25 = 11,3125 ; × 8,1 % = 0,916
    assert app35["location"] == m(10.0, 0.81)      # 120 / 12
    assert app35["total"] == m(27.40, 1.73)


def test_communs_sans_location():
    res = mt.calculer_montants(calbasse(), VALEURS, UN_MOIS)
    communs = res["electricite"]["COMMUN"]
    assert "location" not in communs
    assert communs["total"] == m(46.83, 3.06)      # 9,00 + 37,83 ; TVA 3,064


def test_cout_de_la_pac_et_cle_chauffage_eau_chaude():
    res = mt.calculer_montants(calbasse(), VALEURS, UN_MOIS)
    assert "location" not in res["pac"]
    assert res["pac"]["total"] == m(296.29, 19.54)  # 55,05 + 241,24 ; TVA sur 241,24
    assert res["chauffage"]["cout"] == m(237.03, 15.63)   # 80 %
    assert res["eau_chaude"]["cout"] == m(59.26, 3.91)    # le reste, au centime
    total = mt._somme([res["chauffage"]["cout"], res["eau_chaude"]["cout"]])
    assert total == res["pac"]["total"]


def test_prorata_chauffage_au_centime():
    ch = mt.calculer_montants(calbasse(), VALEURS, UN_MOIS)["chauffage"]["par_zone"]
    assert {z: sans_quantite(v) for z, v in ch.items()} == {
        "APP35": m(31.60, 2.08),    # 237,03 × 40 / 300
        "APP36": m(79.01, 5.21),
        "APP37": m(126.42, 8.34),
    }
    assert ch["APP36"]["quantite"] == 100.0


def test_reste_d_arrondi_a_la_plus_grosse_quantite():
    ec = mt.calculer_montants(calbasse(), VALEURS, UN_MOIS)["eau_chaude"]["par_zone"]
    # TVA 3,91 : 1,47 + 1,96 + 0,49 = 3,92 ; le centime de trop est retiré
    # à l'App 36 (2 m³, la plus grosse quantité).
    assert {z: sans_quantite(v) for z, v in ec.items()} == {
        "APP35": m(22.22, 1.47),
        "APP36": m(29.63, 1.95),
        "APP37": m(7.41, 0.49),
    }


def test_recapitulatif_sans_ligne_pac_et_total_qui_boucle():
    res = mt.calculer_montants(calbasse(), VALEURS, UN_MOIS)
    assert [x["zone"] for x in res["recapitulatif"]] == ["APP35", "APP36", "APP37", "COMMUN"]
    assert ligne(res, "APP35")["total"] == m(81.22, 5.28)
    assert ligne(res, "APP36")["total"] == m(129.54, 8.58)
    assert ligne(res, "APP37")["total"] == m(165.75, 10.86)
    assert ligne(res, "COMMUN") == {"zone": "COMMUN", "label": "Communs",
                                    "electricite": m(46.83, 3.06), "total": m(46.83, 3.06)}
    # Σ lignes = électricité des lots et des Communs + coût de la PAC.
    assert res["total"] == m(423.34, 27.78)
    assert res["messages"] == []


# --- Variantes des paramètres ------------------------------------------------

def test_tva_sur_le_solaire_si_demandee():
    res = mt.calculer_montants(calbasse(), {**VALEURS, "tva_solaire": True}, UN_MOIS)
    assert res["electricite"]["APP35"]["solaire"] == m(6.09, 0.49)


def test_sans_location_pas_de_composante():
    res = mt.calculer_montants(calbasse(), {**VALEURS, "location_compteurs_an": None}, UN_MOIS)
    assert "location" not in res["electricite"]["APP35"]
    assert res["electricite"]["APP35"]["total"] == m(17.40, 0.92)
    assert res["location_periode"] is None


def test_location_au_prorata_des_jours():
    assert mt.fraction_annee("2026-11-01", "2026-11-30") == pytest.approx(1 / 12)
    assert mt.fraction_annee("2026-01-01", "2026-12-31") == pytest.approx(1.0)
    assert mt.fraction_annee("2026-11-01", "2026-11-10") == pytest.approx(10 / 365)
    res = mt.calculer_montants(calbasse(), VALEURS, mt.fraction_annee("2026-11-01", "2026-11-10"))
    assert res["electricite"]["APP35"]["location"]["ht"] == 3.29   # 120 × 10 / 365


def test_prix_manquant_montants_vides_avec_message():
    res = mt.calculer_montants(calbasse(), {**VALEURS, "prix_solaire": None}, UN_MOIS)
    assert res["electricite"]["APP35"] is None and res["pac"] is None
    assert res["total"] is None
    assert any("prix_solaire" in msg for msg in res["messages"])


def test_part_chauffage_manquante():
    res = mt.calculer_montants(calbasse(), {**VALEURS, "pac_part_chauffage": None}, UN_MOIS)
    assert res["chauffage"] is None and ligne(res, "APP35")["chauffage"] is None
    assert ligne(res, "APP35")["total"] is None
    assert ligne(res, "COMMUN")["total"] == m(46.83, 3.06)
    assert any("Part du coût" in msg for msg in res["messages"])


def test_releve_de_chaleur_manquant_bloque_la_repartition():
    entrees = calbasse()
    entrees["chauffage"]["APP37"] = None
    res = mt.calculer_montants(entrees, VALEURS, UN_MOIS)
    assert res["chauffage"]["par_zone"] is None
    assert ligne(res, "APP35")["chauffage"] is None
    assert ligne(res, "APP35")["eau_chaude"] == m(22.22, 1.47)
    assert any("App 37" in msg or "APP37" in msg for msg in res["messages"])


def test_zone_relevee_a_zero_ne_paie_rien():
    entrees = calbasse()
    entrees["chauffage"]["APP35"] = 0.0
    res = mt.calculer_montants(entrees, VALEURS, UN_MOIS)
    assert ligne(res, "APP35")["chauffage"] == m(0.0, 0.0)
    assert sum(v["ht"] for v in res["chauffage"]["par_zone"].values()) == pytest.approx(237.03)


def test_site_sans_pac():
    entrees = calbasse()
    entrees["zones"] = [z for z in entrees["zones"] if z["zone"] != "PAC"]
    res = mt.calculer_montants(entrees, VALEURS, UN_MOIS)
    assert res["chauffage"] is None
    assert any("Pas de compteur de PAC" in msg for msg in res["messages"])


# --- Buanderie et frais (cas synthétique) -----------------------------------

def test_buanderie_cycles_sans_tva_et_kwh_au_prix_reseau():
    entrees = {"zones": [{"zone": "APP01", "label": "App 01", "reseau": 10.0, "solaire": 0.0}],
               "buanderie": {"APP01": {"cycles": 3, "kwh": 4.2}}, "frais": []}
    res = mt.calculer_montants(entrees, {**VALEURS, "buanderie_prix_cycle": 2.2,
                                         "location_compteurs_an": None}, UN_MOIS)
    b = res["buanderie"]["APP01"]
    assert b["cycles"] == m(6.60, 0.0)           # 3 × 2,20, montant final
    assert b["kwh"] == m(1.05, 0.09)             # 4,2 × 0,25 = 1,05 ; TVA 0,085
    assert ligne(res, "APP01")["buanderie"] == m(7.65, 0.09)


def test_buanderie_sans_prix_du_cycle():
    entrees = {"zones": [], "buanderie": {"APP01": {"cycles": 3, "kwh": 4.2}}, "frais": []}
    res = mt.calculer_montants(entrees, VALEURS, UN_MOIS)
    assert res["buanderie"]["APP01"] is None
    assert any("cycle" in msg for msg in res["messages"])


def test_frais_repris_tels_quels():
    entrees = calbasse()
    entrees["frais"] = [
        {"zone": "APP35", "libelle": "Entretien", "montant_ht": 45.5, "tva": True},
        {"zone": "COMMUN", "libelle": "Nettoyage", "montant_ht": 80.0, "tva": False},
    ]
    res = mt.calculer_montants(entrees, VALEURS, UN_MOIS)
    assert ligne(res, "APP35")["frais"] == m(45.50, 3.69)
    assert ligne(res, "COMMUN")["frais"] == m(80.0, 0.0)
    assert res["total"] == m(548.84, 31.47)


def test_repartir_somme_exacte():
    parts = mt.repartir(100.0, {"a": 1, "b": 1, "c": 1})
    assert sum(parts.values()) == pytest.approx(100.0) and parts["a"] == 33.34


# --- Adaptation du payload du décompte ----------------------------------------

def test_entrees_du_decompte():
    def periode(r, s):
        return {"2026-11": {"reseau": {"kwh": r}, "solaire": {"kwh": s}}}
    payload = {
        "zones": [{"zone": "APP35", "label": "App 35", "periodes": periode(45.25, 35.84)},
                  {"zone": "PAC", "label": "PAC", "periodes": periode(964.94, 323.84)},
                  {"zone": "APP99", "label": "App 99", "periodes": {}}],
        "postes": {"2026-11": {
            "chauffage": [{"zone": "COMMUN", "kwh": 10.0}, {"zone": "COMMUN", "kwh": 5.0},
                          {"zone": "APP35", "kwh": None}, {"zone": "APP35", "kwh": 3.0}],
            "eau_chaude": [{"zone": "APP35", "kwh": 1.5}],
            "buanderie": [],
        }},
    }
    e = mt.entrees_du_decompte(payload, "2026-11", frais=[])
    assert [z["zone"] for z in e["zones"]] == ["APP35", "PAC"]
    assert e["chauffage"] == {"COMMUN": 15.0, "APP35": None}
    assert e["eau_chaude"] == {"APP35": 1.5}
