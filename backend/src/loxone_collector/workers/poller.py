"""
Tâches de fond : le poller (valeurs live de chaque Miniserver, écrites en
base) et la synchro périodique des Statistics du décompte. `state` garde
l'état du dernier cycle de chaque site, lu par /health.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from loxone_collector import db
from loxone_collector.config import AppConfig
from loxone_collector.loxone.client import LoxoneAuthError, LoxoneClient, LoxoneError, extract_measurable_points
from loxone_collector.loxone.websocket import LoxoneWsError, fetch_live_values
from loxone_collector.services import classification, statistics_sync

logger = logging.getLogger("app")


state = {
    "last_poll_ts": {},     # miniserver_name -> epoch
    "last_poll_ok": {},     # miniserver_name -> bool
    "last_error": {},       # miniserver_name -> str|None
    "series_count": {},     # miniserver_name -> int
    "started_at": time.time(),
}


# Délai avant la première synchro Statistics après le démarrage.
STATISTICS_SYNC_DELAI_S = 120


def poll_once(cfg: AppConfig, conn) -> None:
    for ms_cfg in cfg.miniservers:
        client = LoxoneClient(
            name=ms_cfg.name,
            host=ms_cfg.host,
            username=ms_cfg.username,
            password=ms_cfg.password,
            port=ms_cfg.port,
            scheme=ms_cfg.scheme,
            verify_ssl=ms_cfg.verify_ssl,
            read_delay_seconds=ms_cfg.read_delay_seconds,
        )
        try:
            structure = client.fetch_structure()
            points = extract_measurable_points(
                structure,
                include_types=cfg.include_types,
                exclude_types=cfg.exclude_types,
                exclude_rooms=cfg.exclude_rooms,
            )

            for p in points:
                apartment = classification.extract_apartment(p.label, cfg.apartment_pattern)
                resource_type = classification.guess_resource_type(
                    p.label, p.control_type, cfg.resource_type_rules
                )
                if p.flow_role == "solaire":
                    resource_type = "energie_solaire"
                elif p.flow_role == "conso":
                    resource_type = "energie_consommee"
                elif p.flow_role == "reseau":
                    resource_type = "energie_reseau"
                db.upsert_series_meta(
                    conn,
                    series_id=f"{ms_cfg.name}:{p.series_id}",
                    miniserver=ms_cfg.name,
                    control_uuid=p.control_uuid,
                    state_name=p.state_name,
                    label=p.label,
                    room=p.room,
                    category=p.category,
                    control_type=p.control_type,
                    unit=p.unit,
                    apartment=apartment,
                    resource_type=resource_type,
                )
            conn.commit()

            uuids = [p.uuid for p in points]
            if ms_cfg.protocol == "websocket":
                # Lecture live via le protocole Websocket chiffré (voir
                # loxone_ws_client.py) — seul chemin supporté par Loxone
                # pour l'accès distant ("Remote Connect"). La structure a
                # quand même été récupérée en HTTP simple ci-dessus (ça,
                # ça fonctionne à distance).
                token_dir = Path(cfg.db_path).parent / "ws_tokens" / ms_cfg.name
                values = fetch_live_values(
                    host=client.host,
                    port=client.port,
                    username=ms_cfg.username,
                    password=ms_cfg.password,
                    use_tls=(ms_cfg.scheme == "https"),
                    token_dir=str(token_dir),
                    wanted_uuids=uuids,
                    collect_seconds=ms_cfg.websocket_max_seconds,
                )
            else:
                values = client.read_values(uuids)

            now = int(time.time())
            rows = []
            for p in points:
                v = values.get(p.uuid)
                series_id = f"{ms_cfg.name}:{p.series_id}"
                if isinstance(v, (int, float)):
                    rows.append((series_id, now, float(v), None))
                elif v is not None:
                    rows.append((series_id, now, None, str(v)))
            db.insert_readings_batch(conn, rows)
            conn.commit()

            state["last_poll_ts"][ms_cfg.name] = now
            state["last_poll_ok"][ms_cfg.name] = True
            state["last_error"][ms_cfg.name] = None
            state["series_count"][ms_cfg.name] = len(points)
            logger.info(
                "[%s] poll OK: %d points, %d valeurs numériques écrites",
                ms_cfg.name, len(points), len(rows),
            )
        except LoxoneAuthError as exc:
            logger.error(str(exc))
            state["last_poll_ok"][ms_cfg.name] = False
            state["last_error"][ms_cfg.name] = str(exc)
        except (LoxoneError, LoxoneWsError) as exc:
            logger.warning(str(exc))
            state["last_poll_ok"][ms_cfg.name] = False
            state["last_error"][ms_cfg.name] = str(exc)
        finally:
            client.close()


def poller_loop(cfg: AppConfig) -> None:
    conn = db.get_connection(cfg.db_path)
    last_maintenance_day = None

    while True:
        cycle_start = time.time()
        try:
            poll_once(cfg, conn)
        except Exception:  # pragma: no cover - filet de sécurité du thread de fond
            logger.exception("Erreur inattendue pendant le cycle de poll")

        # Maintenance quotidienne : downsampling + checkpoint WAL, une fois
        # par jour à l'heure configurée (UTC), pour ménager la carte SD.
        now_utc = datetime.now(timezone.utc)
        if now_utc.hour == cfg.maintenance_hour_utc and now_utc.date() != last_maintenance_day:
            try:
                deleted = db.downsample_and_prune(conn, cfg.raw_retention_days)
                db.prune_hourly(conn, cfg.hourly_retention_days)
                db.checkpoint_wal(conn)
                logger.info("Maintenance DB effectuée (%d lignes brutes archivées).", deleted)
            except Exception:
                logger.exception("Erreur pendant la maintenance DB")
            last_maintenance_day = now_utc.date()

        elapsed = time.time() - cycle_start
        sleep_for = max(1.0, cfg.poll_interval_seconds - elapsed)
        time.sleep(sleep_for)


def sync_statistics_once(cfg: AppConfig, conn) -> None:
    """Complète jusqu'à maintenant les index Statistics de chaque site.
    Sans elle, ils n'avançaient qu'à l'ouverture de /decompte ou à un export,
    et seulement jusqu'à la fin du mois demandé (Sequoia : électricité
    arrêtée au 02.10.2026, alors que le collecteur tournait)."""
    for ms_cfg in cfg.miniservers:
        series = [s for s in db.list_series(conn) if s["miniserver"] == ms_cfg.name]
        if not series:
            continue
        try:
            res = statistics_sync.sync_decompte(
                cfg, conn, ms_cfg.name, series, int(time.time()), postes=True,
            )
        except Exception:  # pragma: no cover - filet de sécurité du thread de fond
            logger.exception("Erreur pendant la synchro Statistics de %s", ms_cfg.name)
            continue
        if res["ok"]:
            logger.info("Synchro Statistics %s : %d série(s), %d point(s).",
                        ms_cfg.name, res["series"], res["points"])


def statistics_sync_loop(cfg: AppConfig) -> None:
    # Laisse le premier cycle du poller passer avant de solliciter les
    # Miniservers une seconde fois.
    time.sleep(STATISTICS_SYNC_DELAI_S)
    conn = db.get_connection(cfg.db_path)
    while True:
        sync_statistics_once(cfg, conn)
        time.sleep(cfg.statistics_sync_hours * 3600)


def start_background_poller(cfg: AppConfig) -> None:
    t = threading.Thread(target=poller_loop, args=(cfg,), daemon=True, name="loxone-poller")
    t.start()
    if cfg.statistics_sync_hours > 0:
        threading.Thread(
            target=statistics_sync_loop, args=(cfg,), daemon=True, name="statistics-sync",
        ).start()

