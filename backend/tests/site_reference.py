"""
Site synthétique « Ref », déterministe, qui sert de référence aux tests de
caractérisation (tests/test_reference_decompte.py) et aux tests de route.

Deux mois de Statistics horaires, choisis pour passer par les chemins du
décompte qui comptent :

- septembre 2026 : séries Grid/Solaire du Miniserver justes, bilan du
  bâtiment qui boucle -> source « loxone », confiance vérifiée ;
- octobre 2026 : séries fausses à la manière de Sequoia (production brute
  répartie, injection comptée en Grid) -> répartition recalculée ; le
  compteur de l'APP2 est remplacé le 20 (rupture d'index) ;
- chauffage, eau chaude et buanderie par appartement sur les deux mois.

Un second site, « Autre », n'a qu'un compteur : il sert aux tests d'accès.
"""
from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from loxone_collector import db

TZ = ZoneInfo("Europe/Zurich")
SITE = "Ref"
AUTRE = "Autre"


def ts(year, month, day, hour=0):
    return int(datetime(year, month, day, hour, tzinfo=TZ).timestamp())


DEBUT = ts(2026, 8, 31, 22)
FIN = ts(2026, 11, 1, 2)
# « Maintenant » des tests : le mois d'octobre est clos.
NOW = ts(2026, 11, 5, 12)
REMPLACEMENT_APP2 = ts(2026, 10, 20, 9)

ZONES = ("APP1", "APP2", "COMMUN")
LABELS = {"APP1": "App 1", "APP2": "App 2", "COMMUN": "Communs"}

# (série, libellé, apartment, resource_type, unité, état)
SERIES = [
    ("prod", "Production (total)", "", "energie_solaire", "kWh", "total"),
    ("imp", "Réseau (total)", "", "energie_reseau", "kWh", "total"),
    ("exp", "Réseau (totalNeg)", "", "energie_reseau", "kWh", "totalNeg"),
]
for _z in ZONES:
    _l = LABELS[_z]
    _ctrl = "Commun (total)" if _z == "COMMUN" else f"Appartement {_l[-1]} (total)"
    SERIES += [
        (f"{_z}:c", _ctrl, _z, "energie_consommee", "kWh", "total"),
        (f"{_z}:g", f"{_l} Grid (total)", _z, "energie_reseau", "kWh", "total"),
        (f"{_z}:s", f"{_l} Solaire (total)", _z, "energie_solaire", "kWh", "total"),
    ]
for _z in ("APP1", "APP2"):
    _n = _z[-1]
    SERIES += [
        (f"{_z}:ch", f"Chauffage App {_n} (total)", _z, "energie_consommee", "kWhth", "total"),
        (f"{_z}:ec", f"Eau chaude App {_n} (total)", _z, "eau_chaude", "m³", "total"),
        (f"{_z}:be", f"CEnergieApp{_n} (total)", _z, "energie_consommee", "kWh", "total"),
        (f"{_z}:bc", f"CNrMachineApp{_n} (total)", _z, "energie_consommee", "", "total"),
    ]


def _heure_locale(t: int) -> int:
    return datetime.fromtimestamp(t, TZ).hour


def _increments(t: int, i: int) -> dict[str, float]:
    """Énergie de l'heure qui commence à `t`, pour chaque série."""
    h = _heure_locale(t)
    octobre = t >= ts(2026, 10, 1)
    conso = {
        "APP1": 0.4 + (0.3 if 18 <= h <= 22 else 0.0),
        "APP2": 0.6 + (0.2 if i % 5 == 0 else 0.0),
        "COMMUN": 0.25,
    }
    pic = 2.0 if octobre else 3.5
    prod = round(max(0.0, pic * math.sin(math.pi * (h - 7) / 12)) if 7 <= h <= 19 else 0.0, 3)
    total = sum(conso.values())
    auto = min(prod, total)
    exp = prod - auto
    imp = total - auto

    inc = {"prod": prod, "imp": imp, "exp": exp}
    for z, c in conso.items():
        part = c / total
        inc[f"{z}:c"] = c
        if octobre:
            # Bloc faux : production brute répartie, injection sur Grid.
            inc[f"{z}:s"] = prod * part
            inc[f"{z}:g"] = (imp + exp) * part
        else:
            inc[f"{z}:s"] = auto * part
            inc[f"{z}:g"] = imp * part
    for z, chaud in (("APP1", 0.3), ("APP2", 0.5)):
        inc[f"{z}:ch"] = chaud * (2.0 if octobre else 0.5)
        inc[f"{z}:ec"] = 0.002 if 6 <= h <= 22 else 0.0
        cycle = 1.0 if i % (48 if z == "APP1" else 72) == 30 else 0.0
        inc[f"{z}:bc"] = cycle
        inc[f"{z}:be"] = 1.2 * cycle
    return inc


def construire(conn) -> None:
    """Écrit les deux sites dans la base `conn` (schéma déjà créé)."""
    for cle, label, apt, rtype, unit, state in SERIES:
        uuid = cle.replace(":", "-")
        db.upsert_series_meta(
            conn, f"{SITE}:{uuid}:{state}", SITE, uuid, state, label,
            "", "Energie", "Meter", unit, apt, rtype,
        )
    ids = {cle: f"{SITE}:{cle.replace(':', '-')}:{state}" for cle, _, _, _, _, state in SERIES}

    index = {cle: 1000.0 + 100 * n for n, (cle, *_rest) in enumerate(SERIES)}
    rows = []
    t, i = DEBUT, 0
    while t <= FIN:
        if t == REMPLACEMENT_APP2:
            index["APP2:c"] = 0.0
        for cle, valeur in index.items():
            rows.append((ids[cle], t, round(valeur, 3)))
        for cle, delta in _increments(t, i).items():
            index[cle] += delta
        t += 3600
        i += 1
    db.upsert_statistic_samples(conn, rows)

    # Relevés du poller : quelques points récents, pour /api/releves,
    # /latest, /data, /range et l'état de la collecte.
    poller = []
    for cle in ("prod", "APP1:c"):
        for k in range(4):
            poller.append((ids[cle], NOW - 3600 * (3 - k), 2000.0 + k, None))
    db.insert_readings_batch(conn, poller)

    db.upsert_series_meta(conn, f"{AUTRE}:u1:total", AUTRE, "u1", "total",
                          "Compteur (total)", "", "Energie", "Meter", "kWh",
                          "APP9", "energie_consommee")
    db.insert_readings_batch(conn, [(f"{AUTRE}:u1:total", NOW - 7200, 5.0, None)])
    conn.commit()


def ids_par_cle() -> dict[str, str]:
    return {cle: f"{SITE}:{cle.replace(':', '-')}:{state}" for cle, _, _, _, _, state in SERIES}
