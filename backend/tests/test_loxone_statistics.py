"""La courbe de puissance de l'aperçu se décode depuis Statistics, pas
depuis les mesures du collecteur."""
import struct
import unittest
from unittest.mock import patch

from loxone_collector.loxone.statistics import (
    StatisticUnavailable,
    align_hourly,
    decode_statistic_body,
    find_stat_mapping,
    points_by_series,
)

HOUR = 1_700_002_800  # multiple de 3600


def _body(*pairs: tuple[int, float]) -> bytes:
    return b"".join(struct.pack("<Id", ts, value) for ts, value in pairs)


class TestDecode(unittest.TestCase):
    def test_enregistrements_little_endian(self):
        records = decode_statistic_body(_body((HOUR, 1.5), (HOUR + 3600, 2.25)))
        self.assertEqual(records, [(HOUR, 1.5), (HOUR + 3600, 2.25)])

    def test_taille_inattendue(self):
        with self.assertRaises(Exception):
            decode_statistic_body(b"\x00\x01")


class TestMapping(unittest.TestCase):
    def test_statistic_v2_a_la_racine(self):
        control = {"statisticV2": {"groups": [
            {"id": 4, "dataPoints": [{"output": "total"}, {"output": "actual"}]},
        ]}}
        self.assertEqual(find_stat_mapping(control, "actual"), ("4", "actual"))

    def test_repli_sous_details(self):
        control = {"details": {"statistic": {"groups": [
            {"id": "g", "dataPoints": [{"output": "actual"}]},
        ]}}}
        self.assertEqual(find_stat_mapping(control, "actual"), ("g", "actual"))

    def test_absent(self):
        self.assertIsNone(find_stat_mapping({"statisticV2": {"groups": []}}, "actual"))


class TestAlign(unittest.TestCase):
    def test_moyenne_dans_l_heure_et_fenetre(self):
        records = [
            (HOUR - 10, 99.0),
            (HOUR + 10, 1.0),
            (HOUR + 20, 3.0),
            (HOUR + 4000, 8.0),
        ]
        self.assertEqual(
            align_hourly(records, HOUR, HOUR + 3600),
            [(HOUR, 2.0)],
        )


class TestPoints(unittest.TestCase):
    def test_interoge_le_miniserver_pour_actual(self):
        structure = {"controls": {"c1": {"statisticV2": {"groups": [
            {"id": 1, "dataPoints": [{"output": "actual"}]},
        ]}}}}
        rows = [{
            "series_id": "site:c1:actual",
            "control_uuid": "c1",
            "state_name": "actual",
            "label": "Solaire (actual)",
        }]
        calls = []

        def fake(_client, control_uuid, group_id, output, from_ts, to_ts):
            calls.append((control_uuid, group_id, output, from_ts, to_ts))
            return [(HOUR + 10, 1.0), (HOUR + 20, 3.0)]

        with patch("loxone_collector.loxone.statistics._fetch_once", fake):
            points, missing = points_by_series(
                None, structure, rows, HOUR, HOUR + 7200, workers=1,
            )
        self.assertEqual(calls, [("c1", "1", "actual", HOUR, HOUR + 7200)])
        self.assertEqual(points["site:c1:actual"], [{"ts": HOUR, "value": 2.0}])
        self.assertEqual(missing, [])

    def test_output_sans_statistics(self):
        structure = {"controls": {"c1": {}}}
        rows = [{
            "series_id": "s",
            "control_uuid": "c1",
            "state_name": "actual",
            "label": "Compteur muet",
        }]
        with patch("loxone_collector.loxone.statistics._fetch_once") as fetch:
            points, missing = points_by_series(None, structure, rows, HOUR, HOUR + 3600)
        fetch.assert_not_called()
        self.assertEqual(points["s"], [])
        self.assertEqual(missing, ["Compteur muet"])

    def test_http_sans_historique_n_empeche_pas_les_autres(self):
        structure = {"controls": {
            "ok": {"statisticV2": {"groups": [{"id": 1, "dataPoints": [{"output": "actual"}]}]}},
            "ko": {"statisticV2": {"groups": [{"id": 2, "dataPoints": [{"output": "actual"}]}]}},
        }}
        rows = [
            {"series_id": "ok", "control_uuid": "ok", "state_name": "actual", "label": "OK"},
            {"series_id": "ko", "control_uuid": "ko", "state_name": "actual", "label": "KO"},
        ]

        def fake(_client, control_uuid, group_id, output, from_ts, to_ts):
            if control_uuid == "ko":
                raise StatisticUnavailable("HTTP 404")
            return [(HOUR + 5, 4.0)]

        with patch("loxone_collector.loxone.statistics._fetch_once", fake):
            points, missing = points_by_series(
                None, structure, rows, HOUR, HOUR + 3600, workers=2,
            )
        self.assertEqual(points["ok"], [{"ts": HOUR, "value": 4.0}])
        self.assertEqual(points["ko"], [])
        self.assertEqual(missing, ["KO"])
