#!/usr/bin/env python3
"""
Peuple une base SQLite de DÉMO avec des données synthétiques mais réalistes
(courbes journalières crédibles), pour valider que le dashboard sait bien
afficher des graphs — indépendamment de tout accès à un vrai Miniserver
Loxone. Utile pour isoler un souci d'affichage d'un souci de connexion.

Ne touche JAMAIS à ta base de production (utilise un fichier .db séparé,
voir config.demo.yaml).

Usage :
    python scripts/seed_demo_data.py [config.demo.yaml]

Puis :
    python app.py config.demo.yaml
    -> ouvre http://127.0.0.1:8082 dans ton navigateur
"""
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from config import load_config  # noqa: E402

random.seed(42)  # résultats reproductibles d'un lancement à l'autre

APARTMENTS = ["APP01", "APP02", "APP03"]
DAYS_OF_HISTORY = 14


def water_delta(hour_of_day: float, intensity: float) -> float:
    """Incrément d'index de compteur d'eau pour une heure donnée : plus actif
    le matin et le soir, quasi nul la nuit."""
    active = 1.0 if (7 <= hour_of_day <= 9 or 18 <= hour_of_day <= 21) else 0.15
    return intensity * active * random.uniform(0.4, 1.2)


def energy_delta(hour_of_day: float, base_load: float) -> float:
    """Incrément d'index de compteur électrique pour une heure donnée : creux
    la nuit, pics matin/soir."""
    load = base_load * (0.4 + 0.3 * max(0, math.sin((hour_of_day - 6) / 18 * math.pi)))
    if 7 <= hour_of_day <= 9 or 18 <= hour_of_day <= 22:
        load *= 1.8
    return load * random.uniform(0.85, 1.15)


def solar_power(hour_of_day: float) -> float:
    """Puissance instantanée (kW) : nulle la nuit, en cloche le jour."""
    if 7 <= hour_of_day <= 19:
        return max(0.0, 3.2 * math.sin((hour_of_day - 7) / 12 * math.pi)) + random.uniform(-0.1, 0.15)
    return 0.0


def build_series_defs():
    """Retourne la liste des capteurs de démo : par appartement (eau chaude,
    eau froide, électricité) + des compteurs partagés au niveau immeuble
    (solaire, import/injection réseau) — de quoi couvrir tous les types de
    ressource gérés par classification.py."""
    defs = []
    for i, apt in enumerate(APARTMENTS):
        defs.append(dict(
            series_id=f"demo:{apt}-ecs:actual", apartment=apt, kind="water_hot",
            label=f"{apt} Eau chaude", room=f"Salle de bain {apt}",
            resource_type="eau_chaude", control_type="Meter", unit="m3",
            base=8.0 + i * 2.5, intensity=0.006,
        ))
        defs.append(dict(
            series_id=f"demo:{apt}-eauf:actual", apartment=apt, kind="water_cold",
            label=f"{apt} Eau froide", room=f"Cuisine {apt}",
            resource_type="eau_froide", control_type="Meter", unit="m3",
            base=30.0 + i * 6, intensity=0.018,
        ))
        defs.append(dict(
            series_id=f"demo:{apt}-energie:actual", apartment=apt, kind="energy",
            label=f"{apt} Compteur électrique", room=f"{apt} Technique",
            resource_type="energie_consommee", control_type="Meter", unit="kWh",
            base=1200.0 + i * 300, load=0.35 + i * 0.1,
        ))

    defs.append(dict(
        series_id="demo:solaire:actual", apartment="", kind="solar",
        label="Production solaire", room="Toiture",
        resource_type="energie_solaire", control_type="InfoOnlyAnalog", unit="kW",
        base=0.0,
    ))
    defs.append(dict(
        series_id="demo:reseau-import:actual", apartment="", kind="energy",
        label="Import réseau (général)", room="Technique",
        resource_type="energie_reseau", control_type="Meter", unit="kWh",
        base=5000.0, load=0.6,
    ))
    defs.append(dict(
        series_id="demo:reseau-injection:actual", apartment="", kind="energy",
        label="Injection réseau (général)", room="Technique",
        resource_type="energie_injectee", control_type="Meter", unit="kWh",
        base=800.0, load=0.15,
    ))
    return defs


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.demo.yaml"
    cfg = load_config(config_path)
    conn = db.get_connection(cfg.db_path)

    series_defs = build_series_defs()
    for s in series_defs:
        db.upsert_series_meta(
            conn, series_id=s["series_id"], miniserver="demo",
            control_uuid=s["series_id"], state_name="actual", label=s["label"],
            room=s["room"], category="Démo", control_type=s["control_type"],
            unit=s["unit"], apartment=s["apartment"], resource_type=s["resource_type"],
        )
    conn.commit()

    now = int(time.time())
    total_hours = DAYS_OF_HISTORY * 24
    running = {s["series_id"]: s["base"] for s in series_defs}

    rows = []
    for h in range(total_hours, -1, -1):
        ts = now - h * 3600
        hour_of_day = (24 - (h % 24)) % 24
        for s in series_defs:
            sid = s["series_id"]
            if s["kind"] == "water_hot":
                running[sid] += water_delta(hour_of_day, s["intensity"])
                value = round(running[sid], 3)
            elif s["kind"] == "water_cold":
                running[sid] += water_delta(hour_of_day, s["intensity"])
                value = round(running[sid], 3)
            elif s["kind"] == "energy":
                running[sid] += energy_delta(hour_of_day, s["load"])
                value = round(running[sid], 2)
            elif s["kind"] == "solar":
                value = round(solar_power(hour_of_day), 2)
            else:
                value = 0.0
            rows.append((sid, ts, value, None))

        if len(rows) > 2000:
            db.insert_readings_batch(conn, rows)
            conn.commit()
            rows = []

    if rows:
        db.insert_readings_batch(conn, rows)
        conn.commit()

    # Archive l'historique au-delà de raw_retention_days en moyennes horaires,
    # exactement comme le ferait la maintenance quotidienne en prod — ça
    # valide aussi ce chemin de code avec des données de démo.
    archived = db.downsample_and_prune(conn, cfg.raw_retention_days)
    db.checkpoint_wal(conn)
    conn.close()

    print(f"Base de démo peuplée : {len(series_defs)} capteurs, "
          f"{DAYS_OF_HISTORY} jours d'historique horaire synthétique "
          f"({archived} points archivés en moyennes horaires).")
    print(f"-> {cfg.db_path}")


if __name__ == "__main__":
    main()
