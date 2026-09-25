"""Le poller ne garde que les états qui servent à visualiser une
consommation facturable : actual, total, totalNeg. Les compteurs glissants
et les contrôles qui ne sont pas des compteurs sont écartés."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loxone_client import endpoint_from_cloud_dns, extract_measurable_points


def _meter():
    return {
        "name": "App 1 Grid",
        "type": "Meter",
        "room": "r1",
        "cat": "c1",
        "states": {
            "actual": "1f90ea6f-0001-0001-0000000000000001",
            "total": "1f90ea6f-0001-0001-0000000000000002",
            "totalNeg": "1f90ea6f-0001-0001-0000000000000003",
            "totalDay": "1f90ea6f-0001-0001-0000000000000004",
            "totalWeek": "1f90ea6f-0001-0001-0000000000000005",
            "totalMonth": "1f90ea6f-0001-0001-0000000000000006",
            "totalYear": "1f90ea6f-0001-0001-0000000000000007",
            "totalNegDay": "1f90ea6f-0001-0001-0000000000000008",
            "jLocked": "1f90ea6f-0001-0001-0000000000000009",
        },
    }


class TestBillableStates(unittest.TestCase):
    def _structure(self, controls):
        return {
            "controls": controls,
            "rooms": {"r1": {"name": "App 1"}},
            "cats": {"c1": {"name": "Energie"}},
        }

    def test_meter_keeps_only_actual_total_totalneg(self):
        points = extract_measurable_points(
            self._structure({"ctrl": _meter()}),
            include_types=["Meter"],
        )
        self.assertEqual(
            sorted(p.state_name for p in points),
            ["actual", "total", "totalNeg"],
        )

    def test_non_meter_controls_are_dropped(self):
        controls = {
            "m": _meter(),
            "efm": {
                "name": "Moniteur",
                "type": "EFM",
                "room": "r1",
                "cat": "c1",
                "states": {"Gpwr": "1f90ea6f-0002-0001-0000000000000001"},
            },
            "sw": {
                "name": "Relai",
                "type": "Switch",
                "room": "r1",
                "cat": "c1",
                "states": {"active": "1f90ea6f-0003-0001-0000000000000001"},
            },
        }
        points = extract_measurable_points(
            self._structure(controls), include_types=["Meter"]
        )
        self.assertTrue(all(p.control_type == "Meter" for p in points))
        self.assertEqual(len(points), 3)

    def test_building_flow_monitor_keeps_grouped_nodes(self):
        nodes = [
            {"nodeType": "Production", "title": "Production", "actualEfmState": "215545d5-03cb-a32a-fffffe9cefa2f75d"},
            {"nodeType": "Grid", "title": "Réseau", "actualEfmState": "215545d5-03cb-a32b-fffffe9cefa2f75d"},
            {"nodeType": "Load", "title": "Commun", "actualEfmState": "215545d5-03cb-a32c-fffffe9cefa2f75d"},
            {"nodeType": "Group", "title": "Appartements", "actualEfmState": "215545d5-03cb-a32d-fffffe9cefa2f75d"},
        ]
        states = {f"actual{i}": n["actualEfmState"] for i, n in enumerate(nodes)}
        states["jLocked"] = "215545d5-03cb-a330-fffffe9cefa2f75d"
        controls = {
            "bat": {
                "name": "Moniteur de flux d'énergie",
                "type": "EFM",
                "room": "r1",
                "cat": "c1",
                "details": {"nodes": nodes},
                "states": states,
            },
            "zone": {
                "name": "App 1",
                "type": "EFM",
                "room": "r1",
                "cat": "c1",
                "details": {"nodes": nodes[:1]},
                "states": {"actual0": nodes[0]["actualEfmState"]},
            },
        }
        points = extract_measurable_points(
            self._structure(controls), include_types=["Meter", "EFM"]
        )
        self.assertEqual(sorted(p.flow_role for p in points), ["conso", "conso", "reseau", "solaire"])
        self.assertEqual({p.control_name for p in points}, {"Production", "Réseau", "Commun", "Appartements"})

    def test_cloud_dns_https_endpoint(self):
        host, port = endpoint_from_cloud_dns(
            {"IPHTTPS": "168.119.185.175:53581"}, "504f94d0e6da"
        )
        self.assertEqual(host, "168-119-185-175.504f94d0e6da.dyndns.loxonecloud.com")
        self.assertEqual(port, 53581)
        self.assertIsNone(endpoint_from_cloud_dns({}, "504f94d0e6da"))


if __name__ == "__main__":
    unittest.main()
