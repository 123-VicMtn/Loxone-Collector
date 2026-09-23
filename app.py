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

from flask import Flask, jsonify, render_template, request, abort, send_from_directory

import billing
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
    """Sert le build Vue de `frontend/pages/admin/` (`npm run build:admin`,
    écrit dans static/admin-app/) -- a remplacé la version Jinja + JS
    vanilla (`templates/admin.html` + `static/js/admin.js`, supprimés) le
    2026-09-23, après vérification (voir CLAUDE.md, "Restructuration
    multi-pages"). 404 si le build n'a pas encore été lancé, comportement
    standard de `send_from_directory`."""
    return send_from_directory(Path(app.static_folder) / "admin-app", "index.html")


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


@app.route("/api/miniservers")
def api_miniservers():
    """Sites configurés (config.yaml, clé `miniservers`) -- sert au
    sélecteur de site de /decompte, qui scope tout le reste de la page
    (la consommation d'une zone n'a de sens que rattachée à un site
    physique, voir CLAUDE.md)."""
    return jsonify([ms.name for ms in _cfg().miniservers])


@app.route("/api/resource-types")
def api_resource_types():
    """Libellés des types de ressource (config.yaml, clé
    `resource_type_labels`) -- avant les pages Vue, ceci n'était injecté
    qu'en Jinja (`window.RESOURCE_TYPE_LABELS` dans templates/index.html,
    dict `labels` de templates/admin.html) ; une page servie en statique
    pur a besoin d'un vrai endpoint. Utilisé par /admin (select de
    classification) et par le futur dashboard (sidebar, onglets Énergie/
    zone)."""
    return jsonify(_cfg().resource_type_labels)


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


# --------------------------------------------------------------------------
# Décompte de charges (page /decompte)
# --------------------------------------------------------------------------

@app.route("/decompte")
def decompte():
    """Sert le build Vue 3/TypeScript/Tailwind de `frontend/`
    (`npm run build`, écrit dans static/decompte-app/) -- voir CLAUDE.md,
    "Migration /decompte vers Vue 3". A remplacé la version Jinja + JS
    vanilla (`templates/decompte.html` + `static/js/decompte/*.js`,
    supprimés) le 2026-09-23, après validation visuelle.

    404 si `npm run build` n'a pas encore été lancé (static/decompte-app/
    n'existe pas) -- c'est un `send_from_directory` standard, aucune gestion
    d'erreur spécifique n'est nécessaire. Voir "Commandes utiles" pour la
    procédure de déploiement (le build doit être généré AVANT de
    redémarrer le service)."""
    return send_from_directory(Path(app.static_folder) / "decompte-app", "index.html")


def _resolve_miniserver(name: str | None) -> str:
    """Valide (ou choisit par défaut) le site sur lequel scoper un appel
    /api/decompte ou /api/tarifs. Un décompte n'a de sens que rattaché à UN
    site physique (miniserver) : mélanger les zones de deux immeubles dans
    un même calcul fausserait consommations ET montants facturés -- voir
    CLAUDE.md, "Décompte de charges"."""
    names = [ms.name for ms in _cfg().miniservers]
    if name is None:
        return names[0] if names else ""
    if name not in names:
        abort(400, f"miniserver invalide: {name!r}. Valeurs possibles: {names}")
    return name


@app.route("/api/decompte")
def api_decompte():
    """Décompte mensuel complet, POUR UN SITE : par zone et par mois, la
    part réseau et la part solaire autoconsommée, le taux d'autoproduction,
    les montants et les alertes de fiabilité. Voir billing.py pour la
    méthode de calcul.

    Paramètre `miniserver` : le site à facturer (défaut : le premier
    configuré). Paramètres optionnels `from` / `to` : clés de mois
    (ex: 2026-05). Sans eux, tous les mois couverts par les données
    disponibles de ce site.
    """
    ms_name = _resolve_miniserver(request.args.get("miniserver"))
    now = int(time.time())
    with closing(_read_conn()) as conn:
        series = [s for s in db.list_series(conn) if s["miniserver"] == ms_name]
        tarifs = db.list_tarifs(conn, ms_name)
        zones = billing.resolve_zones(series)
        batiment_src = billing.resolve_batiment(series)

        rng = billing.available_range(conn, zones, batiment_src)
        if rng is None:
            payload = billing.compute_decompte(conn, series, [], tarifs, now)
            payload["miniserver"] = ms_name
            return jsonify(payload)
        first_ts, last_ts = rng

        try:
            if request.args.get("from"):
                year, month = billing.parse_period_key(request.args["from"])
                first_ts = billing.period_bounds(year, month)[0]
            if request.args.get("to"):
                year, month = billing.parse_period_key(request.args["to"])
                last_ts = billing.period_bounds(year, month)[1] - 1
        except ValueError as exc:
            abort(400, str(exc))

        if first_ts > last_ts:
            abort(400, "la période de début est postérieure à la période de fin")

        periods = billing.periods_covering(first_ts, last_ts)
        payload = billing.compute_decompte(conn, series, periods, tarifs, now)
        payload["miniserver"] = ms_name

    return jsonify(payload)


@app.route("/api/tarifs", methods=["GET", "POST"])
def api_tarifs():
    """Tarifs appliqués au décompte d'UN site (chaque site peut avoir un
    fournisseur/contrat différent). Stockés en base (et non dans le
    navigateur) pour qu'un mois déjà facturé reste reproductible à
    l'identique après un changement de prix -- voir la table `tarifs`."""
    if request.method == "GET":
        ms_name = _resolve_miniserver(request.args.get("miniserver"))
        with closing(_read_conn()) as conn:
            return jsonify(db.list_tarifs(conn, ms_name))

    payload = request.get_json(force=True, silent=True) or {}
    ms_name = _resolve_miniserver(payload.get("miniserver"))
    valid_from = str(payload.get("valid_from", "")).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", valid_from):
        abort(400, "valid_from requis, au format YYYY-MM-DD")
    try:
        prix_reseau = float(payload.get("prix_reseau", 0))
        prix_solaire = float(payload.get("prix_solaire", 0))
        taux_tva = float(payload.get("taux_tva", 0))
    except (TypeError, ValueError):
        abort(400, "prix_reseau, prix_solaire et taux_tva doivent être numériques")
    if min(prix_reseau, prix_solaire, taux_tva) < 0:
        abort(400, "les prix et le taux de TVA ne peuvent pas être négatifs")

    with closing(_read_conn()) as conn:
        db.upsert_tarif(
            conn, ms_name, valid_from, prix_reseau, prix_solaire, taux_tva,
            str(payload.get("note", "")),
        )
        return jsonify(db.list_tarifs(conn, ms_name))


@app.route("/api/tarifs/<int:tarif_id>", methods=["DELETE"])
def api_tarif_delete(tarif_id: int):
    ms_name = _resolve_miniserver(request.args.get("miniserver"))
    with closing(_read_conn()) as conn:
        db.delete_tarif(conn, tarif_id)
        return jsonify(db.list_tarifs(conn, ms_name))


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
