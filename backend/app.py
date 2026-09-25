"""
app.py
------
Point d'entrée de l'application : lance en tâche de fond le poller qui
interroge les Miniservers Loxone et écrit en SQLite, et expose une API
JSON (`/api/*`, `/health`) consommée par le frontend Vue (`frontend/`,
app autonome -- voir CLAUDE.md "Backend 100% API"). Flask ne sert aucune
page HTML lui-même.

Lancement (dev) :
    python app.py

Lancement (prod, sur le Pi) : voir scripts/loxone-collector.service
(gunicorn n'est volontairement pas utilisé ici : le serveur de dev Flask,
mono-process, suffit largement pour cette API sur quelques utilisateurs, et
évite de multiplier les connexions SQLite/la RAM utilisée sur un Pi à 2 Go).
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request, abort, send_file
from openpyxl import Workbook
from flask_login import current_user, login_required, login_user, logout_user

import auth
import billing
import classification
import db
from config import AppConfig, ConfigError, load_config
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


def _allowed_miniservers() -> list[str]:
    """Sites que le compte courant peut voir -- tous les sites configurés
    pour un 'admin', uniquement les siens pour un 'user' (voir CLAUDE.md,
    "Rôles utilisateurs"). Filtré par les sites RÉELLEMENT configurés :
    un site accordé à un 'user' puis retiré de config.yaml ne doit pas
    ressusciter côté accès."""
    configured = [ms.name for ms in _cfg().miniservers]
    if current_user.is_admin:
        return configured
    granted = set(current_user.miniservers)
    return [n for n in configured if n in granted]


def admin_required(fn):
    """Remplace @login_required sur les routes d'écriture (classification,
    tarifs) : un compte 'user' est en lecture seule (décision explicite de
    l'utilisateur, voir CLAUDE.md) -- 403, pas 401 (il EST authentifié,
    juste pas autorisé à cette action)."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return auth.login_manager.unauthorized()
        if not current_user.is_admin:
            return jsonify({"error": "forbidden"}), 403
        return fn(*args, **kwargs)
    return wrapper


def _check_series_access(conn, series_id: str) -> None:
    """403 si la série n'appartient pas à un site que le compte courant
    peut voir -- utilisé par les 3 routes qui servent les données d'UNE
    série précise (data/latest/daily), qui ne passent pas par
    _resolve_miniserver (elles ne prennent pas `miniserver` en paramètre,
    le site se déduit de la série demandée)."""
    if current_user.is_admin:
        return
    ms = db.get_series_miniserver(conn, series_id)
    if ms is not None and ms not in current_user.miniservers:
        abort(403, "accès refusé à cette série")


# --------------------------------------------------------------------------
# Authentification (voir auth.py, docs/plan-installation-auth-frontend-docker.md)
# --------------------------------------------------------------------------

def _user_payload() -> dict:
    """Forme renvoyée par /api/login et /api/me -- `miniservers` est déjà
    résolu via _allowed_miniservers() (tous les sites configurés pour un
    admin, pas la liste brute de user_miniservers) : le frontend n'a pas à
    connaître la distinction admin/user pour savoir quels sites afficher,
    juste à lire ce champ."""
    return {
        "username": current_user.username,
        "role": current_user.role,
        "miniservers": _allowed_miniservers(),
    }


@app.route("/api/login", methods=["POST"])
def api_login():
    """Connexion par cookie de session (Flask-Login). Le frontend Vue
    affiche le formulaire ; ici on ne renvoie que du JSON, jamais de
    redirection -- ce n'est pas une route de page."""
    payload = request.get_json(force=True, silent=True) or {}
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    if not username or not password:
        abort(400, "username et password requis")

    user = auth.verify_login(username, password)
    if user is None:
        return jsonify({"error": "identifiants invalides"}), 401

    login_user(user)
    return jsonify(_user_payload())


@app.route("/api/logout", methods=["POST"])
@login_required
def api_logout():
    logout_user()
    return jsonify({"ok": True})


@app.route("/api/me")
def api_me():
    """Session en cours, ou 401 -- interrogé par le frontend au chargement
    pour savoir s'il doit afficher la page de connexion. Volontairement SANS
    @login_required : un 401 ici est une réponse normale (pas connecté),
    pas une erreur d'accès à signaler comme les autres routes protégées."""
    if not current_user.is_authenticated:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(_user_payload())


@app.route("/api/series/<path:series_id>/classify", methods=["POST"])
@admin_required
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
@login_required
def api_miniservers():
    """Sites accessibles au compte courant (tous les sites configurés pour
    un admin, seulement les siens pour un 'user' -- voir
    _allowed_miniservers) -- sert au sélecteur de site de /decompte, qui
    scope tout le reste de la page (la consommation d'une zone n'a de sens
    que rattachée à un site physique, voir CLAUDE.md)."""
    return jsonify(_allowed_miniservers())


@app.route("/api/resource-types")
@login_required
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
@login_required
def api_series():
    allowed = set(_allowed_miniservers())
    with closing(_read_conn()) as conn:
        series = [s for s in db.list_series(conn) if s["miniserver"] in allowed]
    return jsonify(series)


