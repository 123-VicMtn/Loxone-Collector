"""
Tests ciblés pour classification.py, en particulier les changements du
2026-08-27 (Dashboard énergie) : reconnaissance du bloc Loxone "EFM"
(Moniteur de flux d'énergie), règle batterie, et correction du pattern
d'appartement pour "Appartement N" (en plus de "App N"). Voir CLAUDE.md,
section "Dashboard énergie", pour le contexte.

Utilise unittest (stdlib) plutôt qu'un framework externe -- pas de
dépendance de test dans ce projet à ce jour.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classification import extract_apartment, guess_resource_type


class TestExtractApartment(unittest.TestCase):
    def test_app_with_space(self):
        self.assertEqual(extract_apartment("App 1 Grid"), "APP1")

    def test_app_without_space(self):
        self.assertEqual(extract_apartment("APP01"), "APP01")

    def test_appartement_full_word_converges_with_app(self):
        # Bug réel trouvé sur MS-Arlopi : "Appartement 1" (compteur bare)
        # doit tomber dans la même zone que "App 1 Grid"/"App 1 Solaire".
        self.assertEqual(extract_apartment("Appartement 1"), "APP1")
        self.assertEqual(extract_apartment("Appartement 1"), extract_apartment("App 1 Grid"))

    def test_non_numeric_zones(self):
        self.assertEqual(extract_apartment("Commerce"), "COMMERCE")
        self.assertEqual(extract_apartment("Rez Jardin"), "REZJARDIN")
        self.assertEqual(extract_apartment("Communs"), "COMMUN")

    def test_no_match(self):
        self.assertEqual(extract_apartment("Réseau"), "")
        self.assertEqual(extract_apartment("Production"), "")
        self.assertEqual(extract_apartment(""), "")


class TestGuessResourceType(unittest.TestCase):
    def test_efm_control_type_wins_regardless_of_label(self):
        # Le bloc EFM est reconnu par son control_type, pas par son label
        # (qui reprend souvent juste le nom de la zone, ex: "App 1").
        self.assertEqual(guess_resource_type("App 1", control_type="EFM"), "energie_flux")
        self.assertEqual(guess_resource_type("Moniteur de flux d'énergie", control_type="EFM"), "energie_flux")

    def test_battery_keyword(self):
        self.assertEqual(guess_resource_type("Batterie", control_type="Meter"), "energie_batterie")
        self.assertEqual(guess_resource_type("Accumulateur bâtiment"), "energie_batterie")

    def test_solar_keyword_still_works(self):
        self.assertEqual(guess_resource_type("App 1 Solaire", control_type="Meter"), "energie_solaire")

    def test_grid_keyword_still_works(self):
        self.assertEqual(guess_resource_type("App 1 Grid", control_type="Meter"), "energie_reseau")

    def test_unrecognized_meter_falls_back_to_energie_consommee(self):
        # Cas réel non corrigé volontairement (voir CLAUDE.md) : "Production"
        # sans le mot-clé solaire reste en fourre-tout, à corriger via /admin.
        self.assertEqual(guess_resource_type("Production", control_type="Meter"), "energie_consommee")

    def test_no_match_no_meter_returns_autre(self):
        self.assertEqual(guess_resource_type("Éclairage salon", control_type="LightControllerV2"), "autre")


if __name__ == "__main__":
    unittest.main()
