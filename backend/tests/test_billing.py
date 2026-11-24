"""
Tests pour billing.py (page de décompte de charges, /decompte).

Quatre choses valent d'être verrouillées par un test ici :
  - le découpage MENSUEL en heure LOCALE (Europe/Zurich), qui diffère du
    reste du dashboard (découpage UTC) et se casserait silencieusement si
    quelqu'un le repassait en UTC ;
  - la distinction entre taux d'autoproduction et taux d'autoconsommation,
    qui évoluent en sens inverse et ont déjà été confondus une fois ;
  - la résolution "quelle série alimente quelle colonne", qui repose sur des
    heuristiques de libellé validées sur l'installation MS-Arlopi (le cas
    "Communs Sol", mal classé automatiquement, est le plus fragile) ;
  - le refus de calculer une consommation quand un compteur a été remis à
    zéro dans la période : c'est là qu'un décompte deviendrait faux sans
    prévenir.

Utilise unittest (stdlib), comme test_classification.py.
"""
import io
import sqlite3
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

from loxone_collector import db
from loxone_collector.exports import decompte as decompte_export
from loxone_collector.exports import decompte_xlsx
from loxone_collector.services import billing
from loxone_collector.services.billing import chiffrage
from loxone_collector.services.billing import decompte as billing_decompte


def ts(year, month, day, hour=0, tz="Europe/Zurich"):
    return int(datetime(year, month, day, hour, tzinfo=ZoneInfo(tz)).timestamp())


VALEURS_TEST = {
    "prix_reseau": 0.25, "prix_solaire": 0.12, "taux_tva": 8.1, "tva_solaire": False,
    "location_compteurs_an": None, "pac_part_chauffage": 80.0, "buanderie_prix_cycle": 2.2,
}


def ajouter_chiffrage(payload, cle, valeurs=None, frais=(), fraction=1 / 12):
    """Montants comme les calcule la route d'export (paramètres en vigueur
    depuis le 01.01.2026)."""
    valeurs = dict(VALEURS_TEST if valeurs is None else valeurs)
    params = {
        "valeurs": {k: {"valeur": v, "depuis": None if v is None else "2026-01-01",
                        "origine": "defaut" if v is None else "site", "id": None}
                    for k, v in valeurs.items()},
        "changent": [], "frais": list(frais), "frais_hors_periode": [],
    }
    entrees = chiffrage.entrees_du_decompte(payload, cle, list(frais))
    payload["chiffrage"] = {cle: {
        "du": "2026-01-01", "au": "2026-01-31", "parametres": params,
        "montants": chiffrage.calculer_montants(entrees, valeurs, fraction),
    }}
    return payload