@app.route("/api/releves")
@login_required
def api_releves():
    """Dernier index connu de chaque compteur cumulatif (total / totalNeg).
    Un relevé est l'index du compteur, pas la puissance instantanée."""
    allowed = set(_allowed_miniservers())
    with closing(_read_conn()) as conn:
        series = [
            s for s in db.list_series(conn)
            if s["miniserver"] in allowed
            and s["control_type"] == "Meter"
            and s["state_name"] in ("total", "totalNeg")
        ]
        rows = []
        for s in series:
            latest = db.query_latest(conn, s["series_id"])
            rows.append({
                "series_id": s["series_id"],
                "miniserver": s["miniserver"],
                "label": s["label"],
                "apartment": s["apartment"],
                "unit": s["unit"],
                "state_name": s["state_name"],
                "ts": latest[0] if latest else None,
                "value": latest[1] if latest else None,
            })
    return jsonify(rows)


@app.route("/api/series/<path:series_id>/data")
@login_required
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
        _check_series_access(conn, series_id)
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
@login_required
def api_series_latest(series_id: str):
    """Dernière valeur connue d'une série (peu importe son âge) -- utilisé
    pour les tuiles de synthèse (ex: totalDay/totalWeek/totalMonth/totalYear
    d'un compteur : ce sont des compteurs vivants sans historique Statistics
    propre, voir db.query_daily_last -- seule leur dernière valeur lue par
    le poller a un sens, pas un historique)."""
    with closing(_read_conn()) as conn:
        _check_series_access(conn, series_id)
        latest = db.query_latest(conn, series_id)
    if latest is None:
        return jsonify({"series_id": series_id, "ts": None, "value": None})
    ts, value = latest
    return jsonify({"series_id": series_id, "ts": ts, "value": value})


@app.route("/api/series/<path:series_id>/daily")
@login_required
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
        _check_series_access(conn, series_id)
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


@app.route("/api/series/<path:series_id>/range")
@login_required
def api_series_range(series_id: str):
    """Consommation d'une série cumulative sur [from, to[ : relevé de fin -
    relevé de début (billing.reading_delta), la même méthode que /decompte,
    plutôt qu'un compteur vivant totalDay/Week/Month/Year sans historique
    Statistics propre (voir CLAUDE.md, "Prochaine étape prévue" /
    refactor extraction des données 2026-09-24) -- utilisé par le dashboard
    pour ses tuiles KPI et sélecteur de dates, pour tous les sites et tous
    les types de ressource (pas seulement l'énergie, voir
    billing.min_drop_for_resource_type)."""
    try:
        start_ts = int(request.args["from"])
        end_ts = int(request.args["to"])
    except (KeyError, ValueError):
        abort(400, "from/to sont requis et doivent être des timestamps unix")
    if end_ts <= start_ts:
        abort(400, "to doit être strictement supérieur à from")

    now = int(time.time())
    with closing(_read_conn()) as conn:
        _check_series_access(conn, series_id)
        resource_type = db.get_series_resource_type(conn, series_id)
        min_drop = billing.min_drop_for_resource_type(resource_type)
        delta = billing.reading_delta(conn, series_id, start_ts, end_ts, now, min_drop=min_drop)

    return jsonify({"series_id": series_id, "from": start_ts, "to": end_ts, **delta})


# --------------------------------------------------------------------------
# Décompte de charges (API -- la page /decompte est servie par le frontend
# Vue en tant qu'app autonome, pas par Flask, voir CLAUDE.md "Backend 100% API")
# --------------------------------------------------------------------------

def _resolve_miniserver(name: str | None) -> str:
    """Valide (ou choisit par défaut) le site sur lequel scoper un appel
    /api/decompte ou /api/tarifs. Un décompte n'a de sens que rattaché à UN
    site physique (miniserver) : mélanger les zones de deux immeubles dans
    un même calcul fausserait consommations ET montants facturés -- voir
    CLAUDE.md, "Décompte de charges".

    Scopé par _allowed_miniservers() (pas tous les sites configurés) : un
    compte 'user' ne doit jamais pouvoir calculer le décompte d'un site qui
    ne lui a pas été accordé, même en devinant/forçant le paramètre
    `miniserver` dans l'URL -- 403 (site existant mais pas autorisé),
    distinct du 400 (site qui n'existe pas du tout)."""
    configured = [ms.name for ms in _cfg().miniservers]
    allowed = _allowed_miniservers()
    if name is None:
        if not allowed:
            abort(403, "aucun site accessible pour ce compte")
        return allowed[0]
    if name not in configured:
        abort(400, f"miniserver invalide: {name!r}. Valeurs possibles: {configured}")
    if name not in allowed:
        abort(403, f"accès refusé au site {name!r}")
    return name


