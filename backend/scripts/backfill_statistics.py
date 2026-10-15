#!/usr/bin/env python3
"""
Backfill de l'historique "Statistics" natif Loxone dans la base locale --
pour disposer de données antérieures au démarrage du collecteur (nécessaire
pour un décompte de charges portant sur une période passée).

Contexte / validation préalable (2026-08-26, voir CLAUDE.md et le doc
d'architecture pour l'historique complet) :
  - `scripts/check_statistics.py` a confirmé que `jdev/sps/getStatisticInfo`
    répond HTTP 200 à distance (à travers le relais Loxone "Remote Connect"),
    contrairement à la lecture live par point (`/jdev/sps/io/<uuid>`, 404).
  - `scripts/fetch_statistics_sample.py` a confirmé et validé en conditions
    réelles le format de réponse de `jdev/sps/getStatistic/.../raw/...` :
    binaire (Content-Type application/octet-stream), suite d'enregistrements
    Uint32 timestamp (Unix UTC, PAS l'epoch Loxone 2009) + Float64 valeur,
    little-endian.

Principe :
  1. Pour chaque point mesurable suivi par le poller (mêmes filtres que
     app.py : include_types/exclude_types/exclude_rooms), on cherche dans
     `details.statisticV2` (ou `.statistic`) du contrôle parent un
     dataPoint dont le champ "output" correspond au state_name du point
     (ex: "actual", "total") -- Loxone utilise le même nom des deux côtés.
  2. Si trouvé : `getStatisticInfo` donne `activeSince` pour ce groupe (date
     de début d'historique disponible).
  3. `getStatistic/.../raw/<from>/<to>/all/<group>/<output>` récupère
     l'historique à la résolution native d'enregistrement (pas de perte par
     agrégation côté Miniserver).
  4. Chaque enregistrement récupéré est regroupé par heure (moyenne si
     plusieurs points tombent dans la même heure) et inséré dans
     `readings_hourly` -- SANS jamais écraser une ligne déjà existante (le
     poller live, s'il a déjà une valeur pour cette heure, est considéré
     plus fiable et prioritaire -- voir db.upsert_hourly_batch).

Usage :
    python scripts/backfill_statistics.py <config.yaml> <nom_miniserver> \\
        [--since YYYY-MM-DD] [--dry-run]

--since     : ne remonte pas avant cette date (par défaut : tout l'historique
              disponible, tel que rapporté par activeSince pour chaque point).
--dry-run   : fait tout le travail (structure, mapping, requêtes HTTP,
              décodage, regroupement horaire) mais n'écrit rien en base --
              affiche seulement les compteurs, pour valider avant un run réel.

⚠️ Peut prendre plusieurs minutes sur une grosse installation : une requête
HTTP par (contrôle, groupe statistique) actif, plus une par point mappé.
Lance-le depuis ton Terminal natif (pas via un pont d'exécution distant) --
c'est le seul chemin ayant un accès réseau non restreint vers un miniserver
accédé par DynDNS Loxone.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from config import load_config  # noqa: E402
from loxone_client import (  # noqa: E402
    LoxoneAuthError,
    LoxoneError,
    LoxoneClient,
    extract_measurable_points,
)
from miniserver_statistics import (  # noqa: E402
    bucket_hourly,
    fetch_raw_history,
    find_stat_mapping,
    get_statistic_info,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config_path")
    parser.add_argument("miniserver_name")
    parser.add_argument("--since", default=None, help="YYYY-MM-DD, ne remonte pas avant cette date")
    parser.add_argument("--dry-run", action="store_true", help="n'écrit rien en base")
    args = parser.parse_args()

    since_ts = None
    if args.since:
        try:
            since_ts = int(datetime.strptime(args.since, "%Y-%m-%d")
                            .replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            print(f"--since invalide : '{args.since}' (attendu YYYY-MM-DD)")
            sys.exit(1)

    cfg = load_config(args.config_path)
    ms = next((m for m in cfg.miniservers if m.name == args.miniserver_name), None)
    if ms is None:
        print(f"Miniserver '{args.miniserver_name}' introuvable dans {args.config_path}.")
        sys.exit(1)

    client = LoxoneClient(
        name=ms.name, host=ms.host, username=ms.username, password=ms.password,
        port=ms.port, scheme=ms.scheme, verify_ssl=ms.verify_ssl,
    )

    print(f"Miniserver '{ms.name}' -> {ms.scheme}://{ms.host}:{ms.port}")
    print(f"Mode : {'DRY-RUN (rien ne sera écrit en base)' if args.dry_run else 'ÉCRITURE RÉELLE'}\n")

    print("[1] Récupération de la structure...")
    try:
        structure = client.fetch_structure()
    except LoxoneError as exc:
        print(f"    ÉCHEC : {exc}")
        client.close()
        sys.exit(1)

    controls = structure.get("controls", {}) or {}
    points = extract_measurable_points(
        structure, include_types=cfg.include_types,
        exclude_types=cfg.exclude_types, exclude_rooms=cfg.exclude_rooms,
    )
    print(f"    {len(points)} point(s) mesurable(s) suivi(s) par le poller (sur {len(controls)} contrôles).\n")

    now_ts = int(time.time())
    stat_info_cache: dict[str, dict[str, int]] = {}  # control_uuid -> {group_id: activeSince}

    n_no_mapping = 0
    n_no_active_since = 0
    n_fetch_failed = 0
    n_no_data = 0
    n_ok = 0
    total_records = 0
    total_hourly_rows = 0

    conn = None if args.dry_run else db.get_connection(cfg.db_path)

    print("[2] Parcours des points, backfill de ceux ayant des Statistics actives...\n")
    for p in points:
        control = controls.get(p.control_uuid, {})
        mapping = find_stat_mapping(control, p.state_name)
        if mapping is None:
            n_no_mapping += 1
            continue
        group_id, output_name = mapping

        if p.control_uuid not in stat_info_cache:
            try:
                stat_info_cache[p.control_uuid] = get_statistic_info(client, p.control_uuid)
            except LoxoneAuthError as exc:
                print(f"    ÉCHEC AUTH -- arrêt : {exc}")
                client.close()
                sys.exit(1)
            except LoxoneError as exc:
                print(f"    [{p.control_name}] getStatisticInfo a échoué : {exc}")
                stat_info_cache[p.control_uuid] = {}

        active_since = stat_info_cache[p.control_uuid].get(group_id)
        if active_since is None:
            n_no_active_since += 1
            continue

        from_ts = max(active_since, since_ts) if since_ts else active_since
        if from_ts >= now_ts:
            n_no_data += 1
            continue

        series_id = f"{ms.name}:{p.series_id}"
        try:
            records = fetch_raw_history(client, p.control_uuid, group_id, output_name, from_ts, now_ts)
        except LoxoneError as exc:
            print(f"    [{p.label}] échec de récupération : {exc}")
            n_fetch_failed += 1
            continue

        if not records:
            n_no_data += 1
            continue

        total_records += len(records)
        hourly = bucket_hourly(records)
        total_hourly_rows += len(hourly)
        n_ok += 1

        first_date = datetime.utcfromtimestamp(records[0][0]).date()
        last_date = datetime.utcfromtimestamp(records[-1][0]).date()
        print(f"    [{p.label}] {len(records)} enregistrement(s) brut(s) -> {len(hourly)} heure(s) "
              f"({first_date} -> {last_date})")

        if not args.dry_run:
            rows = [(series_id, ts, avg, mn, mx, cnt) for ts, avg, mn, mx, cnt in hourly]
            db.upsert_hourly_batch(conn, rows)
            if p.state_name in ("total", "totalNeg"):
                db.upsert_statistic_samples(
                    conn, [(series_id, ts, value) for ts, value in records],
                )
                db.mark_statistic_sync(conn, series_id, now_ts, True)
            conn.commit()

    client.close()
    if conn is not None:
        db.checkpoint_wal(conn)
        conn.close()

    print("\n" + "=" * 70)
    print("Résumé :")
    print(f"  {n_ok} point(s) backfillé(s) avec succès "
          f"({total_records} enregistrement(s) bruts -> {total_hourly_rows} ligne(s) horaires "
          f"{'tentées' if not args.dry_run else 'qui SERAIENT tentées'}).")
    print(f"  {n_no_mapping} point(s) sans Statistics configuré (pas de dataPoint statisticV2 "
          f"correspondant -- normal pour la plupart des states non-mesures, ex: jLocked).")
    if n_no_active_since:
        print(f"  {n_no_active_since} point(s) avec un mapping trouvé mais sans activeSince "
              f"renvoyé par getStatisticInfo (à investiguer si ce nombre est élevé).")
    if n_fetch_failed:
        print(f"  {n_fetch_failed} point(s) en échec de récupération (voir détails ci-dessus).")
    if n_no_data:
        print(f"  {n_no_data} point(s) sans donnée dans la période demandée.")
    if args.dry_run:
        print("\n  DRY-RUN : rien n'a été écrit en base. Relance sans --dry-run pour importer "
              "réellement ces données.")
    else:
        print("\n  Recharge le dashboard -- les graphs devraient maintenant remonter jusqu'à "
              "l'historique disponible plutôt que de démarrer au lancement du collecteur.")


if __name__ == "__main__":
    main()
