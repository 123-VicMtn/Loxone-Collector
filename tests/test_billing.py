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
import sqlite3
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import billing
import db


def ts(year, month, day, hour=0, tz="Europe/Zurich"):
    return int(datetime(year, month, day, hour, tzinfo=ZoneInfo(tz)).timestamp())


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
        res = billing._reading_delta(self.conn, "m", start, end, ts(2026, 6, 1))
        # Le relevé du 5 février appartient au mois SUIVANT : le compter
        # ici le ferait apparaître deux fois sur deux factures.
        self.assertAlmostEqual(res["kwh"], 80.0)
        self.assertEqual(res["alertes"], [])

    def test_releve_anterieur_a_la_periode_sert_de_point_de_depart(self):
        self.hourly("m", [(ts(2025, 12, 31, 23), 100.0), (ts(2026, 1, 20), 150.0)])
        start, end = billing.period_bounds(2026, 1)
        res = billing._reading_delta(self.conn, "m", start, end, ts(2026, 6, 1))
        self.assertAlmostEqual(res["kwh"], 50.0)

    def test_reset_de_compteur_rend_la_periode_non_calculable(self):
        # Cas réel : le compteur "Réseau" de MS-Arlopi est passé de 9795 à
        # 1344 le 26.03.2026. Un delta brut donnerait -8451 kWh, une somme
        # de deltas positifs donnerait un chiffre plausible mais faux.
        self.hourly("m", [(ts(2025, 12, 31, 23), 90.0),
                          (ts(2026, 1, 5), 100.0), (ts(2026, 1, 20), 9795.0),
                          (ts(2026, 1, 22), 1344.0), (ts(2026, 1, 25), 1500.0)])
        start, end = billing.period_bounds(2026, 1)
        res = billing._reading_delta(self.conn, "m", start, end, ts(2026, 6, 1))
        self.assertIsNone(res["kwh"])
        self.assertEqual(len(res["ruptures"]), 1)

    def test_micro_baisse_darrondi_ignoree(self):
        # Écart d'arrondi entre le poller live (pleine précision) et
        # l'historique Statistics importé : ne doit PAS passer pour un reset.
        self.hourly("m", [(ts(2025, 12, 31, 23), 2000.0),
                          (ts(2026, 1, 5), 2048.0454678), (ts(2026, 1, 20), 2048.045),
                          (ts(2026, 1, 25), 2100.0)])
        start, end = billing.period_bounds(2026, 1)
        res = billing._reading_delta(self.conn, "m", start, end, ts(2026, 6, 1))
        self.assertIsNotNone(res["kwh"])
        self.assertNotIn("ruptures", res)

    def test_trou_de_collecte_signale(self):
        self.hourly("m", [(ts(2025, 11, 1), 50.0), (ts(2026, 1, 20), 150.0)])
        start, end = billing.period_bounds(2026, 1)
        res = billing._reading_delta(self.conn, "m", start, end, ts(2026, 6, 1))
        self.assertTrue(any("trou de collecte" in a for a in res["alertes"]))

    def test_pas_de_fausse_alerte_sur_une_periode_en_cours(self):
        # Mois en cours : la borne de fin est dans le futur, le dernier
        # relevé est forcément "vieux" par rapport à elle.
        self.hourly("m", [(ts(2026, 1, 1), 100.0), (ts(2026, 1, 20), 150.0)])
        start, end = billing.period_bounds(2026, 1)
        res = billing._reading_delta(self.conn, "m", start, end, ts(2026, 1, 20, 12))
        self.assertEqual(res["alertes"], [])


class TestTarifsEtMontants(unittest.TestCase):
    TARIFS = [
        {"valid_from": "2025-01-01", "prix_reseau": 0.25, "prix_solaire": 0.12, "taux_tva": 8.1},
        {"valid_from": "2026-01-01", "prix_reseau": 0.30, "prix_solaire": 0.15, "taux_tva": 8.1},
    ]

    def test_tarif_choisi_sur_le_debut_de_periode(self):
        # Une hausse au 01.01.2026 ne doit pas s'appliquer à décembre 2025.
        t = billing.tarif_for(self.TARIFS, billing.period_bounds(2025, 12)[0])
        self.assertEqual(t["prix_reseau"], 0.25)
        t = billing.tarif_for(self.TARIFS, billing.period_bounds(2026, 1)[0])
        self.assertEqual(t["prix_reseau"], 0.30)

    def test_aucun_tarif_avant_la_premiere_date(self):
        self.assertIsNone(billing.tarif_for(self.TARIFS, billing.period_bounds(2024, 1)[0]))

    def test_montants(self):
        m = billing.montants(100.0, 50.0, self.TARIFS[1])
        self.assertAlmostEqual(m["ht"], 100 * 0.30 + 50 * 0.15)
        self.assertAlmostEqual(m["tva"], m["ht"] * 0.081)
        self.assertAlmostEqual(m["ttc"], m["ht"] + m["tva"])

    def test_montant_absent_plutot_que_zero(self):
        # Un montant nul faute de données ne doit pas se confondre avec un
        # montant nul réellement dû.
        self.assertIsNone(billing.montants(None, 50.0, self.TARIFS[1])["ttc"])
        self.assertIsNone(billing.montants(100.0, 50.0, None)["ttc"])


if __name__ == "__main__":
    unittest.main()