def _decompte_payload(ms_name: str, now: int) -> dict:
    """Décompte complet d'un site. `from` / `to` (clés YYYY-MM) bornent
    les mois ; sans eux, tous les mois couverts par les données."""
    with closing(_read_conn()) as conn:
        series = [s for s in db.list_series(conn) if s["miniserver"] == ms_name]
        tarifs = db.list_tarifs(conn, ms_name)
        zones = billing.resolve_zones(series)
        batiment_src = billing.resolve_batiment(series)

        rng = billing.available_range(conn, zones, batiment_src)
        if rng is None:
            payload = billing.compute_decompte(conn, series, [], tarifs, now)
            payload["miniserver"] = ms_name
            return payload
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

    return payload


@app.route("/api/decompte")
@login_required
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
    return jsonify(_decompte_payload(ms_name, int(time.time())))


def _export_table():
    """Lignes du mois demandé (`mois=YYYY-MM`) pour le site, mêmes chiffres
    que le tableau de la page."""
    ms_name = _resolve_miniserver(request.args.get("miniserver"))
    mois = (request.args.get("mois") or "").strip()
    try:
        billing.parse_period_key(mois)
    except ValueError as exc:
        abort(400, str(exc))
    payload = _decompte_payload(ms_name, int(time.time()))
    try:
        rows = billing.export_lignes(payload, mois)
    except KeyError:
        abort(400, f"aucune donnée de décompte pour {mois}")
    safe_site = re.sub(r"[^A-Za-z0-9._-]+", "_", ms_name).strip("_") or "site"
    return rows, f"decompte-{safe_site}-{mois}"


def _csv_cell(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        return f"{value:.2f}".replace(".", ",")
    return str(value)


@app.route("/api/decompte/export.csv")
@login_required
def api_decompte_csv():
    rows, stem = _export_table()
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    for row in rows:
        writer.writerow([_csv_cell(c) if not isinstance(c, str) else c for c in row])
    data = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    return send_file(
        data, mimetype="text/csv; charset=utf-8", as_attachment=True,
        download_name=f"{stem}.csv",
    )


@app.route("/api/decompte/export.xlsx")
@login_required
def api_decompte_xlsx():
    rows, stem = _export_table()
    wb = Workbook()
    ws = wb.active
    ws.title = "Décompte"
    for row in rows:
        ws.append(row)
    for col in ("C", "D", "E"):
        for cell in ws[col][1:]:
            if isinstance(cell.value, float):
                cell.number_format = "0.00"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"{stem}.xlsx",
    )


@app.route("/api/tarifs", methods=["GET", "POST"])
@login_required
def api_tarifs():
    """Tarifs appliqués au décompte d'UN site (chaque site peut avoir un
    fournisseur/contrat différent). Stockés en base (et non dans le
    navigateur) pour qu'un mois déjà facturé reste reproductible à
    l'identique après un changement de prix -- voir la table `tarifs`.

    GET : lecture seule, ouverte à tout compte ayant accès à ce site
    (`_resolve_miniserver` filtre déjà par _allowed_miniservers). POST :
    admin uniquement -- un compte 'user' ne modifie jamais les tarifs
    (décision explicite, voir CLAUDE.md "Rôles utilisateurs"). Une seule
    fonction de vue plutôt que @admin_required en décorateur : GET et POST
    n'ont pas les mêmes droits ici, contrairement aux autres routes."""
    if request.method == "GET":
        ms_name = _resolve_miniserver(request.args.get("miniserver"))
        with closing(_read_conn()) as conn:
            return jsonify(db.list_tarifs(conn, ms_name))

    if not current_user.is_admin:
        return jsonify({"error": "forbidden"}), 403

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
@admin_required
def api_tarif_delete(tarif_id: int):
    ms_name = _resolve_miniserver(request.args.get("miniserver"))
    with closing(_read_conn()) as conn:
        db.delete_tarif(conn, tarif_id)
        return jsonify(db.list_tarifs(conn, ms_name))


def create_app(config_path: str = "config.yaml") -> Flask:
    cfg = load_config(config_path)  # charge aussi .env (voir config.load_config)
    app.config["LOXONE_CFG"] = cfg
    # Crée la base + le schéma tout de suite, y compris si le poller n'a pas
    # encore tourné (évite une erreur 500 sur un dashboard vide au premier
    # démarrage) -- crée aussi la table `users` au passage (voir db.SCHEMA).
    db.get_connection(cfg.db_path).close()

    if "SECRET_KEY" not in os.environ:
        raise ConfigError(
            "Variable d'environnement 'SECRET_KEY' absente (nécessaire pour "
            "signer les cookies de session -- vérifie ton fichier .env, voir "
            ".env.example). Générer une valeur : "
            "python3 -c \"import secrets; print(secrets.token_hex(32))\""
        )
    app.secret_key = os.environ["SECRET_KEY"]
    auth.login_manager.init_app(app)

    start_background_poller(cfg)
    return app


if __name__ == "__main__":
    import sys
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    flask_app = create_app(config_path)
    cfg = flask_app.config["LOXONE_CFG"]
    flask_app.run(host=cfg.host_bind, port=cfg.port, debug=False, use_reloader=False)
