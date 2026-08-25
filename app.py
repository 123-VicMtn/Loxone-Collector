"""
app.py
------
Point d'entrée de l'application : lance en tâche de fond le poller qui
interroge les Miniservers Loxone et écrit en SQLite, et sert un dashboard
web (Flask) permettant de choisir un capteur et d'en visualiser l'historique
sous forme de graph (Chart.js).

Lancement (dev) :
    python app.py

Lancement (prod, sur le Pi) : voir scripts/loxone-collector.service
(gunicorn n'est volontairement pas utilisé ici : le serveur de dev Flask,
mono-process, suffit largement pour un dashboard local sur quelques
utilisateurs, et évite de multiplier les connexions SQLite/la RAM utilisée
sur un Pi à 2 Go).
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import closing
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request, abort

import db
from config import AppConfig, load_config
from loxone_client import LoxoneAuthError, LoxoneClient, LoxoneError, extract_measurable_points

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app")

app = Flask(__name__)

_state = {
    "last_poll_ts": {},     # miniserver_name -> epoch
    "last_poll_ok": {},     # miniserver_name -> bool
    "last_error": {},       # miniserver_name -> str|None
    "series_count": {},     # miniserver_name -> int
    "started_at": time.time(),
}

RANGE_PRESETS = {
    "1h": 3600,
    "24h": 86400,
    "7d": 7 * 86400,
    "30d": 30 * 86400,
    "1y": 365 * 86400,
}


# --------------------------------------------------------------------------
# Poller (tâche de fond)
# --------------------------------------------------------------------------

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
                )
            conn.commit()

            uuids = [p.uuid for p in points]
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

            _state["last_poll_ts"][ms_cfg.name] = now
            _state["last_poll_ok"][ms_cfg.name] = True
            _state["last_error"][ms_cfg.name] = None
            _state["series_count"][ms_cfg.name] = len(points)
            logger.info(
                "[%s] poll OK: %d points, %d valeurs numériques écrites",
                ms_cfg.name, len(points), len(rows),
            )
        except LoxoneAuthError as exc:
            logger.error(str(exc))
            _state["last_poll_ok"][ms_cfg.name] = False
            _state["last_error"][ms_cfg.name] = str(exc)
        except LoxoneError as exc:
            logger.warning(str(exc))
            _state["last_poll_ok"][ms_cfg.name] = False
            _state["last_error"][ms_cfg.name] = str(exc)
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


def start_background_poller(cfg: AppConfig) -> None:
    t = threading.Thread(target=poller_loop, args=(cfg,), daemon=True, name="loxone-poller")
    t.start()


# --------------------------------------------------------------------------
# Routes web
# --------------------------------------------------------------------------

def _cfg() -> AppConfig:
    return app.config["LOXONE_CFG"]


def _read_conn():
    return db.get_connection(_cfg().db_path)


@app.route("/")
def index():
    with closing(_read_conn()) as conn:
        series = db.list_series(conn)

    grouped: dict[str, list[dict]] = {}
    for s in series:
        grouped.setdefault(s["room"] or "Sans pièce", []).append(s)

    return render_template(
        "index.html",
        grouped_series=grouped,
        range_presets=list(RANGE_PRESETS.keys()),
    )


@app.route("/health")
def health():
    return jsonify(
        {
            "started_at": _state["started_at"],
            "last_poll_ts": _state["last_poll_ts"],
            "last_poll_ok": _state["last_poll_ok"],
            "last_error": _state["last_error"],
            "series_count": _state["series_count"],
        }
    )


@app.route("/api/series")
def api_series():
    with closing(_read_conn()) as conn:
        series = db.list_series(conn)
    return jsonify(series)


@app.route("/api/series/<path:series_id>/data")
def api_series_data(series_id: str):
    range_key = request.args.get("range", "24h")
    now = int(time.time())

    if request.args.get("start") and request.args.get("end"):
        try:
            start_ts = int(request.args["start"])
            end_ts = int(request.args["end"])
        except ValueError:
            abort(400, "start/end doivent être des timestamps unix")
    else:
        if range_key not in RANGE_PRESETS:
            abort(400, f"range invalide, valeurs possibles: {list(RANGE_PRESETS)}")
        start_ts = now - RANGE_PRESETS[range_key]
        end_ts = now

    with closing(_read_conn()) as conn:
        rows = db.query_readings(conn, series_id, start_ts, end_ts)

    return jsonify(
        {
            "series_id": series_id,
            "start": start_ts,
            "end": end_ts,
            "points": [{"ts": ts, "value": value} for ts, value in rows],
        }
    )


def create_app(config_path: str = "config.yaml") -> Flask:
    cfg = load_config(config_path)
    app.config["LOXONE_CFG"] = cfg
    # Crée la base + le schéma tout de suite, y compris si le poller n'a pas
    # encore tourné (évite une erreur 500 sur un dashboard vide au premier
    # démarrage).
    db.get_connection(cfg.db_path).close()
    start_background_poller(cfg)
    return app


if __name__ == "__main__":
    flask_app = create_app()
    cfg = flask_app.config["LOXONE_CFG"]
    flask_app.run(host=cfg.host_bind, port=cfg.port, debug=False, use_reloader=False)
