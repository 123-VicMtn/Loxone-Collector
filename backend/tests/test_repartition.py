"""
Tests pour repartition.py (reconstruction de la part solaire/réseau).

Ce qui vaut d'être verrouillé ici :
  - la propriété qui rend la méthode facturable : réseau + solaire redonne
    EXACTEMENT la consommation mesurée de la zone. Si un refactor casse ça,
    on facturerait des kWh inventés ou on en perdrait ;
  - la nécessité du compteur d'INJECTION : sans lui, le solaire revendu au
    réseau serait compté comme autoconsommé et la part solaire de chaque
    lot serait surestimée ;
  - le garde-fou `audit_series_loxone`, qui doit rester MUET sur une
    installation saine (sinon il rendrait Arlopi non facturable) et détecter
    la pathologie réelle de MS-PPE-Sequoia (des achats au réseau pendant les
    heures où le bâtiment n'importe rien) ;
  - le refus de verser au réseau, en silence, la consommation d'une heure
    dont on ne connaît pas la part solaire.

Utilise unittest (stdlib), comme les autres tests du dépôt.
"""
import sqlite3
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import repartition


def ts(year, month, day, hour=0):
    return int(datetime(year, month, day, hour, tzinfo=ZoneInfo("Europe/Zurich")).timestamp())


def batiment(prod="prod", imp="imp", exp="exp"):
    """Structure equivalente a billing.resolve_batiment."""
    def src(sid):
        return {"series_id": sid, "label": sid, "unit": "kWh"} if sid else None
    return {"production": src(prod), "reseau_import": src(imp), "reseau_export": src(exp)}


def zone(nom, reseau=None, solaire=None, controle=None):
    def src(sid):
        return {"series_id": sid, "label": sid, "unit": "kWh"} if sid else None
    return {"zone": nom, "label": nom,
            "sources": {"reseau": src(reseau), "solaire": src(solaire),
                        "controle": src(controle)}}


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(db.SCHEMA)

    def tearDown(self):
        self.conn.close()

    def index(self, series_id, points):
        """Écrit une série cumulative : (ts, valeur d'index)."""
        self.conn.executemany(
            "INSERT INTO readings_hourly (series_id, ts, avg_value, max_value) VALUES (?, ?, ?, ?)",
            [(series_id, t, v, v) for t, v in points],
        )
        self.conn.commit()

    def cumule(self, series_id, t0, deltas, depart=0.0):
        """Écrit une série cumulative à partir d'énergies horaires."""
        points, v = [(t0, depart)], depart
        for i, d in enumerate(deltas, start=1):
            v += d
            points.append((t0 + i * 3600, v))
        self.index(series_id, points)


class TestHourlyDeltas(Base):
    def test_difference_entre_releves_consecutifs(self):
        self.index("m", [(ts(2026, 1, 1), 10.0), (ts(2026, 1, 1, 1), 12.5),
                         (ts(2026, 1, 1, 2), 13.0)])
        d = repartition.hourly_deltas(self.conn, "m", ts(2026, 1, 1), ts(2026, 1, 2))
        self.assertAlmostEqual(d[ts(2026, 1, 1, 1)], 2.5)
        self.assertAlmostEqual(d[ts(2026, 1, 1, 2)], 0.5)

    def test_releve_precedant_la_fenetre_amorce_la_premiere_heure(self):
        # Sans cette amorce, la première heure de la période serait perdue.
        self.index("m", [(ts(2025, 12, 31, 23), 100.0), (ts(2026, 1, 1), 103.0)])
        d = repartition.hourly_deltas(self.conn, "m", ts(2026, 1, 1), ts(2026, 1, 2))
        self.assertAlmostEqual(d[ts(2026, 1, 1)], 3.0)

    def test_micro_baisse_darrondi_ramenee_a_zero(self):
        # Écart d'arrondi entre poller live et historique Loxone : ne doit
        # pas produire d'énergie négative.
        self.index("m", [(ts(2026, 1, 1), 10.0), (ts(2026, 1, 1, 1), 9.9964)])
        d = repartition.hourly_deltas(self.conn, "m", ts(2026, 1, 1), ts(2026, 1, 2))
        self.assertEqual(d[ts(2026, 1, 1, 1)], 0.0)