class TestPeriodes(unittest.TestCase):
    def test_libelles_mensuels(self):
        self.assertEqual(billing.period_label(2026, 1), "Janvier 2026")
        self.assertEqual(billing.period_label(2026, 8), "Août 2026")
        self.assertEqual(billing.period_label_short(2026, 5), "mai. 26")

    def test_bornes_calees_sur_minuit_local(self):
        start, end = billing.period_bounds(2026, 1)
        self.assertEqual(start, ts(2026, 1, 1))
        self.assertEqual(end, ts(2026, 2, 1))

    def test_decembre_deborde_sur_lannee_suivante(self):
        start, end = billing.period_bounds(2025, 12)
        self.assertEqual(start, ts(2025, 12, 1))
        self.assertEqual(end, ts(2026, 1, 1))

    def test_bornes_pas_en_utc(self):
        # Régression : en heure suisse (UTC+1 en hiver), le 1er janvier
        # commence une heure avant le minuit UTC.
        start, _ = billing.period_bounds(2026, 1)
        utc_midnight = int(datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC")).timestamp())
        self.assertEqual(utc_midnight - start, 3600)

    def test_mois_a_cheval_sur_le_changement_dheure(self):
        # Mars contient le passage à l'heure d'été : le mois dure une heure
        # de moins que 31 jours pleins.
        start, end = billing.period_bounds(2026, 3)
        self.assertEqual(end - start, 31 * 86400 - 3600)

    def test_fevrier_non_bissextile(self):
        start, end = billing.period_bounds(2026, 2)
        self.assertEqual((end - start) // 86400, 28)

    def test_periodes_couvrantes(self):
        keys = [p["key"] for p in billing.periods_covering(ts(2025, 11, 15), ts(2026, 2, 2))]
        self.assertEqual(keys, ["2025-11", "2025-12", "2026-01", "2026-02"])

    def test_cle_de_periode_aller_retour(self):
        self.assertEqual(billing.parse_period_key("2026-03"), (2026, 3))
        with self.assertRaises(ValueError):
            billing.parse_period_key("2026-13")
        with self.assertRaises(ValueError):
            billing.parse_period_key("2026-P1")

    def test_plage_rognee_au_jour_pres(self):
        # Départ d'un locataire le 15.10 : du 15.01 au 15.10 inclus.
        plage = billing.periodes_plage(ts(2026, 1, 15), ts(2026, 10, 16))
        self.assertEqual(len(plage), 10)
        self.assertEqual((plage[0]["start"], plage[0]["end"]), (ts(2026, 1, 15), ts(2026, 2, 1)))
        self.assertEqual(plage[0]["label"], "Janvier 2026 (15.01 → 31.01)")
        self.assertEqual(plage[1]["label"], "Février 2026")
        self.assertEqual(plage[-1]["label"], "Octobre 2026 (01.10 → 15.10)")
        self.assertEqual(plage[-1]["end"], ts(2026, 10, 16))

    def test_jour_en_heure_locale(self):
        self.assertEqual(billing.parse_jour("2026-03-29"), ts(2026, 3, 29))
        for invalide in ("2026-02-30", "29.03.2026", "2026-3-29", ""):
            with self.assertRaises(ValueError):
                billing.parse_jour(invalide)


def s(series_id, label, resource_type, apartment, state_name="total"):
    return {
        "series_id": series_id, "label": label, "resource_type": resource_type,
        "apartment": apartment, "state_name": state_name, "unit": "kWh",
    }


class TestResolutionDesSeries(unittest.TestCase):
    """Cas réels observés sur MS-Arlopi (voir CLAUDE.md)."""

    def test_zone_standard(self):
        series = [
            s("g", "App 1 Grid (total)", "energie_reseau", "APP1"),
            s("s", "App 1 Solaire (total)", "energie_solaire", "APP1"),
            s("c", "Appartement 1 (total)", "energie_consommee", "APP1"),
        ]
        z = billing.resolve_zones(series)[0]
        self.assertEqual(z["sources"]["reseau"]["series_id"], "g")
        self.assertEqual(z["sources"]["solaire"]["series_id"], "s")
        self.assertEqual(z["sources"]["controle"]["series_id"], "c")

    def test_le_chauffage_nest_pas_le_compteur_de_controle(self):
        # Périmètre séparé, vérifié sur les données.
        series = [
            s("c", "Appartement 1 (total)", "energie_consommee", "APP1"),
            s("ch", "Chauffage App 1 (total)", "energie_consommee", "APP1"),
        ]
        z = billing.resolve_zones(series)[0]
        self.assertEqual(z["sources"]["controle"]["series_id"], "c")

    def test_communs_sol_reconnu_comme_solaire_malgre_sa_classification(self):
        # "Communs Sol" est rangé en "énergie consommée" par
        # classification.py (pas de mot-clé solaire reconnu dans "Sol") :
        # le repli sur le libellé doit le rattraper, et surtout ne pas le
        # confondre avec le compteur de contrôle de la zone.
        series = [
            s("g", "Communs Grid (total)", "energie_reseau", "COMMUN"),
            s("sol", "Communs Sol (total)", "energie_consommee", "COMMUN"),
            s("c", "Commun et Boiler (total)", "energie_consommee", "COMMUN"),
        ]
        z = billing.resolve_zones(series)[0]
        self.assertEqual(z["sources"]["solaire"]["series_id"], "sol")
        self.assertEqual(z["sources"]["controle"]["series_id"], "c")

    def test_seuls_les_compteurs_cumulatifs_sont_retenus(self):
        # "actual" est une puissance instantanée en kW : en faire une
        # différence de relevés n'aurait aucun sens.
        series = [
            s("a", "App 1 Grid (actual)", "energie_reseau", "APP1", state_name="actual"),
            s("g", "App 1 Grid (total)", "energie_reseau", "APP1"),
        ]
        z = billing.resolve_zones(series)[0]
        self.assertEqual(z["sources"]["reseau"]["series_id"], "g")

    def test_colonne_sans_serie(self):
        series = [s("c", "Rez jardin (total)", "energie_consommee", "REZJARDIN")]
        z = billing.resolve_zones(series)[0]
        self.assertIsNone(z["sources"]["reseau"])
        self.assertEqual(z["label"], "Rez Jardin")

    def test_wallbox_est_une_zone_a_part(self):
        # Borne de recharge : un compteur d'énergie dédié, sans appartement
        # dans Loxone Config -- reclassé manuellement comme Salles Communes.
        series = [s("w", "Wallbox Energizähler (total)", "energie_consommee", "WALLBOX")]
        z = billing.resolve_zones(series)[0]
        self.assertEqual(z["zone"], "WALLBOX")
        self.assertEqual(z["label"], "Wallbox")
        self.assertEqual(z["sources"]["controle"]["series_id"], "w")
        self.assertIsNone(z["sources"]["reseau"])

    def test_batiment(self):
        series = [
            s("p", "Production (total)", "energie_consommee", ""),
            s("i", "Réseau (total)", "energie_reseau", ""),
            s("e", "Réseau (totalNeg)", "energie_reseau", "", state_name="totalNeg"),
            s("z", "Appartement 1 (total)", "energie_consommee", "APP1"),
        ]
        b = billing.resolve_batiment(series)
        self.assertEqual(b["production"]["series_id"], "p")
        self.assertEqual(b["reseau_import"]["series_id"], "i")
        self.assertEqual(b["reseau_export"]["series_id"], "e")

    def test_solaire_total_prime_sur_production_figee(self):
        # Sequoia : « Production Solaire » ne bouge plus, « Solaire (total) » si.
        series = [
            s("fige", "Production Solaire (total)", "energie_solaire", ""),
            s("vif", "Solaire (total)", "energie_solaire", ""),
            s("i", "Réseau (total)", "energie_reseau", ""),
            s("e", "Réseau (totalNeg)", "energie_reseau", "", state_name="totalNeg"),
        ]
        self.assertEqual(billing.resolve_batiment(series)["production"]["series_id"], "vif")

    def test_fenetre_passee_prend_le_compteur_qui_a_des_heures(self):
        # « Solaire (total) » n'existe qu'après le mois demandé.
        conn = sqlite3.connect(":memory:")
        conn.executescript(db.SCHEMA)
        start, end = billing.period_bounds(2026, 8)
        conn.executemany(
            "INSERT INTO readings_hourly (series_id, ts, avg_value, max_value) VALUES (?, ?, ?, ?)",
            [
                ("hist", start, 100.0, 100.0),
                ("hist", start + 3600, 110.0, 110.0),
                ("vif", ts(2026, 9, 24), 1.0, 1.0),
            ],
        )
        series = [
            s("hist", "Production Solaire (total)", "energie_solaire", ""),
            s("vif", "Solaire (total)", "energie_solaire", ""),
        ]
        chosen = billing.production_couvrante(conn, series, start, end)
        self.assertEqual(chosen["series_id"], "hist")
        conn.close()

    def test_solaire_et_batterie_ne_remplace_pas_la_production(self):
        # Arlopy : « Solaire & Batterie » est un calcul de Loxone (lots −
        # import + export), la production est « Compteur PV » (2026-11-02).
        series = [
            s("bat", "Solaire & Batterie (total)", "energie_batterie", ""),
            s("pv", "Compteur PV (total)", "energie_solaire", ""),
        ]
        self.assertEqual(billing.resolve_batiment(series)["production"]["series_id"], "pv")
        self.assertEqual([c["series_id"] for c in billing.candidats_production(series)], ["pv"])

    def test_batiment_convention_la_calbasse(self):
        series = [
            s("pv", "Compteur PV (total)", "energie_solaire", ""),
            s("i", "Compteur Intro (total)", "energie_consommee", ""),
            s("e", "Compteur Intro (totalNeg)", "energie_consommee", "", state_name="totalNeg"),
        ]
        b = billing.resolve_batiment(series)
        self.assertEqual(b["production"]["series_id"], "pv")
        self.assertEqual(b["reseau_import"]["series_id"], "i")
        self.assertEqual(b["reseau_export"]["series_id"], "e")

    def test_compteurs_pv_dhorizon_ne_sont_pas_la_production(self):
        # Horizon : sa production est la somme des deux champs (historique
        # des graphiques) ; son « Compteur PV » ne sert pas encore.
        series = [
            s("ppe", "Compteur PV PPE (total)", "energie_solaire", ""),
            s("gar", "Compteur PV garages (total)", "energie_solaire", ""),
            s("pv", "Compteur PV (total)", "energie_solaire", ""),
        ]
        self.assertIsNone(billing.resolve_batiment(series)["production"])

    def test_pac_du_batiment_est_une_zone_a_part(self):
        # La Calbasse : la PAC n'a pas de zone dans Loxone Config, mais a ses
        # parts réseau / solaire, hors lots et hors Communs.
        series = [
            s("pac", "PAC (total)", "energie_consommee", ""),
            s("pr", "PAC Réseau (total)", "energie_reseau", ""),
            s("ps", "PAC Solaire (total)", "energie_solaire", ""),
            s("c", "Commun (total)", "energie_consommee", "COMMUN"),
        ]
        zones = {z["zone"]: z for z in billing.resolve_zones(series)}
        self.assertEqual(list(zones), ["COMMUN", "PAC"])
        pac = zones["PAC"]
        self.assertEqual(pac["label"], "PAC")
        self.assertEqual(pac["sources"]["controle"]["series_id"], "pac")
        self.assertEqual(pac["sources"]["reseau"]["series_id"], "pr")
        self.assertEqual(pac["sources"]["solaire"]["series_id"], "ps")

    def test_pac_deja_mesuree_de_sequoia_deduite_des_communs(self):
        # Étape 6 (décision B) : la PAC de Sequoia est une ligne, déduite des
        # Communs ; « Communs sans PAC » (même chiffre, depuis le 16.10.2026
        # seulement) n'est pas lu.
        series = [
            s("pac", "PAC déjà mesuré (total)", "energie_consommee", ""),
            s("com", "Communs (total)", "energie_consommee", "COMMUN"),
            s("sp", "Communs sans PAC (total)", "energie_consommee", "COMMUN"),
        ]
        zones = {z["zone"]: z for z in billing.resolve_zones(series)}
        self.assertEqual(sorted(zones), ["COMMUN", "PAC"])
        self.assertEqual(zones["PAC"]["sources"]["controle"]["series_id"], "pac")
        commun = zones["COMMUN"]["sources"]["controle"]
        self.assertEqual((commun["series_id"], commun["moins"]["series_id"]), ("com", "pac"))
        self.assertEqual(zones["COMMUN"]["ambigus"]["controle"], [])
        self.assertNotIn("moins", zones["PAC"]["sources"]["controle"])

    def test_pac_de_la_calbasse_pas_deduite(self):
        series = [
            s("pac", "PAC (total)", "energie_consommee", ""),
            s("com", "Commun (total)", "energie_consommee", "COMMUN"),
        ]
        zones = {z["zone"]: z for z in billing.resolve_zones(series)}
        self.assertNotIn("moins", zones["COMMUN"]["sources"]["controle"])


class TestTaux(unittest.TestCase):
    """Les deux taux évoluent en sens INVERSE au fil des saisons. Les avoir
    confondus une fois a produit un affichage qui semblait inversé (taux
    élevé en hiver) -- ce test fige la distinction."""

    def test_autoproduction_monte_en_ete(self):
        # Février : 19 kWh de solaire pour 1733 kWh consommés.
        hiver = billing.taux(19.0, 1733.0)
        # Juillet : 454 kWh de solaire pour 1950 kWh consommés.
        ete = billing.taux(454.0, 1950.0)
        self.assertLess(hiver, 2.0)
        self.assertGreater(ete, 20.0)
        self.assertGreater(ete, hiver)

    def test_autoconsommation_baisse_en_ete(self):
        # Même solaire autoconsommé, rapporté cette fois à la PRODUCTION.
        hiver = billing.taux(19.0, 23.0)      # février : on produit 23 kWh
        ete = billing.taux(454.0, 1578.0)     # juillet : on produit 1578 kWh
        self.assertGreater(hiver, 80.0)
        self.assertLess(ete, 35.0)
        self.assertLess(ete, hiver)

    def test_denominateur_nul_donne_none_pas_zero(self):
        # "aucune production du mois" et "production entièrement réinjectée"
        # ne sont pas la même information.
        self.assertIsNone(billing.taux(0.0, 0.0))
        self.assertIsNone(billing.taux(None, 100.0))


class TestConsommationDunePeriode(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(db.SCHEMA)

    def tearDown(self):
        self.conn.close()

    def hourly(self, series_id, points):
        self.conn.executemany(
            "INSERT INTO readings_hourly (series_id, ts, avg_value) VALUES (?, ?, ?)",
            [(series_id, t, v) for t, v in points],
        )
        self.conn.commit()

    def test_delta_entre_deux_releves(self):
        self.hourly("m", [(ts(2026, 1, 1), 100.0), (ts(2026, 1, 15), 160.0),
                          (ts(2026, 1, 31, 23), 180.0), (ts(2026, 2, 5), 190.0)])
        start, end = billing.period_bounds(2026, 1)
        res = billing.reading_delta(self.conn, "m", start, end, ts(2026, 6, 1))
        # Le relevé du 5 février appartient au mois SUIVANT : le compter
        # ici le ferait apparaître deux fois sur deux factures.
        self.assertAlmostEqual(res["kwh"], 80.0)
        self.assertEqual(res["alertes"], [])

    def test_releve_anterieur_a_la_periode_sert_de_point_de_depart(self):
        self.hourly("m", [(ts(2025, 12, 31, 23), 100.0), (ts(2026, 1, 20), 150.0)])
        start, end = billing.period_bounds(2026, 1)
        res = billing.reading_delta(self.conn, "m", start, end, ts(2026, 6, 1))
        self.assertAlmostEqual(res["kwh"], 50.0)

    def test_reset_de_compteur_rend_la_periode_non_calculable(self):
        # Cas réel : le compteur "Réseau" de MS-Arlopi est passé de 9795 à
        # 1344 le 26.03.2026. Un delta brut donnerait -8451 kWh, une somme
        # de deltas positifs donnerait un chiffre plausible mais faux.
        self.hourly("m", [(ts(2025, 12, 31, 23), 90.0),
                          (ts(2026, 1, 5), 100.0), (ts(2026, 1, 20), 9795.0),
                          (ts(2026, 1, 22), 1344.0), (ts(2026, 1, 25), 1500.0)])
        start, end = billing.period_bounds(2026, 1)
        res = billing.reading_delta(self.conn, "m", start, end, ts(2026, 6, 1))
        self.assertIsNone(res["kwh"])
        self.assertEqual(len(res["ruptures"]), 1)

    def test_micro_baisse_darrondi_ignoree(self):
        # Écart d'arrondi entre le poller live (pleine précision) et
        # l'historique Statistics importé : ne doit PAS passer pour un reset.
        self.hourly("m", [(ts(2025, 12, 31, 23), 2000.0),
                          (ts(2026, 1, 5), 2048.0454678), (ts(2026, 1, 20), 2048.045),
                          (ts(2026, 1, 25), 2100.0)])
        start, end = billing.period_bounds(2026, 1)
        res = billing.reading_delta(self.conn, "m", start, end, ts(2026, 6, 1))
        self.assertIsNotNone(res["kwh"])
        self.assertNotIn("ruptures", res)

    def test_trou_de_collecte_signale(self):
        self.hourly("m", [(ts(2025, 11, 1), 50.0), (ts(2026, 1, 20), 150.0)])
        start, end = billing.period_bounds(2026, 1)
        res = billing.reading_delta(self.conn, "m", start, end, ts(2026, 6, 1))
        self.assertTrue(any("trou de collecte" in a for a in res["alertes"]))

    def test_le_decompte_ignore_le_collecteur(self):
        # Le collecteur s'est arrêté le 28 : son dernier index donne 70 kWh.
        # Les Statistics du Miniserver, elles, vont jusqu'à la fin du mois.
        self.conn.execute(
            "INSERT INTO readings (series_id, ts, value) VALUES (?, ?, ?)",
            ("m", ts(2026, 9, 28, 17), 170.0),
        )
        self.conn.executemany(
            "INSERT INTO statistic_samples (series_id, ts, value) VALUES (?, ?, ?)",
            [
                ("m", ts(2026, 9, 1), 100.0),
                ("m", ts(2026, 9, 30, 23), 177.1),
            ],
        )
        self.conn.commit()
        start, end = billing.period_bounds(2026, 9)
        res = billing.reading_delta(self.conn, "m", start, end, ts(2026, 10, 2), statistics=True)
        self.assertAlmostEqual(res["kwh"], 77.1)
        self.assertEqual(res["alertes"], [])

    def test_pas_de_fausse_alerte_sur_une_periode_en_cours(self):
        # Mois en cours : la borne de fin est dans le futur, le dernier
        # relevé est forcément "vieux" par rapport à elle.
        self.hourly("m", [(ts(2026, 1, 1), 100.0), (ts(2026, 1, 20), 150.0)])
        start, end = billing.period_bounds(2026, 1)
        res = billing.reading_delta(self.conn, "m", start, end, ts(2026, 1, 20, 12))
        self.assertEqual(res["alertes"], [])


class TestExportLignes(unittest.TestCase):
    def test_lignes_et_total(self):
        payload = {
            "zones": [
                {"label": "App 1", "periodes": {"2026-05": {
                    "reseau": {"kwh": 10.0}, "solaire": {"kwh": 5.0}, "total": 15.0,
                    "taux_autoproduction": 33.333, "facturable": True, "en_cours": False,
                }}},
                {"zone": "APP2", "label": "App 2", "periodes": {"2026-05": {
                    "reseau": {"kwh": None}, "solaire": {"kwh": 2.0}, "total": None,
                    "taux_autoproduction": None, "facturable": False, "en_cours": False,
                }}},
            ],
        }
        rows = decompte_export.export_lignes(payload, "2026-05")
        self.assertEqual(rows[0], decompte_export.EXPORT_HEADERS)
        self.assertEqual(rows[1][0], "Mai 2026")
        self.assertEqual(rows[1][1], "App 1")
        self.assertEqual(rows[1][5], "facturable")
        self.assertEqual(rows[2][5], "données incomplètes")
        self.assertEqual(rows[3][0], "Mai 2026")
        self.assertEqual(rows[3][2], 15.0)
        self.assertEqual(rows[3][3], 7.0)
        self.assertEqual(rows[3][4], 10.0)
        self.assertEqual(rows[3][1], "Total immeuble")

    def test_mois_absent(self):
        with self.assertRaises(KeyError):
            decompte_export.export_lignes({"zones": []}, "2026-01")


class TestDecompteWorkbook(unittest.TestCase):
    """Le classeur doit s'ouvrir déjà lisible : titre, colonnes assez larges,
    nombres en kWh, paramètres et montants calculés par l'application,
    relevés dans un dernier onglet, mois en cours borné au dernier relevé."""

    def setUp(self):
        self.payload = {
            "timezone": "Europe/Zurich",
            "generated_at": int(datetime(2026, 6, 2, 10, 0, tzinfo=ZoneInfo("Europe/Zurich")).timestamp()),
            "periodes": [{
                "key": "2026-05",
                "label": "Mai 2026",
                "start": billing.period_bounds(2026, 5)[0],
                "end": billing.period_bounds(2026, 5)[1],
            }],
            "repartition": {"2026-05": {
                "source": "calculee",
                "confiance_verifiee": True,
                "raisons": ["heures d'achat au réseau impossibles"],
            }},
            "zones": [
                {"zone": "APP1", "label": "App 1", "sources": {
                    "controle": {"label": "Appartement 1 (total)", "series_id": "lot", "unit": "kWh"},
                }, "periodes": {"2026-05": {
                    "reseau": {"kwh": 10.0, "alertes": []},
                    "solaire": {"kwh": 5.004, "alertes": []},
                    "controle": {
                        "kwh": 15.004, "releve_debut": 100.0, "releve_fin": 115.004,
                        "releve_debut_ts": int(datetime(2026, 5, 1, 0, 10, tzinfo=ZoneInfo("Europe/Zurich")).timestamp()),
                        "releve_fin_ts": int(datetime(2026, 5, 31, 23, 50, tzinfo=ZoneInfo("Europe/Zurich")).timestamp()),
                        "alertes": [],
                    },
                    "total": 15.004,
                    "facturable": True, "en_cours": False,
                }}},
                {"zone": "APP2", "label": "App 2", "periodes": {"2026-05": {
                    "reseau": {"kwh": None, "alertes": ["pas de relevé dans la période"]},
                    "solaire": {"kwh": 2.0, "alertes": []},
                    "total": None,
                    "facturable": False, "en_cours": False,
                }}},
            ],
        }

    def classeur(self, payload=None, mois="2026-05", site="MS-Démo", conn=None):
        buf = io.BytesIO()
        wb = decompte_xlsx.build_decompte_workbook(payload or self.payload, mois, site, conn)
        wb.save(buf)
        buf.seek(0)
        return load_workbook(buf)

    def workbook(self, payload=None, mois="2026-05", site="MS-Démo", conn=None):
        return self.classeur(payload, mois, site, conn)["Électricité"]

    def _ligne(self, ws, valeur, col=1):
        for r in range(1, ws.max_row + 1):
            if ws.cell(r, col).value == valeur:
                return r
        self.fail(f"{valeur!r} introuvable")

    def test_onglet_frais_sans_parametres(self):
        ws = self.classeur()["Frais supplémentaires"]
        self.assertIn("MS-Démo", ws["A1"].value)
        self.assertEqual(
            [ws.cell(4, c).value for c in range(1, 9)],
            ["Zone", "Libellé", "Du", "Au", "Montant HT (CHF)", "TVA (CHF)",
             "Montant TTC (CHF)", "Remarque"],
        )
        self.assertIsNone(ws["A5"].value)
        self.assertIn("paramètres du décompte indisponibles", ws["A6"].value)

    def test_onglet_frais_saisis_et_lies_aux_zones(self):
        frais = [{"zone": "APP1", "libelle": "Entretien", "montant_ht": 45.5, "tva": True,
                  "du": "2026-05-01", "au": "2026-05-31", "note": "chaudière"}]
        wb = self.classeur(ajouter_chiffrage(self.payload, "2026-05", frais=frais))
        elec, ws = wb["Électricité"], wb["Frais supplémentaires"]
        self.assertEqual([ws.cell(5, c).value for c in range(1, 9)],
                         ["App 1", "Entretien", "01.05.2026", "31.05.2026", 45.5, 3.69, 49.19,
                          "chaudière"])
        self.assertEqual(ws["A5"].hyperlink.location, f"'Électricité'!A{self._ligne(elec, 'App 1')}")
        self.assertEqual([ws.cell(6, c).value for c in (1, 5, 6, 7)],
                         ["Total immeuble", 45.5, 3.69, 49.19])
        self.assertEqual(self._ligne(wb["Récapitulatif"], "App 1"), 5)

    def test_titre_largeurs_et_nombres(self):
        ws = self.workbook()
        self.assertEqual(ws.title, "Électricité")
        self.assertIn("MS-Démo", ws["A1"].value)
        self.assertIn("Mai 2026", ws["A2"].value)
        self.assertIn("01.05.2026", ws["A2"].value)
        self.assertIn("31.05.2026", ws["A2"].value)
        header = self._ligne(ws, "Zone")
        self.assertEqual(
            [ws.cell(header, c).value for c in range(1, 10)],
            ["Zone", "Consommation totale (kWh)", "Solaire (kWh)", "Réseau (kWh)",
             "Solaire HT (CHF)", "Réseau HT (CHF)", "TVA (CHF)", "Total TTC (CHF)", "État"],
        )
        self.assertIsNone(ws.cell(header, 10).value)
        self.assertGreaterEqual(ws.column_dimensions["A"].width, 22)
        self.assertGreaterEqual(ws.column_dimensions["B"].width, 18)
        app1 = self._ligne(ws, "App 1")
        app2 = self._ligne(ws, "App 2")
        total = self._ligne(ws, "Total immeuble")
        self.assertEqual(ws.cell(app1, 2).value, 15.0)
        self.assertEqual(ws.cell(app1, 3).value, 5.0)
        self.assertEqual(ws.cell(app1, 4).value, 10.0)
        self.assertEqual(ws.cell(app1, 2).number_format, "#,##0.00")
        self.assertIsNone(ws.cell(app2, 4).value)
        self.assertEqual(ws.cell(app2, 9).value, "données incomplètes")
        self.assertEqual(ws.cell(total, 2).value, 15.0)
        self.assertEqual(ws.cell(total, 3).value, 7.0)
        self.assertEqual(ws.cell(total, 4).value, 10.0)
        self.assertEqual(ws.freeze_panes, f"A{header + 1}")
        self.assertEqual(ws.page_setup.orientation, "landscape")
        self.assertEqual(ws.page_setup.fitToWidth, 1)
        self.assertEqual(int(ws.page_setup.paperSize), 9)
        self.assertIn(ws.page_setup.fitToHeight, (0, None))
        self.assertFalse(ws.sheet_view.showGridLines)
        self.assertTrue(any(str(r).startswith("A1:I") for r in ws.merged_cells.ranges))

    def test_le_total_egal_la_somme_des_lignes_affichees(self):
        ws = self.workbook()
        app1 = self._ligne(ws, "App 1")
        app2 = self._ligne(ws, "App 2")
        total = self._ligne(ws, "Total immeuble")
        self.assertEqual(ws.cell(total, 3).value, ws.cell(app1, 3).value + ws.cell(app2, 3).value)
        self.assertEqual(ws.cell(total, 2).value, ws.cell(app1, 2).value)
        textes = [ws.cell(r, 1).value for r in range(1, ws.max_row + 1)]
        self.assertTrue(any(isinstance(t, str) and "47 %" in t for t in textes))

    def test_parametres_affiches_et_montants_de_l_application(self):
        wb = self.classeur(ajouter_chiffrage(self.payload, "2026-05"))
        self.assertEqual(wb.sheetnames[0], "Récapitulatif")
        ws = wb["Électricité"]
        self.assertEqual([ws.cell(r, 1).value for r in range(4, 8)],
                         ["Prix réseau HT (CHF/kWh)", "Prix solaire HT (CHF/kWh)", "TVA (%)",
                          "TVA sur le solaire"])
        self.assertEqual([ws.cell(r, 2).value for r in range(4, 8)], [0.25, 0.12, 8.1, "non"])
        self.assertEqual(ws["C4"].value, "depuis le 01.01.2026")
        self.assertFalse(str(ws["B4"].fill.fgColor.rgb).endswith("FFF9C4"))
        self.assertIn("modifier dans la page", ws["A8"].value)
        app1, app2 = self._ligne(ws, "App 1"), self._ligne(ws, "App 2")
        # 5,004 × 0,12 = 0,60 ; 10 × 0,25 = 2,50, TVA 0,2025 ; pas de TVA sur le solaire.
        self.assertEqual([ws.cell(app1, c).value for c in range(5, 9)], [0.6, 2.5, 0.2, 3.3])
        self.assertEqual([ws.cell(app2, c).value for c in range(5, 9)], [None] * 4)
        total = self._ligne(ws, "Total immeuble")
        self.assertEqual(ws.cell(total, 8).value, 3.3)
        recap = wb["Récapitulatif"]
        self.assertEqual([recap.cell(4, c).value for c in range(1, 6)],
                         ["Zone", "Électricité TTC (CHF)", "Total HT (CHF)", "TVA (CHF)",
                          "Total TTC (CHF)"])
        self.assertEqual([recap.cell(5, c).value for c in range(1, 6)], ["App 1", 3.3, 3.1, 0.2, 3.3])
        self.assertEqual([recap.cell(6, c).value for c in range(1, 6)], ["App 2", None, None, None, None])
        notes = " ".join(str(recap.cell(r, 1).value) for r in range(7, recap.max_row + 1))
        self.assertIn("App 2 : relevé électrique incomplet", notes)

    def test_sans_parametres_montants_vides(self):
        wb = self.classeur()
        self.assertNotIn("Récapitulatif", wb.sheetnames)
        ws = wb["Électricité"]
        self.assertIn("paramètres du décompte indisponibles", ws["A4"].value)
        app1 = self._ligne(ws, "App 1")
        self.assertEqual([ws.cell(app1, c).value for c in range(5, 9)], [None] * 4)

    def test_onglet_releves(self):
        start, end = billing.period_bounds(2026, 5)
        conn = sqlite3.connect(":memory:")
        conn.executescript(db.SCHEMA)
        # Trois points dans le mois, un avant : seul le mois figure.
        for ts, value in (
            (start - 3600, 99.0),
            (start, 100.0),
            (start + 3600, 100.5),
            (end - 3600, 115.004),
        ):
            conn.execute(
                "INSERT INTO statistic_samples (series_id, ts, value) VALUES ('lot', ?, ?)",
                (ts, value),
            )
        conn.commit()
        wb = self.classeur(conn=conn)
        self.assertEqual(wb.sheetnames, ["Électricité", "Frais supplémentaires", "Relevés"])
        ws = wb["Relevés"]
        self.assertIn("Relevés de compteurs", ws["A1"].value)
        self.assertIn("Mai 2026", ws["A2"].value)
        self.assertEqual([ws.cell(4, c).value for c in range(1, 5)], ["Zone", "Compteur", "Date", "Index"])
        self.assertIsNone(ws.cell(4, 5).value)
        self.assertEqual(ws["A5"].value, "App 1")
        self.assertEqual(ws["B5"].value, "Appartement 1 (total)")
        self.assertIn("01.05.2026", ws["C5"].value)
        self.assertEqual(ws["D5"].value, 100.0)
        self.assertEqual(ws["D6"].value, 100.5)
        self.assertEqual(ws["D7"].value, 115.004)
        self.assertIsNone(ws["A8"].value)
        self.assertNotIn("99", [str(ws.cell(r, 4).value) for r in range(5, 8)])
        self.assertGreaterEqual(ws.column_dimensions["B"].width, 30)
        self.assertFalse(ws.sheet_view.showGridLines)
        conn.close()

    def test_notes_de_lecture_et_releves_manquants(self):
        ws = self.workbook()
        textes = []
        for r in range(1, ws.max_row + 1):
            for c in range(1, 4):
                v = ws.cell(r, c).value
                if isinstance(v, str):
                    textes.append(v)
        bloc = "\n".join(textes)
        self.assertIn("Ces parts sont calculées", bloc)
        self.assertNotIn("heures d'achat au réseau impossibles", bloc)
        self.assertIn("App 2 — Réseau : pas de relevé dans la période", bloc)
        self.assertIn("Une case vide n'est pas un zéro", bloc)

    def test_alerte_de_zone_quand_les_colonnes_n_en_ont_pas(self):
        self.payload["zones"].append({"label": "App 100", "periodes": {"2026-05": {
            "reseau": {"kwh": None, "alertes": []},
            "solaire": {"kwh": None, "alertes": []},
            "total": None,
            "facturable": False, "en_cours": False,
            "alertes": ["compteur du lot introuvable : répartition impossible"],
        }}})
        ws = self.workbook()
        textes = [ws.cell(r, 1).value for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value]
        self.assertIn(
            "App 100 : compteur du lot introuvable : répartition impossible",
            "\n".join(textes),
        )

    def test_mois_en_cours_s_arrete_au_dernier_releve(self):
        start, end = billing.period_bounds(2026, 10)
        dernier = int(datetime(2026, 10, 1, 15, 30, tzinfo=ZoneInfo("Europe/Zurich")).timestamp())
        payload = {
            "timezone": "Europe/Zurich",
            "generated_at": dernier + 60,
            "periodes": [{"key": "2026-10", "label": "Octobre 2026", "start": start, "end": end}],
            "batiment": {"periodes": {"2026-10": {"en_cours": True, "production": {}}}},
            "zones": [{"label": "App 1", "periodes": {"2026-10": {
                "reseau": {"kwh": 1.0, "alertes": [], "releve_fin_ts": dernier},
                "solaire": {"kwh": 1.0, "alertes": []},
                "total": 2.0,
                "facturable": False, "en_cours": True,
            }}}],
        }
        ws = self.workbook(payload, "2026-10", "MS-PPE-Sequoia")
        self.assertIn("01.10.2026", ws["A2"].value)
        self.assertIn("dernier relevé", ws["A2"].value)
        self.assertNotIn("31.10.2026", ws["A2"].value)

    def test_mois_absent(self):
        with self.assertRaises(KeyError):
            decompte_xlsx.build_decompte_workbook({"zones": []}, "2026-01", "site")


class TestRepartitionDansLeDecompte(unittest.TestCase):
    """Quand evaluer_site condamne les séries du Miniserver, le décompte
    facture le compteur du lot, scindé. Sinon il garde Grid + Solaire."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(db.SCHEMA)

    def tearDown(self):
        self.conn.close()

    def index(self, series_id, points):
        self.conn.executemany(
            "INSERT INTO statistic_samples (series_id, ts, value) VALUES (?, ?, ?)",
            [(series_id, t, v) for t, v in points],
        )
        self.conn.commit()

    def test_series_incoherentes_sont_remplacees_par_le_compteur_du_lot(self):
        # Heure 1 : le bâtiment n'importe rien, le Grid du lot augmente quand
        # même. Heure 2 : mix normal. Le compteur du lot suit le bâtiment.
        t0 = ts(2026, 1, 1)
        self.index("prod", [(t0, 0.0), (t0 + 3600, 10.0), (t0 + 7200, 16.0)])
        self.index("imp", [(t0, 0.0), (t0 + 3600, 0.0), (t0 + 7200, 4.0)])
        self.index("exp", [(t0, 0.0), (t0 + 3600, 2.0), (t0 + 7200, 3.0)])
        self.index("lot", [(t0, 0.0), (t0 + 3600, 8.0), (t0 + 7200, 17.0)])
        self.index("grid", [(t0, 0.0), (t0 + 3600, 5.0), (t0 + 7200, 6.0)])
        self.index("sol", [(t0, 0.0), (t0 + 3600, 0.0), (t0 + 7200, 4.0)])
        series = [
            s("prod", "Solaire (total)", "energie_solaire", ""),
            s("fige", "Production Solaire (total)", "energie_solaire", ""),
            s("imp", "Réseau (total)", "energie_reseau", ""),
            s("exp", "Réseau (totalNeg)", "energie_reseau", "", state_name="totalNeg"),
            s("lot", "Appartement 1 (total)", "energie_consommee", "APP1"),
            s("grid", "App 1 Grid (total)", "energie_reseau", "APP1"),
            s("sol", "App 1 Solaire (total)", "energie_solaire", "APP1"),
        ]
        payload = billing.compute_decompte(
            self.conn, series, billing.periods_covering(t0, t0 + 7200), ts(2026, 6, 1),
        )
        self.assertEqual(payload["repartition"]["2026-01"]["source"], "calculee")
        self.assertEqual(payload["batiment"]["sources"]["production"]["series_id"], "prod")
        e = payload["zones"][0]["periodes"]["2026-01"]
        self.assertEqual(e["reseau"]["kwh"], 4.0)
        self.assertEqual(e["solaire"]["kwh"], 13.0)
        self.assertEqual(e["total"], 17.0)
        self.assertEqual(e["reseau"]["kwh"] + e["solaire"]["kwh"], e["total"])
        self.assertTrue(e["facturable"])
        self.assertEqual(e["reseau"]["alertes"], [])

    def test_pac_de_sequoia_ligne_a_part_et_deduite_des_communs(self):
        # Bâtiment : 8 puis 9 kWh. Lot 3 + 4, Communs 5 + 5 dont PAC 3 + 3.
        t0 = ts(2026, 1, 1)
        h = lambda a, b: [(t0, 0.0), (t0 + 3600, a), (t0 + 7200, a + b)]  # noqa: E731
        self.index("prod", h(10.0, 6.0))
        self.index("imp", h(0.0, 4.0))
        self.index("exp", h(2.0, 1.0))
        self.index("lot", h(3.0, 4.0))
        self.index("grid", h(2.0, 1.0))  # achat sans import : séries condamnées
        self.index("sol", h(0.0, 3.0))
        self.index("com", h(5.0, 5.0))
        self.index("pac", h(3.0, 3.0))
        series = [
            s("prod", "Solaire (total)", "energie_solaire", ""),
            s("imp", "Réseau (total)", "energie_reseau", ""),
            s("exp", "Réseau (totalNeg)", "energie_reseau", "", state_name="totalNeg"),
            s("lot", "Appartement 1 (total)", "energie_consommee", "APP1"),
            s("grid", "App 1 Grid (total)", "energie_reseau", "APP1"),
            s("sol", "App 1 Solaire (total)", "energie_solaire", "APP1"),
            s("com", "Communs (total)", "energie_consommee", "COMMUN"),
            s("pac", "PAC déjà mesuré (total)", "energie_consommee", ""),
        ]
        payload = billing.compute_decompte(
            self.conn, series, billing.periods_covering(t0, t0 + 7200), ts(2026, 6, 1),
        )
        self.assertEqual(payload["repartition"]["2026-01"]["source"], "calculee")
        e = {z["zone"]: z["periodes"]["2026-01"] for z in payload["zones"]}
        self.assertEqual((e["APP1"]["total"], e["COMMUN"]["total"], e["PAC"]["total"]), (7.0, 4.0, 6.0))
        for z in e.values():
            self.assertTrue(z["facturable"])
            self.assertAlmostEqual(z["reseau"]["kwh"] + z["solaire"]["kwh"], z["total"])
        # Bilan : les zones (PAC comprise une seule fois) bouclent sur le bâtiment.
        self.assertAlmostEqual(sum(z["total"] for z in e.values()), 17.0)

    def test_bilan_non_exploitable_garde_les_series_du_miniserver(self):
        # Le compteur réseau du bâtiment ne mesure pas la même chose que
        # les zones : on ne recalcule pas, on garde Grid + Solaire.
        t0 = ts(2026, 1, 1)
        self.index("prod", [(t0, 0.0), (t0 + 3600, 1.0)])
        self.index("imp", [(t0, 0.0), (t0 + 3600, 1.0)])
        self.index("exp", [(t0, 0.0), (t0 + 3600, 0.0)])
        self.index("lot", [(t0, 0.0), (t0 + 3600, 100.0)])
        self.index("grid", [(t0, 0.0), (t0 + 3600, 40.0)])
        self.index("sol", [(t0, 0.0), (t0 + 3600, 60.0)])
        series = [
            s("prod", "Production (total)", "energie_consommee", ""),
            s("imp", "Réseau (total)", "energie_reseau", ""),
            s("exp", "Réseau (totalNeg)", "energie_reseau", "", state_name="totalNeg"),
            s("lot", "Appartement 1 (total)", "energie_consommee", "APP1"),
            s("grid", "App 1 Grid (total)", "energie_reseau", "APP1"),
            s("sol", "App 1 Solaire (total)", "energie_solaire", "APP1"),
        ]
        payload = billing.compute_decompte(
            self.conn, series, billing.periods_covering(t0, t0 + 3600), ts(2026, 6, 1),
        )
        self.assertEqual(payload["repartition"]["2026-01"]["source"], "loxone")
        e = payload["zones"][0]["periodes"]["2026-01"]
        self.assertAlmostEqual(e["reseau"]["kwh"], 40.0)
        self.assertAlmostEqual(e["solaire"]["kwh"], 60.0)
        self.assertAlmostEqual(e["total"], 100.0)

    def test_verdict_propre_a_chaque_mois(self):
        # Janvier : le Grid du lot augmente sans import du bâtiment (bloc
        # faux, comme Sequoia avant le 10.09.2026). Février : séries justes.
        # Le mois faux ne doit pas condamner le mois juste.
        t0, f0 = ts(2026, 1, 1), ts(2026, 2, 1)
        self.index("prod", [(t0, 0.0), (t0 + 3600, 10.0), (t0 + 7200, 16.0), (f0, 16.0), (f0 + 3600, 32.0)])
        self.index("imp", [(t0, 0.0), (t0 + 3600, 0.0), (t0 + 7200, 4.0), (f0, 4.0), (f0 + 3600, 8.0)])
        self.index("exp", [(t0, 0.0), (t0 + 3600, 2.0), (t0 + 7200, 3.0), (f0, 3.0), (f0 + 3600, 6.0)])
        self.index("lot", [(t0, 0.0), (t0 + 3600, 8.0), (t0 + 7200, 17.0), (f0, 17.0), (f0 + 3600, 34.0)])
        self.index("grid", [(t0, 0.0), (t0 + 3600, 5.0), (t0 + 7200, 6.0), (f0, 6.0), (f0 + 3600, 10.0)])
        self.index("sol", [(t0, 0.0), (t0 + 3600, 0.0), (t0 + 7200, 4.0), (f0, 4.0), (f0 + 3600, 17.0)])
        series = [
            s("prod", "Solaire (total)", "energie_solaire", ""),
            s("imp", "Réseau (total)", "energie_reseau", ""),
            s("exp", "Réseau (totalNeg)", "energie_reseau", "", state_name="totalNeg"),
            s("lot", "Appartement 1 (total)", "energie_consommee", "APP1"),
            s("grid", "App 1 Grid (total)", "energie_reseau", "APP1"),
            s("sol", "App 1 Solaire (total)", "energie_solaire", "APP1"),
        ]
        payload = billing.compute_decompte(
            self.conn, series, billing.periods_covering(t0, f0 + 3600), ts(2026, 6, 1),
        )
        janvier, fevrier = payload["repartition"]["2026-01"], payload["repartition"]["2026-02"]
        self.assertEqual(janvier["source"], "calculee")
        self.assertTrue(janvier["raisons"])
        self.assertEqual(fevrier["source"], "loxone")
        self.assertTrue(fevrier["confiance_verifiee"])
        e = payload["zones"][0]["periodes"]["2026-02"]
        self.assertAlmostEqual(e["reseau"]["kwh"], 4.0)
        self.assertAlmostEqual(e["solaire"]["kwh"], 13.0)
        self.assertEqual(billing.repartition_du_mois(payload, "2026-02"), fevrier)
        self.assertEqual(billing.repartition_du_mois(payload, "2030-01")["source"], "loxone")

    def test_releve_de_compteurs_qui_se_relaient(self):
        # « Production Solaire » couvre le début du mois, « Solaire (total) »
        # la fin. Aucun des deux seul ne ferme le bilan ; ensemble, le
        # décompte reste sur le compteur du lot.
        t0 = ts(2026, 9, 1)
        self.index("hist", [(t0, 1000.0), (t0 + 3600, 1010.0)])
        self.index("vif", [(t0 + 7200, 0.0), (t0 + 10800, 6.0)])
        self.index("imp", [
            (t0, 0.0), (t0 + 3600, 0.0), (t0 + 7200, 0.0), (t0 + 10800, 4.0),
        ])
        self.index("exp", [
            (t0, 0.0), (t0 + 3600, 2.0), (t0 + 7200, 2.0), (t0 + 10800, 3.0),
        ])
        self.index("lot", [(t0, 0.0), (t0 + 3600, 8.0), (t0 + 10800, 17.0)])
        self.index("grid", [(t0, 0.0), (t0 + 3600, 5.0), (t0 + 10800, 6.0)])
        self.index("sol", [(t0, 0.0), (t0 + 3600, 0.0), (t0 + 10800, 4.0)])
        series = [
            s("hist", "Production Solaire (total)", "energie_solaire", ""),
            s("vif", "Solaire (total)", "energie_solaire", ""),
            s("imp", "Réseau Général (total)", "energie_reseau", ""),
            s("exp", "Réseau Général (totalNeg)", "energie_reseau", "", state_name="totalNeg"),
            s("lot", "Appartement 1 (total)", "energie_consommee", "APP1"),
            s("grid", "App 1 Grid (total)", "energie_reseau", "APP1"),
            s("sol", "App 1 Solaire (total)", "energie_solaire", "APP1"),
        ]
        payload = billing.compute_decompte(
            self.conn, series, billing.periods_covering(t0, t0 + 10800), ts(2026, 10, 2),
        )
        self.assertEqual(payload["repartition"]["2026-09"]["source"], "calculee")
        e = payload["zones"][0]["periodes"]["2026-09"]
        self.assertEqual(e["total"], 17.0)
        self.assertEqual(e["reseau"]["kwh"] + e["solaire"]["kwh"], e["total"])
        prod = payload["batiment"]["periodes"]["2026-09"]["production"]["kwh"]
        self.assertAlmostEqual(prod, 16.0)


class TestBatimentPartiel(unittest.TestCase):
    def test_la_consommation_reste_quand_une_zone_manque(self):
        zones = [
            {"periodes": {"2026-08": {
                "reseau": {"kwh": 10.0}, "solaire": {"kwh": 5.0}, "total": 15.0,
            }}},
            {"periodes": {"2026-08": {
                "reseau": {"kwh": None}, "solaire": {"kwh": 2.0}, "total": None,
            }}},
        ]
        p = {"key": "2026-08", "start": 0, "end": 1}
        b = billing_decompte._batiment_period(None, {"production": None}, zones, p, 0)
        self.assertEqual(b["consommation_totale"], 15.0)
        self.assertEqual(b["autoconsommation"], 5.0)
        self.assertTrue(b["zones_incompletes"])
        self.assertIsNone(b["injection"])


class TestPostes(unittest.TestCase):
    """Chauffage (énergie seulement), eau chaude (pas l'eau froide) et
    buanderie, sur les libellés réels de Sequoia et d'Arlopi."""

    SERIES = [
        s("ch", "APP 01 compteur chaleur [5] (total)", "energie_consommee", "APP01"),
        s("chv", "APP 01 compteur chaleur volume [5] (total)", "energie_consommee", "APP01"),
        s("ec", "APP 01 compteur eau chaude m^3 [6] (total)", "eau_chaude", "APP01"),
        s("ef", "APP 01 compteur eau m^3 [7] (total)", "eau_froide", "APP01"),
        s("bk", "CEnergieApp 01 (total)", "energie_consommee", "APP01"),
        s("bc", "CNrMachineApp 01 (total)", "energie_consommee", "APP01"),
        s("lot", "Appartement 1 (total)", "energie_consommee", "APP01"),
        s("c34v", "APP 34 compteur Ccaleur volume [53] (total)", "energie_consommee", "APP34"),
        s("c34", "APP 34 compteur chaleur [53] (total)", "energie_consommee", "APP34"),
        s("b100", "CEnergieApp100 (total)", "energie_consommee", "APP100"),
        s("boiler", "Boiler ecs mesuré aussi ? (total)", "eau_chaude", ""),
        s("com1", "Chauffage Commerce (total)", "energie_consommee", "COMMERCE"),
        s("com2", "Chauffage Commerce supp. (total)", "energie_consommee", "COMMERCE"),
        s("live", "APP 01 compteur chaleur [5]", "energie_consommee", "APP01", state_name="actual"),
    ]

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(db.SCHEMA)

    def tearDown(self):
        self.conn.close()

    def index(self, series_id, points):
        self.conn.executemany(
            "INSERT INTO statistic_samples (series_id, ts, value) VALUES (?, ?, ?)",
            [(series_id, t, v) for t, v in points],
        )
        self.conn.commit()

    def postes(self):
        return {z["zone"]: z["compteurs"] for z in billing.resolve_postes(self.SERIES)}

    def ids(self, srcs):
        return [c["series_id"] for c in srcs]

    def test_energie_de_chauffage_sans_le_volume(self):
        p = self.postes()
        self.assertEqual(self.ids(p["APP01"]["chauffage"]), ["ch"])
        self.assertEqual(self.ids(p["APP34"]["chauffage"]), ["c34"])

    def test_eau_chaude_sans_eau_froide(self):
        self.assertEqual(self.ids(self.postes()["APP01"]["eau_chaude"]), ["ec"])

    def test_buanderie_et_cycles(self):
        p = self.postes()["APP01"]
        self.assertEqual(self.ids(p["buanderie"]), ["bk"])
        self.assertEqual(self.ids(p["buanderie_cycles"]), ["bc"])

    def test_zone_fantome_et_compteurs_d_immeuble_exclus(self):
        p = self.postes()
        self.assertNotIn("APP100", p)
        self.assertNotIn("", p)

    def test_tous_les_compteurs_d_une_zone_sont_gardes(self):
        self.assertEqual(self.ids(self.postes()["COMMERCE"]["chauffage"]), ["com1", "com2"])

    def test_nouveau_chauffage_du_commerce_pas_encore_decompte(self):
        series = [
            s("anc", "Commerce Chauffage ancien (total)", "energie_consommee", "COMMERCE"),
            s("anc2", "Commerce Chauffage ancien 2 (total)", "energie_consommee", "COMMERCE"),
            s("neuf", "Commerce Chauffage (total)", "energie_consommee", "COMMERCE"),
        ]
        p = {z["zone"]: z["compteurs"] for z in billing.resolve_postes(series)}
        self.assertEqual(self.ids(p["COMMERCE"]["chauffage"]), ["anc", "anc2"])

    def test_releves_du_mois(self):
        start, end = billing.period_bounds(2026, 8)
        self.index("ch", [(start - 600, 1000.0), (end - 600, 1250.5)])
        self.index("ec", [(start - 600, 10.0), (end - 600, 10.875)])
        self.index("bk", [(start - 600, 37.0), (end - 600, 41.5)])
        self.index("bc", [(start - 600, 2.0), (end - 600, 5.0)])
        r = billing.compute_postes(
            self.conn, billing.resolve_postes(self.SERIES),
            {"key": "2026-08", "start": start, "end": end}, end + 86400,
        )
        app01 = next(x for x in r["chauffage"] if x["zone"] == "APP01")
        self.assertAlmostEqual(app01["kwh"], 250.5)
        self.assertEqual(app01["releve_debut"], 1000.0)
        self.assertAlmostEqual(r["eau_chaude"][0]["kwh"], 0.875)
        b = r["buanderie"][0]
        self.assertAlmostEqual(b["energie"]["kwh"], 4.5)
        self.assertAlmostEqual(b["cycles"]["kwh"], 3.0)
        self.assertIsNone(r["buanderie_depuis"])

    def test_chauffage_de_la_calbasse_en_wh_jusqu_au_20_novembre(self):
        # Dette technique (postes.CHAUFFAGE_EN_WH_JUSQU_AU) : index en Wh
        # jusqu'à l'enregistrement du 20.11.2026, puis remise à zéro en kWh.
        from loxone_collector.services.billing import postes as postes_mod
        bascule = postes_mod.CHAUFFAGE_EN_WH_JUSQU_AU["La Calbasse"]
        cal = "La Calbasse:app35:total"
        series = [s(cal, "APP35 Chauffage (total)", "energie_consommee", "APP35")]
        start, end = billing.period_bounds(2026, 11)
        gel = postes_mod.CALBASSE_GEL_DEPUIS
        self.index(cal, [(start - 600, 236000.0), (gel - 600, 418000.0), (bascule - 800, 418000.0),
                         (bascule, 0.0), (end - 600, 87.0)])
        lire = lambda a, b: billing.compute_postes(  # noqa: E731
            self.conn, billing.resolve_postes(series), {"key": "x", "start": a, "end": b}, end + 86400,
        )["chauffage"][0]
        nov = lire(start, end)
        # Index M-Bus au redémarrage (607), pas l'index figé du 10.11 (418).
        self.assertAlmostEqual(nov["kwh"], 607.0 - 236.0 + 87.0)
        self.assertEqual((nov["releve_debut"], nov["releve_fin"]), (236.0, 87.0))
        self.assertNotIn("ruptures", nov)
        self.assertTrue(any("divisés par 1 000" in a for a in nov["alertes"]))
        self.assertTrue(any("compteur M-Bus" in a for a in nov["alertes"]))
        self.assertAlmostEqual(lire(start, gel)["kwh"], 182.0)  # avant le gel : en kWh aussi
        self.assertIsNone(lire(start, bascule - 1)["kwh"])      # fin pendant le gel
        self.assertIsNone(lire(gel + 3600, end)["kwh"])         # début pendant le gel
        self.assertAlmostEqual(lire(bascule, end)["kwh"], 87.0)

    def test_chauffage_de_la_calbasse_index_reconstitues(self):
        # APP41 / APP48 : index du 01.11 reconstitué (dette technique) ;
        # l'ancien compteur d'APP48, sans relevé après le 20.11, disparaît.
        from loxone_collector.services.billing import postes as postes_mod
        bascule = postes_mod.CHAUFFAGE_EN_WH_JUSQU_AU["La Calbasse"]
        series = [
            s("La Calbasse:a41:total", "APP41 Chauffage (total)", "energie_consommee", "APP41"),
            s("La Calbasse:a48old:total", "APP48 Chauffage (total)", "energie_consommee", "APP48"),
            s("La Calbasse:a48:total", "APP48 Chauffage (total)", "energie_consommee", "APP48"),
        ]
        start, end = billing.period_bounds(2026, 11)
        self.index("La Calbasse:a41:total", [(start - 600, 0.0), (bascule - 800, 366000.0),
                                             (bascule, 0.0), (end - 600, 100.0)])
        self.index("La Calbasse:a48old:total", [(start - 600, 0.0), (bascule - 900000, 1.0)])
        self.index("La Calbasse:a48:total", [(bascule, 0.0), (end - 600, 40.0)])
        lire = lambda a, b: billing.compute_postes(  # noqa: E731
            self.conn, billing.resolve_postes(series), {"key": "x", "start": a, "end": b}, end + 86400,
        )["chauffage"]
        nov = {x["zone"]: x for x in lire(start, end)}
        self.assertEqual(len(lire(start, end)), 2)
        self.assertAlmostEqual(nov["APP41"]["kwh"], 443 - 254.4 + 100.0)
        self.assertAlmostEqual(nov["APP48"]["kwh"], 298 - 127.4 + 40.0)
        self.assertEqual(nov["APP48"]["series_id"], "La Calbasse:a48:total")
        self.assertTrue(any(a.startswith("estimé") for a in nov["APP48"]["alertes"]))
        # Début autre que le 01.11 : l'index reconstitué ne vaut pas.
        self.assertIsNone(next(x for x in lire(start + 86400, end) if x["zone"] == "APP41")["kwh"])

    def test_chauffage_des_autres_sites_inchange(self):
        start, end = billing.period_bounds(2026, 11)
        self.index("ch", [(start - 600, 236000.0), (end - 600, 236500.0)])
        r = billing.compute_postes(
            self.conn, billing.resolve_postes(self.SERIES),
            {"key": "x", "start": start, "end": end}, end + 86400,
        )
        self.assertAlmostEqual(next(x for x in r["chauffage"] if x["zone"] == "APP01")["kwh"], 500.0)

    def test_buanderie_comptee_depuis_sa_mise_en_service(self):
        start, end = billing.period_bounds(2026, 7)
        self.index("bk", [(start, 0.0), (billing.BUANDERIE_VALID_FROM - 3600, 37.0), (end - 600, 38.0)])
        self.index("bc", [(start, 0.0), (end - 600, 1.0)])
        r = billing.compute_postes(
            self.conn, billing.resolve_postes(self.SERIES),
            {"key": "2026-07", "start": start, "end": end}, end + 86400,
        )
        self.assertAlmostEqual(r["buanderie"][0]["energie"]["kwh"], 1.0)
        self.assertEqual(r["buanderie_depuis"], billing.BUANDERIE_VALID_FROM)

    def test_pas_de_buanderie_avant_sa_mise_en_service(self):
        start, end = billing.period_bounds(2026, 5)
        r = billing.compute_postes(
            self.conn, billing.resolve_postes(self.SERIES),
            {"key": "2026-05", "start": start, "end": end}, end + 86400,
        )
        self.assertEqual(r["buanderie"], [])
        self.assertTrue(r["buanderie_avant_mise_en_service"])


class TestExportPostes(unittest.TestCase):
    """Classeur et CSV : un tableau électricité avec la colonne buanderie,
    puis chauffage, eau chaude et buanderie."""

    def setUp(self):
        start, end = billing.period_bounds(2026, 8)
        self.payload = {
            "timezone": "Europe/Zurich",
            "generated_at": end + 3600,
            "periodes": [{"key": "2026-08", "label": "Août 2026", "start": start, "end": end}],
            "zones": [
                {"zone": "APP01", "label": "App 1", "periodes": {"2026-08": {
                    "reseau": {"kwh": 10.0, "alertes": []},
                    "solaire": {"kwh": 5.0, "alertes": []},
                    "total": 15.0, "facturable": True, "en_cours": False,
                }}},
                {"zone": "COMMUN", "label": "Communs", "periodes": {"2026-08": {
                    "reseau": {"kwh": 20.0, "alertes": []},
                    "solaire": {"kwh": 1.0, "alertes": []},
                    "total": 21.0, "facturable": True, "en_cours": False,
                }}},
            ],
            "postes": {"2026-08": {
                "en_cours": False, "buanderie_depuis": None,
                "buanderie_avant_mise_en_service": False,
                "chauffage": [
                    {"zone": "APP01", "label": "App 1", "compteur": "APP 01 compteur chaleur [5] (total)",
                     "en_cours": False, "kwh": 250.5, "releve_debut": 1000.0, "releve_fin": 1250.5,
                     "alertes": []},
                    {"zone": "COMMUN", "label": "Communs", "compteur": "Commun - Caisson (total)",
                     "en_cours": False, "kwh": 100.0, "releve_debut": 1.0, "releve_fin": 101.0,
                     "alertes": []},
                    {"zone": "COMMUN", "label": "Communs", "compteur": "Commun - Cuisine (total)",
                     "en_cours": False, "kwh": None, "releve_debut": None, "releve_fin": None,
                     "alertes": ["pas de relevé dans la période"]},
                ],
                "eau_chaude": [
                    {"zone": "APP01", "label": "App 1", "compteur": "APP 01 compteur eau chaude m^3 [6] (total)",
                     "en_cours": False, "kwh": 0.8754, "releve_debut": 10.0, "releve_fin": 10.8754,
                     "alertes": []},
                ],
                "buanderie": [
                    {"zone": "APP01", "label": "App 1", "en_cours": False,
                     "energie": {"kwh": 4.5, "alertes": []}, "cycles": {"kwh": 3.0, "alertes": []}},
                ],
            }},
        }

    def classeur(self, payload=None):
        buf = io.BytesIO()
        decompte_xlsx.build_decompte_workbook(payload or self.payload, "2026-08",
                                              "MS-PPE-Sequoia").save(buf)
        buf.seek(0)
        return load_workbook(buf)

    def ligne(self, ws, valeur, debut=1):
        for r in range(debut, ws.max_row + 1):
            if ws.cell(r, 1).value == valeur:
                return r
        self.fail(f"{valeur!r} introuvable")

    def test_onglets(self):
        self.assertEqual(
            self.classeur().sheetnames,
            ["Électricité", "Chauffage", "Eau chaude", "Buanderie",
             "Frais supplémentaires", "Relevés"],
        )

    def test_colonne_buanderie_dans_l_electricite(self):
        ws = self.classeur()["Électricité"]
        header = self.ligne(ws, "Zone")
        self.assertEqual(
            [ws.cell(header, c).value for c in range(1, 6)],
            ["Zone", "Consommation totale (kWh)", "Solaire (kWh)", "Réseau (kWh)", "Buanderie (kWh)"],
        )
        app1, communs = self.ligne(ws, "App 1"), self.ligne(ws, "Communs")
        self.assertEqual(ws.cell(app1, 5).value, 4.5)
        self.assertIsNone(ws.cell(communs, 5).value)
        self.assertEqual(ws.cell(self.ligne(ws, "Total immeuble"), 5).value, 4.5)
        # Les montants restent sur réseau et solaire, la buanderie n'y entre pas.
        self.assertEqual(ws.cell(header, 6).value, "Solaire HT (CHF)")
        self.assertEqual(ws.cell(header, 10).value, "État")

    def test_onglet_chauffage(self):
        ws = self.classeur()["Chauffage"]
        self.assertIn("MS-PPE-Sequoia", ws["A1"].value)
        self.assertEqual(
            [ws.cell(5, c).value for c in range(1, 7)],
            ["Zone", "Compteur", "Index début (kWh th)", "Index fin (kWh th)",
             "Énergie (kWh th)", "État"],
        )
        self.assertEqual(ws["B6"].value, "APP 01 compteur chaleur [5]")
        self.assertEqual(ws["E6"].value, 250.5)
        self.assertEqual(ws["F8"].value, "données incomplètes")
        total = self.ligne(ws, "Total immeuble")
        self.assertEqual(ws.cell(total, 5).value, 350.5)
        textes = "\n".join(str(ws.cell(r, 1).value) for r in range(total, ws.max_row + 1))
        self.assertIn("volume circulé n'est pas repris", textes)
        self.assertIn("Communs — Commun - Cuisine : pas de relevé dans la période", textes)

    def test_repartition_du_cout_de_la_pac(self):
        postes = self.payload["postes"]["2026-08"]
        postes["chauffage"] = postes["chauffage"][:2]          # tous relevés
        self.payload["zones"].append({"zone": "PAC", "label": "PAC", "periodes": {"2026-08": {
            "reseau": {"kwh": 400.0, "alertes": []}, "solaire": {"kwh": 100.0, "alertes": []},
            "total": 500.0, "facturable": True, "en_cours": False,
        }}})
        wb = self.classeur(ajouter_chiffrage(self.payload, "2026-08"))
        ws = wb["Chauffage"]
        # PAC : 100 × 0,12 + 400 × 0,25 = 112,00 HT, TVA 8,10 ; 80 % au chauffage.
        self.assertEqual([ws.cell(r, 2).value for r in (4, 5, 6)], [89.6, 6.48, 96.08])
        self.assertIn("80 % de l'électricité de la PAC (112,00 CHF HT)", ws["B7"].value)
        rep = self.ligne(ws, "Zone", debut=self.ligne(ws, "Total immeuble") + 1)
        self.assertEqual([ws.cell(rep, c).value for c in range(1, 7)],
                         ["Zone", "Énergie (kWh th)", "Part (%)", "Montant HT (CHF)", "TVA (CHF)",
                          "Montant TTC (CHF)"])
        # 89,60 × 250,5 / 350,5 = 64,04 ; 89,60 × 100 / 350,5 = 25,56.
        self.assertEqual([ws.cell(rep + 1, c).value for c in range(1, 7)],
                         ["App 1", 250.5, 71.47, 64.04, 4.63, 68.67])
        self.assertEqual([ws.cell(rep + 2, c).value for c in range(1, 7)],
                         ["Communs", 100.0, 28.53, 25.56, 1.85, 27.41])
        self.assertEqual([ws.cell(rep + 3, c).value for c in (1, 4, 5, 6)],
                         ["Total immeuble", 89.6, 6.48, 96.08])
        # La PAC n'est pas une ligne du récapitulatif.
        recap = wb["Récapitulatif"]
        self.assertEqual([recap.cell(r, 1).value for r in range(5, 8)],
                         ["App 1", "Communs", "Total immeuble"])



    def test_onglet_eau_chaude_au_litre_pres(self):
        ws = self.classeur()["Eau chaude"]
        self.assertEqual(ws["E5"].value, "Volume (m³)")
        self.assertEqual(ws["E6"].value, 0.875)
        self.assertEqual(ws["E6"].number_format, "#,##0.000")

    def test_onglet_buanderie(self):
        ws = self.classeur()["Buanderie"]
        self.assertEqual([ws.cell(5, c).value for c in range(1, 9)],
                         ["Zone", "Cycles", "Énergie (kWh)", "Cycles (CHF)", "Énergie HT (CHF)",
                          "TVA (CHF)", "Total TTC (CHF)", "État"])
        self.assertEqual([ws.cell(6, c).value for c in range(1, 9)],
                         ["App 1", 3.0, 4.5, None, None, None, None, "facturable"])

    def test_montants_de_la_buanderie(self):
        ws = self.classeur(ajouter_chiffrage(self.payload, "2026-08"))["Buanderie"]
        self.assertEqual(ws["B4"].value, 2.2)
        # 3 × 2,20 = 6,60 sans TVA ; 4,5 × 0,25 = 1,13, TVA 0,09.
        self.assertEqual([ws.cell(8, c).value for c in range(1, 8)],
                         ["App 1", 3.0, 4.5, 6.6, 1.13, 0.09, 7.82])

    def test_csv_en_sections(self):
        rows = decompte_export.export_csv_lignes(self.payload, "2026-08")
        titres = [r[0] for r in rows if len(r) == 1]
        self.assertEqual(titres, ["Électricité", "Chauffage", "Eau chaude", "Buanderie"])
        self.assertEqual(rows[1][-2], "Buanderie (kWh)")
        self.assertEqual(rows[2], ["Août 2026", "App 1", "15,00", "5,00", "10,00", "4,50", "facturable"])
        self.assertIn(["Août 2026", "Total immeuble", "", "", "", "350,50", ""], rows)
        self.assertIn(["Août 2026", "App 1", "APP 01 compteur eau chaude m^3 [6]",
                       "10,000", "10,875", "0,875", "facturable"], rows)
        self.assertIn(["Août 2026", "App 1", "3", "4,50", "facturable"], rows)

    def test_index_identiques_signales(self):
        postes = self.payload["postes"]["2026-08"]
        postes["eau_chaude"].append(dict(postes["eau_chaude"][0], zone="APP41", label="App 41",
                                         compteur="APP 41 compteur eau chaude m^3 [60!] (total)"))
        ws = self.classeur()["Eau chaude"]
        textes = "\n".join(str(ws.cell(r, 1).value) for r in range(1, ws.max_row + 1))
        self.assertIn("App 1 et App 41 : mêmes index", textes)

    def test_total_csv_des_valeurs_affichees(self):
        z = self.payload["zones"]
        z[0]["periodes"]["2026-08"]["total"] = 1.004
        z[1]["periodes"]["2026-08"]["total"] = 4.504
        rows = decompte_export.export_csv_lignes(self.payload, "2026-08")
        total = next(r for r in rows if r[1:2] == ["Total immeuble"])
        self.assertEqual(total[2], "5,50")

    def test_sans_postes_le_csv_reste_l_electricite(self):
        del self.payload["postes"]
        rows = decompte_export.export_csv_lignes(self.payload, "2026-08")
        self.assertEqual([r[0] for r in rows if len(r) == 1], ["Électricité"])
        self.assertNotIn("Buanderie (kWh)", rows[1])


if __name__ == "__main__":
    unittest.main()


class TestWallbox(unittest.TestCase):
    """Le compteur de la Wallbox de Sequoia démarre le 04.03.2026 à 20 h :
    avant, 0 kWh à facturer, pas un relevé manquant."""

    def setUp(self):
        self.conn = db.get_connection(":memory:")
        self.src = {"series_id": "W:w:total", "label": "Wallbox Energizähler (total)"}
        db.upsert_statistic_samples(self.conn, [
            ("W:w:total", billing.WALLBOX_VALID_FROM, 0.0),
            ("W:w:total", ts(2026, 3, 31, 23), 12.5),
        ])

    def lire(self, mois):
        start, end = billing.period_bounds(2026, mois)
        return billing_decompte._lecture_zone(self.conn, self.src, {"start": start, "end": end}, ts(2026, 6, 1))

    def test_avant_la_mise_en_service(self):
        lecture = self.lire(2)
        self.assertEqual(lecture["kwh"], 0.0)
        self.assertEqual(lecture["alertes"], [])

    def test_mois_de_la_mise_en_service(self):
        self.assertEqual(self.lire(3)["kwh"], 12.5)

    def test_autre_compteur_inchange(self):
        start, end = billing.period_bounds(2026, 2)
        autre = {"series_id": "W:w:total", "label": "App 1 (total)"}
        lecture = billing_decompte._lecture_zone(self.conn, autre, {"start": start, "end": end}, ts(2026, 6, 1))
        self.assertIsNone(lecture["kwh"])
