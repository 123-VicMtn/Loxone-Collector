#!/usr/bin/env python3
"""
Peuple une base SQLite de DÉMO avec des données synthétiques mais réalistes
(courbes journalières crédibles), pour valider que le dashboard sait bien
afficher des graphs — indépendamment de tout accès à un vrai Miniserver
Loxone. Utile pour isoler un souci d'affichage d'un souci de connexion.

Schéma aligné (2026-08-26) sur ce qui a été observé en conditions réelles
sur une installation Loxone avec compteurs "Energie Flow Monitor" (voir
CLAUDE.md) : pour un compteur d'énergie, deux states ont un historique
("actual" = puissance instantanée en kW, "total" = index cumulatif en kWh,
jamais remis à zéro) tandis que "totalDay"/"totalWeek"/"totalMonth"/
"totalYear" sont des compteurs vivants SANS historique propre (juste leur
dernière valeur a un sens) -- ce script reproduit fidèlement cette
distinction : "actual"/"total" reçoivent un historique horaire complet,
les totalX sont dérivés de "total" mais UNE SEULE valeur (la plus récente)
est écrite, exactement comme le ferait le vrai Miniserver via le poller
live (jamais via un backfill Statistics).

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

APARTMENTS = ["APP1", "APP2", "APP3"]
DAYS_OF_HISTORY = 60


def solar_power_kw(hour_of_day: float, peak_kw: float) -> float:
    """Puissance solaire instantanée (kW) : nulle la nuit, en cloche le jour."""
    if 7 <= hour_of_day <= 19:
        return max(0.0, peak_kw * math.sin((hour_of_day - 7) / 12 * math.pi)) + random.uniform(-0.05, 0.08)
    return 0.0


def grid_power_kw(hour_of_day: float, base_kw: float) -> float:
    """Puissance tirée du réseau (kW) : creux la nuit, pics matin/soir,
    réduite en journée quand le solaire couvre une partie du besoin."""
    load = base_kw * (0.35 + 0.25 * max(0, math.sin((hour_of_day - 6) / 18 * math.pi)))
    if 7 <= hour_of_day <= 9 or 18 <= hour_of_day <= 22:
        load *= 1.7
    if 11 <= hour_of_day <= 15:
        load *= 0.4  # le solaire couvre une partie du besoin en milieu de journée
    return max(0.0, load * random.uniform(0.85, 1.15))


def water_delta_m3(hour_of_day: float, intensity: float) -> float:
    active = 1.0 if (7 <= hour_of_day <= 9 or 18 <= hour_of_day <= 21) else 0.15
    return intensity * active * random.uniform(0.4, 1.2)


def build_energy_meter(apartment: str, kind: str, peak_kw: float, base_total_kwh: float) -> dict:
    """kind: 'grid' ou 'solar'. Retourne la config d'un compteur d'énergie
    avec ses deux séries historisées (actual, total) + les 4 séries
    dérivées sans historique (totalDay/Week/Month/Year)."""
    label = f"App {apartment[-1]} {'Grid' if kind == 'grid' else 'Solaire'}" if apartment else ""
    resource_type = "energie_reseau" if kind == "grid" else "energie_solaire"
    return dict(
        apartment=apartment, kind=kind, label=label,
        resource_type=resource_type, base_total=base_total_kwh, peak_kw=peak_kw,
    )


def build_series_defs():
    defs = {"energy_meters": [], "water": [], "building": []}

    for i, apt in enumerate(APARTMENTS):
        defs["energy_meters"].append(build_energy_meter(apt, "grid", peak_kw=1.2 + i * 0.3, base_total_kwh=1800 + i * 200))
        defs["energy_meters"].append(build_energy_meter(apt, "solar", peak_kw=2.5 + i * 0.4, base_total_kwh=400 + i * 60))
        defs["water"].append(dict(
            apartment=apt, label=f"Eau chaude App {apt[-1]}", room=f"Salle de bain {apt}",
            resource_type="eau_chaude", unit="m3", base_total=8.0 + i * 2.5, intensity=0.006,
        ))

    # Compteurs de bâtiment (non rattachés à un appartement précis) --
    # teste la zone "Bâtiment (non affecté)" du dashboard.
    defs["building"].append(build_energy_meter("", "grid", peak_kw=0.8, base_total_kwh=5000))
    defs["building"].append(build_energy_meter("", "solar", peak_kw=1.0, base_total_kwh=900))

    return defs


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.demo.yaml"
    cfg = load_config(config_path)
    conn = db.get_connection(cfg.db_path)

    defs = build_series_defs()
    all_energy_meters = defs["energy_meters"] + defs["building"]

    def series_id(prefix, apt, kind, state):
        apt_key = apt if apt else "batiment"
        return f"demo:{apt_key}-{kind}:{state}"

    # --- Métadonnées : actual/total (historisées) + les 4 dérivées (live seulement) ---
    for m in all_energy_meters:
        label = m["label"] if m["apartment"] else f"{'Réseau' if m['kind'] == 'grid' else 'Production'} (général)"
        for state, unit in (("actual", "kW"), ("total", "kWh"), ("totalDay", "kWh"),
                             ("totalWeek", "kWh"), ("totalMonth", "kWh"), ("totalYear", "kWh")):
            db.upsert_series_meta(
                conn, series_id=series_id("demo", m["apartment"], m["kind"], state),
                miniserver="demo", control_uuid=f"demo-{m['apartment'] or 'batiment'}-{m['kind']}",
                state_name=state, label=f"{label} ({state})", room="Technique", category="Démo",
                control_type="Meter", unit=unit, apartment=m["apartment"], resource_type=m["resource_type"],
            )

    for w in defs["water"]:
        db.upsert_series_meta(
            conn, series_id=f"demo:{w['apartment']}-eauchaude:total", miniserver="demo",
            control_uuid=f"demo-{w['apartment']}-eauchaude", state_name="total", label=f"{w['label']} (total)",
            room=w["room"], category="Démo", control_type="Meter", unit=w["unit"],
            apartment=w["apartment"], resource_type=w["resource_type"],
        )
    conn.commit()

    # --- Simulation horaire ---
    now = int(time.time())
    total_hours = DAYS_OF_HISTORY * 24
    running_total = {(m["apartment"], m["kind"]): m["base_total"] for m in all_energy_meters}
    running_water = {w["apartment"]: w["base_total"] for w in defs["water"]}

    # Instantanés du cumul (kWh) pris à ~1 jour / ~1 semaine / ~1 mois avant
    # "maintenant", pour dériver des totalDay/Week/Month/Year réalistes en
    # toute fin de script (voir plus bas) -- plutôt qu'un pourcentage
    # arbitraire du cumul total, qui n'aurait aucun sens physique.
    snapshot_day = {}
    snapshot_week = {}
    snapshot_month = {}

    # Historique complet pour actual/total (comme un vrai backfill Statistics).
    rows = []
    for h in range(total_hours, -1, -1):
        ts = now - h * 3600
        hour_of_day = (24 - (h % 24)) % 24

        for m in all_energy_meters:
            key = (m["apartment"], m["kind"])
            if m["kind"] == "solar":
                power = solar_power_kw(hour_of_day, m["peak_kw"])
            else:
                power = grid_power_kw(hour_of_day, m["peak_kw"])
            running_total[key] += power  # kW pendant 1h -> kWh
            rows.append((series_id("demo", m["apartment"], m["kind"], "actual"), ts, round(power, 3), None))
            rows.append((series_id("demo", m["apartment"], m["kind"], "total"), ts, round(running_total[key], 3), None))
            if h == 24:
                snapshot_day[key] = running_total[key]
            if h == 24 * 7:
                snapshot_week[key] = running_total[key]
            if h == 24 * 30 and total_hours >= 24 * 30:
                snapshot_month[key] = running_total[key]

        for w in defs["water"]:
            running_water[w["apartment"]] += water_delta_m3(hour_of_day, w["intensity"])
            rows.append((f"demo:{w['apartment']}-eauchaude:total", ts, round(running_water[w["apartment"]], 3), None))

        if len(rows) > 4000:
            db.insert_readings_batch(conn, rows)
            conn.commit()
            rows = []

    if rows:
        db.insert_readings_batch(conn, rows)
        conn.commit()

    # Archive l'historique au-delà de raw_retention_days en moyennes horaires,
    # exactement comme le ferait la maintenance quotidienne en prod (et comme
    # scripts/backfill_statistics.py le fait directement pour un import
    # rétroactif) -- ça valide aussi ce chemin de code avec des données de
    # démo.
    archived = db.downsample_and_prune(conn, cfg.raw_retention_days)

    # --- Valeurs "live" pour les compteurs dérivés (totalDay/Week/Month/Year)
    # -- une seule valeur récente, PAS d'historique, comme sur un vrai
    # Miniserver (voir docstring). Calculées comme un vrai delta (cumul
    # actuel - cumul au début de la période), pas un pourcentage arbitraire.
    # "totalYear" est borné à toute la période simulée (60 jours), faute
    # d'avoir un cumul vieux d'un an dans cette démo -- label optimiste mais
    # valeur physiquement cohérente avec le reste.
    live_rows = []
    for m in all_energy_meters:
        key = (m["apartment"], m["kind"])
        total_now = running_total[key]
        base_total = m["base_total"]
        day_val = total_now - snapshot_day.get(key, base_total)
        week_val = total_now - snapshot_week.get(key, base_total)
        month_val = total_now - snapshot_month.get(key, base_total)
        year_val = total_now - base_total
        live_rows.append((series_id("demo", m["apartment"], m["kind"], "totalDay"), now, round(max(0.0, day_val), 3), None))
        live_rows.append((series_id("demo", m["apartment"], m["kind"], "totalWeek"), now, round(max(0.0, week_val), 3), None))
        live_rows.append((series_id("demo", m["apartment"], m["kind"], "totalMonth"), now, round(max(0.0, month_val), 3), None))
        live_rows.append((series_id("demo", m["apartment"], m["kind"], "totalYear"), now, round(max(0.0, year_val), 3), None))
    db.insert_readings_batch(conn, live_rows)
    conn.commit()

    db.checkpoint_wal(conn)
    conn.close()

    n_series = len(all_energy_meters) * 6 + len(defs["water"])
    print(f"Base de démo peuplée : {n_series} capteurs "
          f"({len(all_energy_meters)} compteurs d'énergie x 6 states + {len(defs['water'])} compteurs d'eau), "
          f"{DAYS_OF_HISTORY} jours d'historique horaire synthétique sur actual/total "
          f"({archived} points archivés en moyennes horaires).")
    print(f"-> {cfg.db_path}")


if __name__ == "__main__":
    main()