class TestPartSolaire(Base):
    def test_part_solaire_du_mix(self):
        t0 = ts(2026, 6, 1)
        # Production 10, injection 4 -> 6 autoconsommés ; import 6.
        # Consommation du bâtiment = 6 + 10 - 4 = 12 -> part solaire 50 %.
        self.cumule("prod", t0, [10.0]); self.cumule("exp", t0, [4.0])
        self.cumule("imp", t0, [6.0])
        info = repartition.solar_fraction(self.conn, batiment(), t0, t0 + 7200)
        self.assertAlmostEqual(info["fraction"][t0 + 3600], 0.5)

    def test_sans_injection_la_part_solaire_serait_surestimee(self):
        # Même heure que ci-dessus, mais en ignorant l'injection : 10/16 =
        # 62,5 % au lieu de 50 %. Ce test fige la raison d'être du
        # compteur d'injection dans le calcul.
        t0 = ts(2026, 6, 1)
        self.cumule("prod", t0, [10.0]); self.cumule("exp", t0, [0.0])
        self.cumule("imp", t0, [6.0])
        info = repartition.solar_fraction(self.conn, batiment(), t0, t0 + 7200)
        self.assertAlmostEqual(info["fraction"][t0 + 3600], 10.0 / 16.0)

    def test_nuit_part_solaire_nulle(self):
        t0 = ts(2026, 1, 15)
        self.cumule("prod", t0, [0.0]); self.cumule("exp", t0, [0.0])
        self.cumule("imp", t0, [3.0])
        info = repartition.solar_fraction(self.conn, batiment(), t0, t0 + 7200)
        self.assertAlmostEqual(info["fraction"][t0 + 3600], 0.0)

    def test_part_bornee_a_un_et_comptee(self):
        # Tolérances de compteur : la production dépasse la consommation
        # déduite. On borne à 100 % au lieu de propager un ratio > 1.
        t0 = ts(2026, 6, 1)
        self.cumule("prod", t0, [10.0]); self.cumule("exp", t0, [0.0])
        self.cumule("imp", t0, [-0.0])
        self.conn.execute("UPDATE readings_hourly SET max_value = 0 WHERE series_id='imp'")
        self.conn.commit()
        info = repartition.solar_fraction(self.conn, batiment(), t0, t0 + 7200)
        self.assertLessEqual(info["fraction"][t0 + 3600], 1.0)

    def test_compteur_batiment_manquant_rend_le_calcul_indisponible(self):
        t0 = ts(2026, 6, 1)
        info = repartition.solar_fraction(self.conn, batiment(exp=None), t0, t0 + 7200)
        self.assertIn("reseau_export", info["indisponible"])
        self.assertEqual(info["fraction"], {})


class TestSplitZone(Base):
    def test_reseau_plus_solaire_egale_la_consommation_mesuree(self):
        # LA propriété qui rend la méthode facturable.
        t0 = ts(2026, 6, 1)
        self.cumule("prod", t0, [10.0, 8.0, 0.0]); self.cumule("exp", t0, [4.0, 0.0, 0.0])
        self.cumule("imp", t0, [6.0, 2.0, 5.0])
        self.cumule("lot", t0, [1.0, 2.5, 0.75], depart=500.0)
        info = repartition.solar_fraction(self.conn, batiment(), t0, t0 + 4 * 3600)
        r = repartition.split_zone(self.conn, "lot", info["fraction"], t0, t0 + 4 * 3600)
        self.assertAlmostEqual(r["total"], 4.25)
        self.assertAlmostEqual(r["reseau"] + r["solaire"], r["total"], places=9)
        self.assertEqual(r["non_reparti"], 0.0)

    def test_part_solaire_appliquee_heure_par_heure(self):
        t0 = ts(2026, 6, 1)
        # Heure 1 : mix 50 % solaire. Heure 2 : 100 % solaire.
        self.cumule("prod", t0, [10.0, 8.0]); self.cumule("exp", t0, [4.0, 0.0])
        self.cumule("imp", t0, [6.0, 0.0])
        self.cumule("lot", t0, [2.0, 3.0])
        info = repartition.solar_fraction(self.conn, batiment(), t0, t0 + 3 * 3600)
        r = repartition.split_zone(self.conn, "lot", info["fraction"], t0, t0 + 3 * 3600)
        self.assertAlmostEqual(r["solaire"], 2.0 * 0.5 + 3.0 * 1.0)
        self.assertAlmostEqual(r["reseau"], 2.0 * 0.5)

    def test_heure_sans_part_connue_nest_pas_versee_au_reseau(self):
        t0 = ts(2026, 6, 1)
        self.cumule("lot", t0, [1.0, 2.0])
        r = repartition.split_zone(self.conn, "lot", {}, t0, t0 + 3 * 3600)
        self.assertAlmostEqual(r["total"], 3.0)
        self.assertAlmostEqual(r["non_reparti"], 3.0)
        self.assertEqual(r["reseau"], 0.0)
        self.assertEqual(r["solaire"], 0.0)
        self.assertEqual(r["heures_sans_part"], 2)


