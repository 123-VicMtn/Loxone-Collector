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
import re
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request, abort

import classification
import db
from config import AppConfig, load_config
from loxone_client import LoxoneAuthError, LoxoneClient, LoxoneError, extract_measurable_points
from loxone_ws_client import LoxoneWsError, fetch_live_values

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
                    host=ms_cfg.host,
                    port=ms_cfg.port,
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
        except (LoxoneError, LoxoneWsError) as exc:
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


def _apartment_sort_key(name: str):
    """Tri "naturel" par numéro d'appartement (App 2 avant App 10) --
    encore utilisé par /admin pour la liste d'autocomplete des appartements
    connus. Le regroupement de la sidebar (qui utilisait aussi cette
    fonction avant le refactor JS) a son équivalent côté client dans
    static/js/sidebar.js (compareApartments)."""
    m = re.search(r"\d+", name)
    return (0, int(m.group())) if m else (1, name)


@app.route("/")
def index():
    # La sidebar de sélection des capteurs (regroupement par appartement ou
    # par pièce) est entièrement rendue côté client (static/js/sidebar.js),
    # à partir de GET /api/series -- la même source que les onglets Énergie
    # et Consommations par zone. Avant ce refactor, ce regroupement était
    # calculé ici en Python (build_apartment_groups/build_room_groups) ET
    # refait côté JS pour les autres onglets : même donnée, deux
    # implémentations à maintenir. Cette route ne fait donc plus que
    # rendre le squelette de la page.
    return render_template(
        "index.html",
        range_presets=list(RANGE_PRESETS.keys()),
        resource_type_labels=_cfg().resource_type_labels,
    )


@app.route("/admin")
def admin():
    with closing(_read_conn()) as conn:
        series = db.list_series(conn)

    labels = _cfg().resource_type_labels
    known_apartments = sorted(
        {s["apartment"] for s in series if s["apartment"]}, key=_apartment_sort_key
    )

    return render_template(
        "admin.html",
        series=series,
        labels=labels,
        known_apartments=known_apartments,
    )


@app.route("/api/series/<path:series_id>/classify", methods=["POST"])
def api_classify(series_id: str):
    payload = request.get_json(force=True, silent=True) or {}

    with closing(_read_conn()) as conn:
        if payload.get("reset"):
            db.reset_series_classification(conn, series_id)
        else:
            apartment = payload.get("apartment")
            resource_type = payload.get("resource_type")
            if apartment is None and resource_type is None:
                abort(400, "apartment et/ou resource_type (ou reset:true) requis")
            db.set_series_classification(conn, series_id, apartment=apartment, resource_type=resource_type)

    return jsonify({"ok": True})


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


@app.route("/api/series/<path:series_id>/latest")
def api_series_latest(series_id: str):
    """Dernière valeur connue d'une série (peu importe son âge) -- utilisé
    pour les tuiles de synthèse (ex: totalDay/totalWeek/totalMonth/totalYear
    d'un compteur : ce sont des compteurs vivants sans historique Statistics
    propre, voir db.query_daily_last -- seule leur dernière valeur lue par
    le poller a un sens, pas un historique)."""
    with closing(_read_conn()) as conn:
        latest = db.query_latest(conn, series_id)
    if latest is None:
        return jsonify({"series_id": series_id, "ts": None, "value": None})
    ts, value = latest
    return jsonify({"series_id": series_id, "ts": ts, "value": value})


@app.route("/api/series/<path:series_id>/daily")
def api_series_daily(series_id: str):
    """Relevés de fin de journée + consommation journalière dérivée (delta
    entre deux relevés successifs), pour une série cumulative -- un index
    croissant (state "total"/"totalNeg" typiquement). Voir db.query_daily_last
    pour le détail de la méthode -- c'est la même logique qu'un décompte de
    charges (différence entre deux relevés de compteur), appliquée jour par
    jour. Ne JAMAIS appeler cette route sur une puissance instantanée
    (state "actual", ou "Gpwr"/"Ppwr"/"Spwr" d'un bloc EFM) : MAX(valeur) par
    jour n'a de sens que sur un cumul qui ne fait que croître."""
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        abort(400, "days doit être un entier")
    days = max(1, min(days, 400))

    now = int(time.time())
    # Un jour de marge avant le début demandé, pour pouvoir calculer le
    # delta du tout premier jour retourné (sinon son delta serait inconnu,
    # faute de relevé antérieur dans la fenêtre).
    start_ts = now - (days + 1) * 86400

    with closing(_read_conn()) as conn:
        rows = db.query_daily_last(conn, series_id, start_ts, now)

    points = []
    for i in range(1, len(rows)):
        day_ts, end_value = rows[i]
        _, prev_value = rows[i - 1]
        consumption = end_value - prev_value
        # Un delta négatif signale un compteur qui est reparti de zéro
        # (remplacement de compteur, reset) plutôt qu'une "consommation
        # négative" -- on le remonte tel quel (pas de valeur aberrante
        # masquée), à charge pour l'affichage de le signaler.
        points.append({"date_ts": day_ts, "end_value": end_value, "consumption": consumption})

    return jsonify({"series_id": series_id, "days": days, "points": points})


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
    import sys
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    flask_app = create_app(config_path)
    cfg = flask_app.config["LOXONE_CFG"]
    flask_app.run(host=cfg.host_bind, port=cfg.port, debug=False, use_reloader=False)
