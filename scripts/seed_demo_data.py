#!/usr/bin/env python3
"""
Peuple une base SQLite de DÉMO avec des données synthétiques mais réalistes
(courbes journalières crédibles), pour valider que le dashboard sait bien
afficher des graphs — indépendamment de tout accès à un vrai Miniserver
Loxone. Utile pour isoler un souci d'affichage d'un souci de connexion.

Schéma aligné (2026-08-27) sur ce qui a été observé en conditions réelles sur
une installation Loxone mixte (compteurs "Meter" bidirectionnels + bloc
"Moniteur de flux d'énergie", voir CLAUDE.md, section "Dashboard énergie") :

- "actual" (kW) et "total"/"totalNeg" (kWh, index cumulatif jamais remis à
  zéro) ont un historique complet (comme un vrai backfill Statistics) ;
- "totalDay/Week/Month/Year" et "totalNegDay/Week/Month/Year" sont des
  compteurs vivants SANS historique propre -- une seule valeur (la plus
  récente) est écrite, exactement comme le ferait le vrai poller live ;
- Réseau et Batterie sont bidirectionnels (import/export, charge/décharge :
  states "total" ET "totalNeg") ; Solaire ne produit que (pas de "totalNeg") ;
- "storage" (état de charge batterie, %) est un state vivant sans historique
  propre lui aussi, comme les totalX.

La simulation couple les trois compteurs par zone (solaire -> autoconsommé
sur place -> surplus vers la batterie -> le reste exporté au réseau ; le
soir, la batterie se décharge pour réduire l'import réseau) pour que les
deltas jour/semaine/mois dérivés dans le dashboard (import, export,
autoconsommation Pd-Ed, charge/décharge) restent physiquement cohérents.

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

# grid et battery sont bidirectionnels (import/export, charge/décharge) :
# ils reçoivent des states totalNeg* en plus des totalX habituels, comme
# observé sur l'installation réelle (CLAUDE.md).
BIDIRECTIONAL_KINDS = {"grid", "battery"}

KIND_LABELS = {"grid": "Grid", "solar": "Solaire", "battery": "Batterie"}
BUILDING_LABELS = {"grid": "Réseau (général)", "solar": "Production (général)", "battery": "Batterie (générale)"}
RESOURCE_TYPES = {"grid": "energie_reseau", "solar": "energie_solaire", "battery": "energie_batterie"}

# apartment, demand_kw (base de la consommation avant solaire/batterie),
# solar_kw (crête PV), battery_kw (puissance charge/décharge max),
# battery_kwh (capacité), base_grid_kwh/base_solar_kwh (cumuls déjà
# accumulés avant le début de la simulation, pour ne pas repartir de zéro
# comme une installation flambant neuve).
ZONES = [
    {"apartment": "APP1", "demand_kw": 1.2, "solar_kw": 2.6, "battery_kw": 1.5, "battery_kwh": 6.0,
     "base_grid_kwh": 1800.0, "base_solar_kwh": 400.0},
    {"apartment": "APP2", "demand_kw": 1.5, "solar_kw": 3.0, "battery_kw": 1.5, "battery_kwh": 6.0,
     "base_grid_kwh": 2000.0, "base_solar_kwh": 460.0},
    {"apartment": "APP3", "demand_kw": 1.8, "solar_kw": 3.4, "battery_kw": 1.5, "battery_kwh": 6.0,
     "base_grid_kwh": 2200.0, "base_solar_kwh": 520.0},
    # Compteurs de bâtiment (non rattachés à un appartement précis) --
    # teste la zone "Bâtiment (non affecté)" du dashboard.
    {"apartment": "", "demand_kw": 3.5, "solar_kw": 4.5, "battery_kw": 3.0, "battery_kwh": 15.0,
     "base_grid_kwh": 5000.0, "base_solar_kwh": 900.0},
]


def solar_power_kw(hour_of_day: float, peak_kw: float) -> float:
    """Puissance solaire instantanée (kW) : nulle la nuit, en cloche le jour."""
    if 7 <= hour_of_day <= 19:
        return max(0.0, peak_kw * math.sin((hour_of_day - 7) / 12 * math.pi)) + random.uniform(-0.05, 0.08)
    return 0.0


def demand_power_kw(hour_of_day: float, base_kw: float) -> float:
    """Demande totale du foyer (kW), AVANT solaire/batterie -- creux la nuit,
    pics matin/soir. Le solaire et la batterie réduisent ensuite ce qui est
    effectivement tiré du réseau (voir la boucle de simulation dans main())."""
    load = base_kw * (0.35 + 0.25 * max(0, math.sin((hour_of_day - 6) / 18 * math.pi)))
    if 7 <= hour_of_day <= 9 or 18 <= hour_of_day <= 22:
        load *= 1.7
    return max(0.0, load * random.uniform(0.85, 1.15))


def water_delta_m3(hour_of_day: float, intensity: float) -> float:
    active = 1.0 if (7 <= hour_of_day <= 9 or 18 <= hour_of_day <= 21) else 0.15
    return intensity * active * random.uniform(0.4, 1.2)


def build_series_defs():
    defs = {"energy_meters": [], "water": []}

    for z in ZONES:
        apt = z["apartment"]
        for kind in ("grid", "solar", "battery"):
            label = f"App {apt[-1]} {KIND_LABELS[kind]}" if apt else BUILDING_LABELS[kind]
            defs["energy_meters"].append(dict(apartment=apt, kind=kind, label=label, resource_type=RESOURCE_TYPES[kind]))

    for i, apt in enumerate(APARTMENTS):
        defs["water"].append(dict(
            apartment=apt, label=f"Eau chaude App {apt[-1]}", room=f"Salle de bain {apt}",
            resource_type="eau_chaude", unit="m3", base_total=8.0 + i * 2.5, intensity=0.006,
        ))

    return defs


def series_id(apt: str, kind: str, state: str) -> str:
    apt_key = apt if apt else "batiment"
    return f"demo:{apt_key}-{kind}:{state}"


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.demo.yaml"
    cfg = load_config(config_path)
    conn = db.get_connection(cfg.db_path)

    defs = build_series_defs()
    all_energy_meters = defs["energy_meters"]

    # --- Métadonnées : actual/total(+Neg) historisés + les dérivées live-only ---
    for m in all_energy_meters:
        states = [("actual", "kW"), ("total", "kWh"), ("totalDay", "kWh"),
                  ("totalWeek", "kWh"), ("totalMonth", "kWh"), ("totalYear", "kWh")]
        if m["kind"] in BIDIRECTIONAL_KINDS:
            states += [("totalNeg", "kWh"), ("totalNegDay", "kWh"), ("totalNegWeek", "kWh"),
                       ("totalNegMonth", "kWh"), ("totalNegYear", "kWh")]
        if m["kind"] == "battery":
            states.append(("storage", "%"))
        for state, unit in states:
            db.upsert_series_meta(
                conn, series_id=series_id(m["apartment"], m["kind"], state),
                miniserver="demo", control_uuid=f"demo-{m['apartment'] or 'batiment'}-{m['kind']}",
                state_name=state, label=f"{m['label']} ({state})", room="Technique", category="Démo",
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

    # --- Simulation horaire, couplée par zone : solaire -> autoconsommé sur
    # place -> surplus vers la batterie -> le reste exporté au réseau ; le
    # soir/la nuit, la batterie se décharge pour réduire l'import réseau.
    now = int(time.time())
    total_hours = DAYS_OF_HISTORY * 24

    zone_state = {}
    for z in ZONES:
        zone_state[z["apartment"]] = dict(
            grid_total=z["base_grid_kwh"],
            grid_neg_total=round(z["base_solar_kwh"] * 0.25, 1),
            solar_total=z["base_solar_kwh"],
            battery_total=0.0,
            battery_neg_total=0.0,
            battery_soc_kwh=z["battery_kwh"] * 0.5,  # état de charge initial : moitié pleine
        )
    running_water = {w["apartment"]: w["base_total"] for w in defs["water"]}

    # Instantanés des cumuls pris à ~1 jour / ~1 semaine / ~1 mois avant
    # "maintenant", pour dériver des totalDay/Week/Month/Year réalistes en
    # toute fin de script -- plutôt qu'un pourcentage arbitraire du cumul
    # total, qui n'aurait aucun sens physique.
    snapshot_day = {}
    snapshot_week = {}
    snapshot_month = {}

    rows = []
    for h in range(total_hours, -1, -1):
        ts = now - h * 3600
        hour_of_day = (24 - (h % 24)) % 24

        for z in ZONES:
            apt = z["apartment"]
            st = zone_state[apt]

            solar_kw = solar_power_kw(hour_of_day, z["solar_kw"])
            demand_kw = demand_power_kw(hour_of_day, z["demand_kw"])

            self_consumed_kw = min(demand_kw, solar_kw)
            solar_surplus_kw = solar_kw - self_consumed_kw
            remaining_demand_kw = demand_kw - self_consumed_kw

            capacity = z["battery_kwh"]
            battery_flow_kw = 0.0
            if solar_surplus_kw > 0 and st["battery_soc_kwh"] < capacity * 0.97:
                charge_kw = min(solar_surplus_kw, z["battery_kw"], capacity - st["battery_soc_kwh"])
                battery_flow_kw = charge_kw
                solar_surplus_kw -= charge_kw
                st["battery_soc_kwh"] += charge_kw
            elif remaining_demand_kw > 0 and st["battery_soc_kwh"] > capacity * 0.1 and (hour_of_day >= 18 or hour_of_day <= 6):
                discharge_kw = min(remaining_demand_kw, z["battery_kw"], st["battery_soc_kwh"])
                battery_flow_kw = -discharge_kw
                remaining_demand_kw -= discharge_kw
                st["battery_soc_kwh"] -= discharge_kw

            grid_import_kw = remaining_demand_kw
            grid_export_kw = solar_surplus_kw

            st["solar_total"] += solar_kw
            st["grid_total"] += grid_import_kw
            st["grid_neg_total"] += grid_export_kw
            if battery_flow_kw >= 0:
                st["battery_total"] += battery_flow_kw
            else:
                st["battery_neg_total"] += -battery_flow_kw

            rows.append((series_id(apt, "solar", "actual"), ts, round(solar_kw, 3), None))
            rows.append((series_id(apt, "solar", "total"), ts, round(st["solar_total"], 3), None))
            rows.append((series_id(apt, "grid", "actual"), ts, round(grid_import_kw, 3), None))
            rows.append((series_id(apt, "grid", "total"), ts, round(st["grid_total"], 3), None))
            rows.append((series_id(apt, "grid", "totalNeg"), ts, round(st["grid_neg_total"], 3), None))
            rows.append((series_id(apt, "battery", "actual"), ts, round(battery_flow_kw, 3), None))
            rows.append((series_id(apt, "battery", "total"), ts, round(st["battery_total"], 3), None))
            rows.append((series_id(apt, "battery", "totalNeg"), ts, round(st["battery_neg_total"], 3), None))

            if h in (24, 24 * 7) or (h == 24 * 30 and total_hours >= 24 * 30):
                snap = dict(grid=st["grid_total"], grid_neg=st["grid_neg_total"], solar=st["solar_total"],
                            battery=st["battery_total"], battery_neg=st["battery_neg_total"])
                if h == 24:
                    snapshot_day[apt] = snap
                elif h == 24 * 7:
                    snapshot_week[apt] = snap
                else:
                    snapshot_month[apt] = snap

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

    # --- Valeurs "live" pour les compteurs dérivés (totalDay/Week/Month/Year,
    # totalNegDay/Week/Month/Year, storage) -- une seule valeur récente, PAS
    # d'historique, comme sur un vrai Miniserver (voir docstring). Calculées
    # comme un vrai delta (cumul actuel - cumul au début de la période), pas
    # un pourcentage arbitraire. "totalYear"/"totalNegYear" sont bornés à
    # toute la période simulée (60 jours), faute d'avoir un cumul vieux d'un
    # an dans cette démo -- label optimiste mais valeur physiquement cohérente.
    live_rows = []
    for z in ZONES:
        apt = z["apartment"]
        st = zone_state[apt]
        base_day = snapshot_day.get(apt, dict(
            grid=z["base_grid_kwh"], grid_neg=round(z["base_solar_kwh"] * 0.25, 1),
            solar=z["base_solar_kwh"], battery=0.0, battery_neg=0.0,
        ))
        base_week = snapshot_week.get(apt, base_day)
        base_month = snapshot_month.get(apt, base_week)

        def add(kind, prefix, now_val, base_day_val, base_week_val, base_month_val, base_year_val):
            live_rows.append((series_id(apt, kind, f"{prefix}Day"), now, round(max(0.0, now_val - base_day_val), 3), None))
            live_rows.append((series_id(apt, kind, f"{prefix}Week"), now, round(max(0.0, now_val - base_week_val), 3), None))
            live_rows.append((series_id(apt, kind, f"{prefix}Month"), now, round(max(0.0, now_val - base_month_val), 3), None))
            live_rows.append((series_id(apt, kind, f"{prefix}Year"), now, round(max(0.0, now_val - base_year_val), 3), None))

        add("grid", "total", st["grid_total"], base_day["grid"], base_week["grid"], base_month["grid"], z["base_grid_kwh"])
        add("grid", "totalNeg", st["grid_neg_total"], base_day["grid_neg"], base_week["grid_neg"],
            base_month["grid_neg"], round(z["base_solar_kwh"] * 0.25, 1))
        add("solar", "total", st["solar_total"], base_day["solar"], base_week["solar"], base_month["solar"], z["base_solar_kwh"])
        add("battery", "total", st["battery_total"], base_day["battery"], base_week["battery"], base_month["battery"], 0.0)
        add("battery", "totalNeg", st["battery_neg_total"], base_day["battery_neg"], base_week["battery_neg"],
            base_month["battery_neg"], 0.0)

        live_rows.append((series_id(apt, "battery", "storage"), now,
                           round(st["battery_soc_kwh"] / z["battery_kwh"] * 100, 1), None))

    db.insert_readings_batch(conn, live_rows)
    conn.commit()

    db.checkpoint_wal(conn)
    conn.close()

    n_energy_series = sum((11 if m["kind"] in BIDIRECTIONAL_KINDS else 6) + (1 if m["kind"] == "battery" else 0)
                          for m in all_energy_meters)
    n_series = n_energy_series + len(defs["water"])
    print(f"Base de démo peuplée : {n_series} capteurs "
          f"({len(all_energy_meters)} compteurs d'énergie (grid/solaire/batterie) + {len(defs['water'])} compteurs d'eau), "
          f"{DAYS_OF_HISTORY} jours d'historique horaire synthétique sur actual/total(+Neg) "
          f"({archived} points archivés en moyennes horaires).")
    print(f"-> {cfg.db_path}")


if __name__ == "__main__":
    main()