class TestGardeFou(Base):
    def setUp(self):
        super().setUp()
        self.t0 = ts(2026, 6, 1)

    def test_installation_saine_reste_facturable(self):
        # Arlopi : les zones n'achètent au réseau que quand le bâtiment
        # importe, et le solaire est bien attribué. Le garde-fou doit se
        # taire, sinon il casserait un site qui fonctionne.
        self.cumule("imp", self.t0, [0.0, 5.0])      # h1 sans import, h2 avec
        self.cumule("prod", self.t0, [8.0, 0.0])     # h1 produit, h2 non
        self.cumule("exp", self.t0, [0.0, 0.0])
        self.cumule("zg", self.t0, [0.0, 3.0])       # réseau seulement en h2
        self.cumule("zs", self.t0, [6.0, 0.0])       # solaire seulement en h1
        zones = [zone("APP1", reseau="zg", solaire="zs", controle="zc")]
        a = repartition.audit_series_loxone(self.conn, zones, batiment(),
                                            self.t0, self.t0 + 3 * 3600)
        self.assertTrue(a["testable"])
        self.assertTrue(a["fiable"], a["raisons"])
        self.assertEqual(a["heures_grid_impossible"], 0)

    def test_achat_reseau_sans_import_du_batiment_est_detecte(self):
        # La pathologie réelle de MS-PPE-Sequoia.
        self.cumule("imp", self.t0, [0.0, 0.0])      # le bâtiment n'importe rien
        self.cumule("prod", self.t0, [8.0, 9.0])
        self.cumule("exp", self.t0, [1.0, 1.0])
        self.cumule("zg", self.t0, [3.0, 4.0])       # ... mais la zone "achète"
        self.cumule("zs", self.t0, [0.0, 0.0])
        zones = [zone("APP1", reseau="zg", solaire="zs", controle="zc")]
        a = repartition.audit_series_loxone(self.conn, zones, batiment(),
                                            self.t0, self.t0 + 3 * 3600)
        self.assertFalse(a["fiable"])
        self.assertEqual(a["heures_grid_impossible"], 2)
        self.assertAlmostEqual(a["kwh_grid_impossible"], 7.0)
        self.assertTrue(any("impossible" in r for r in a["raisons"]))

    def test_production_sans_solaire_attribue_est_detectee(self):
        self.cumule("imp", self.t0, [5.0, 5.0])
        self.cumule("prod", self.t0, [8.0, 9.0])     # production franche
        self.cumule("exp", self.t0, [0.0, 0.0])
        self.cumule("zg", self.t0, [3.0, 4.0])
        self.cumule("zs", self.t0, [0.0, 0.0])       # aucun solaire attribué
        zones = [zone("APP1", reseau="zg", solaire="zs", controle="zc")]
        a = repartition.audit_series_loxone(self.conn, zones, batiment(),
                                            self.t0, self.t0 + 3 * 3600)
        self.assertFalse(a["fiable"])
        self.assertEqual(a["heures_prod_sans_solaire"], 2)

    def test_sans_serie_par_zone_rien_a_auditer(self):
        self.cumule("imp", self.t0, [5.0]); self.cumule("prod", self.t0, [1.0])
        self.cumule("exp", self.t0, [0.0])
        zones = [zone("APP1", controle="zc")]
        a = repartition.audit_series_loxone(self.conn, zones, batiment(),
                                            self.t0, self.t0 + 2 * 3600)
        self.assertFalse(a["testable"])


class TestEvaluerSite(Base):
    """Le choix de la source de répartition, site par site.

    Les deux scénarios reproduits ici sont ceux des deux installations
    réelles, et ils demandent des décisions OPPOSÉES : c'est le bouclage du
    bilan qui les sépare.
    """

    def setUp(self):
        super().setUp()
        self.t0 = ts(2026, 6, 1)

    def _batiment_qui_boucle(self):
        # 2 heures : import 5+0, production 0+10, injection 0+2.
        # Consommation déduite = 5 + 10 - 2 = 13 kWh.
        self.cumule("imp", self.t0, [5.0, 0.0])
        self.cumule("prod", self.t0, [0.0, 10.0])
        self.cumule("exp", self.t0, [0.0, 2.0])
        self.cumule("zc", self.t0, [5.0, 8.0])   # 13 kWh mesurés : bilan bouclé

    def test_series_loxone_incoherentes_declenchent_la_repartition_calculee(self):
        # Cas MS-PPE-Sequoia : le bilan boucle, donc les compteurs de
        # bâtiment sont exploitables, et l'audit peut condamner les séries
        # de zone -- ici la zone "achète" 4 kWh à l'heure 2 alors que le
        # bâtiment n'importe rien.
        self._batiment_qui_boucle()
        self.cumule("zg", self.t0, [5.0, 4.0])
        self.cumule("zs", self.t0, [0.0, 0.0])
        zones = [zone("APP1", reseau="zg", solaire="zs", controle="zc")]
        ev = repartition.evaluer_site(self.conn, zones, batiment(),
                                      self.t0, self.t0 + 3 * 3600)
        self.assertTrue(ev["bilan_exploitable"])
        self.assertEqual(ev["source_recommandee"], "calculee")
        self.assertTrue(ev["confiance_verifiee"])

    def test_series_loxone_saines_sont_conservees(self):
        self._batiment_qui_boucle()
        self.cumule("zg", self.t0, [5.0, 0.0])   # n'achète que quand le bâtiment importe
        self.cumule("zs", self.t0, [0.0, 8.0])   # solaire attribué pendant la production
        zones = [zone("APP1", reseau="zg", solaire="zs", controle="zc")]
        ev = repartition.evaluer_site(self.conn, zones, batiment(),
                                      self.t0, self.t0 + 3 * 3600)
        self.assertEqual(ev["source_recommandee"], "loxone")
        self.assertTrue(ev["confiance_verifiee"])

    def test_bilan_qui_ne_boucle_pas_interdit_daccuser_les_series(self):
        # Cas MS-Arlopi : le compteur réseau du bâtiment est posé à
        # l'onduleur, pas sur l'alimentation. Ses heures "sans import" ne
        # prouvent rien sur les zones -- l'audit doit se taire (régression
        # réelle : il déclarait 1064 heures impossibles sur un site dont
        # les séries avaient été validées contre le bloc EFM).
        self.cumule("imp", self.t0, [0.0, 0.0])   # compteur non représentatif
        self.cumule("prod", self.t0, [0.0, 10.0])
        self.cumule("exp", self.t0, [0.0, 2.0])
        self.cumule("zc", self.t0, [40.0, 40.0])  # zones bien au-delà : ne boucle pas
        self.cumule("zg", self.t0, [20.0, 20.0])
        self.cumule("zs", self.t0, [0.0, 0.0])
        zones = [zone("APP1", reseau="zg", solaire="zs", controle="zc")]
        ev = repartition.evaluer_site(self.conn, zones, batiment(),
                                      self.t0, self.t0 + 3 * 3600)
        self.assertFalse(ev["bilan_exploitable"])
        self.assertFalse(ev["audit"]["testable"])
        self.assertEqual(ev["source_recommandee"], "loxone")
        # Le point clé : on ne prétend PAS avoir vérifié.
        self.assertFalse(ev["confiance_verifiee"])
        self.assertTrue(any("bouclage" in r for r in ev["audit"]["raisons"]))

    def test_seuil_de_bouclage(self):
        self.assertTrue(repartition.bilan_exploitable({"ecart_pct": -6.3}))
        self.assertTrue(repartition.bilan_exploitable({"ecart_pct": 14.9}))
        self.assertFalse(repartition.bilan_exploitable({"ecart_pct": 138.7}))
        self.assertFalse(repartition.bilan_exploitable({"ecart_pct": None}))


class TestQualiteBilan(Base):
    def test_ecart_de_bouclage(self):
        t0 = ts(2026, 6, 1)
        self.cumule("prod", t0, [10.0]); self.cumule("exp", t0, [4.0])
        self.cumule("imp", t0, [6.0])                # bâtiment : 12 kWh
        self.cumule("zc", t0, [11.4])                # zones : 11,4 kWh
        info = repartition.solar_fraction(self.conn, batiment(), t0, t0 + 7200)
        q = repartition.qualite_bilan(self.conn, [zone("APP1", controle="zc")],
                                      batiment(), info, t0, t0 + 7200)
        self.assertAlmostEqual(q["conso_batiment"], 12.0)
        self.assertAlmostEqual(q["conso_zones"], 11.4)
        self.assertAlmostEqual(q["ecart_pct"], -5.0)


if __name__ == "__main__":
    unittest.main()
