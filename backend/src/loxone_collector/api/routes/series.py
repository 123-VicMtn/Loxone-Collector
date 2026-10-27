"""Sites, séries, relevés et puissance de l'aperçu (dashboard)."""

from __future__ import annotations

import logging
from contextlib import closing

from flask import Blueprint, abort, jsonify, request
from flask_login import login_required

from loxone_collector import db
from loxone_collector.api import access
from loxone_collector.api.access import (
    allowed_miniservers,
    check_series_access,
    current_cfg,
    read_conn,
    resolve_miniserver,
)
from loxone_collector.loxone.client import LoxoneAuthError, LoxoneClient, LoxoneError
from loxone_collector.loxone.statistics import points_by_series
from loxone_collector.services import billing

logger = logging.getLogger("app")

bp = Blueprint("series", __name__)


@bp.route("/api/miniservers")
@login_required
def api_miniservers():
    """Sites accessibles au compte courant (tous les sites configurés pour
    un admin, seulement les siens pour un 'user' -- voir
    _allowed_miniservers) -- sert au sélecteur de site de /decompte, qui
    scope tout le reste de la page (la consommation d'une zone n'a de sens
    que rattachée à un site physique, voir CLAUDE.md)."""
    return jsonify(allowed_miniservers())


@bp.route("/api/resource-types")
@login_required
def api_resource_types():
    """Libellés des types de ressource (config.yaml, clé
    `resource_type_labels`) -- avant les pages Vue, ceci n'était injecté
    qu'en Jinja (`window.RESOURCE_TYPE_LABELS` dans templates/index.html,
    dict `labels` de templates/admin.html) ; une page servie en statique
    pur a besoin d'un vrai endpoint. Utilisé par /admin (select de
    classification) et par le futur dashboard (sidebar, onglets Énergie/
    zone)."""
    return jsonify(current_cfg().resource_type_labels)


RANGE_PRESETS = {
    "1h": 3600,
    "24h": 86400,
    "7d": 7 * 86400,
    "30d": 30 * 86400,
    "1y": 365 * 86400,
}


@bp.route("/api/series")
@login_required
def api_series():
    allowed = set(allowed_miniservers())
    with closing(read_conn()) as conn:
        series = [s for s in db.list_series(conn) if s["miniserver"] in allowed]
    return jsonify(series)


@bp.route("/api/releves")
@login_required
def api_releves():
    """Dernier index connu de chaque compteur cumulatif (total / totalNeg).
    Un relevé est l'index du compteur, pas la puissance instantanée."""
    allowed = set(allowed_miniservers())
    with closing(read_conn()) as conn:
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


@bp.route("/api/series/<path:series_id>/data")
@login_required
def api_series_data(series_id: str):
    range_key = request.args.get("range", "24h")
    now = access.maintenant()

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

    with closing(read_conn()) as conn:
        check_series_access(conn, series_id)
        rows = db.query_readings(conn, series_id, start_ts, end_ts)

    return jsonify(
        {
            "series_id": series_id,
            "start": start_ts,
            "end": end_ts,
            "points": [{"ts": ts, "value": value} for ts, value in rows],
        }
    )


def _client_for(name: str) -> LoxoneClient:
    ms = next(m for m in current_cfg().miniservers if m.name == name)
    return LoxoneClient(
        name=ms.name,
        host=ms.host,
        username=ms.username,
        password=ms.password,
        port=ms.port,
        scheme=ms.scheme,
        verify_ssl=ms.verify_ssl,
    )


@bp.route("/api/apercu/puissance", methods=["POST"])
@login_required
def api_apercu_puissance():
    """Puissance (kW) depuis une borne, lue sur le Miniserver (Statistics),
    pas dans les mesures du collecteur. L'aperçu s'en sert pour la courbe
    du jour : elle reste disponible quand le poller est arrêté."""
    payload = request.get_json(force=True, silent=True) or {}
    ms_name = resolve_miniserver(payload.get("miniserver"))
    try:
        from_ts = int(payload["from"])
        to_ts = int(payload["to"])
    except (KeyError, TypeError, ValueError):
        abort(400, "from/to sont requis et doivent être des timestamps unix")
    if to_ts <= from_ts:
        abort(400, "to doit être strictement supérieur à from")
    if to_ts - from_ts > 48 * 3600:
        abort(400, "la fenêtre de puissance est limitée à 48 h")
    raw_ids = payload.get("series_ids")
    if not isinstance(raw_ids, list) or any(not isinstance(s, str) or not s for s in raw_ids):
        abort(400, "series_ids doit être une liste d'identifiants")
    series_ids = list(dict.fromkeys(raw_ids))
    if len(series_ids) > 80:
        abort(400, "trop de séries demandées")
    if not series_ids:
        return jsonify({"miniserver": ms_name, "from": from_ts, "to": to_ts,
                        "points": {}, "missing": []})

    with closing(read_conn()) as conn:
        rows = db.get_series_rows(conn, series_ids)
    found = {row["series_id"]: row for row in rows}
    unknown = [sid for sid in series_ids if sid not in found]
    if unknown:
        abort(400, "série inconnue")
    for row in rows:
        if row["miniserver"] != ms_name:
            abort(403, "accès refusé à cette série")
        if row["state_name"] != "actual":
            abort(400, "seule la puissance (actual) se lit sur le Miniserver pour ce graphique")

    client = _client_for(ms_name)
    try:
        structure = client.fetch_structure()
        points, missing = points_by_series(client, structure, rows, from_ts, to_ts)
    except LoxoneAuthError as exc:
        logger.error("[%s] Statistics: %s", ms_name, exc)
        return jsonify({"error": "le Miniserver a refusé l'authentification"}), 502
    except LoxoneError as exc:
        logger.warning("[%s] Statistics: %s", ms_name, exc)
        return jsonify({"error": "le Miniserver n'a pas répondu"}), 502
    finally:
        client.close()

    return jsonify({
        "miniserver": ms_name,
        "from": from_ts,
        "to": to_ts,
        "points": points,
        "missing": missing,
    })


@bp.route("/api/series/<path:series_id>/latest")
@login_required
def api_series_latest(series_id: str):
    """Dernière valeur connue d'une série (peu importe son âge) -- utilisé
    pour les tuiles de synthèse (ex: totalDay/totalWeek/totalMonth/totalYear
    d'un compteur : ce sont des compteurs vivants sans historique Statistics
    propre, voir db.query_daily_last -- seule leur dernière valeur lue par
    le poller a un sens, pas un historique)."""
    with closing(read_conn()) as conn:
        check_series_access(conn, series_id)
        latest = db.query_latest(conn, series_id)
    if latest is None:
        return jsonify({"series_id": series_id, "ts": None, "value": None})
    ts, value = latest
    return jsonify({"series_id": series_id, "ts": ts, "value": value})


@bp.route("/api/series/<path:series_id>/daily")
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

    now = access.maintenant()
    # Un jour de marge avant le début demandé, pour pouvoir calculer le
    # delta du tout premier jour retourné (sinon son delta serait inconnu,
    # faute de relevé antérieur dans la fenêtre).
    start_ts = now - (days + 1) * 86400

    with closing(read_conn()) as conn:
        check_series_access(conn, series_id)
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


@bp.route("/api/series/<path:series_id>/range")
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

    now = access.maintenant()
    with closing(read_conn()) as conn:
        check_series_access(conn, series_id)
        resource_type = db.get_series_resource_type(conn, series_id)
        min_drop = billing.min_drop_for_resource_type(resource_type)
        delta = billing.reading_delta(conn, series_id, start_ts, end_ts, now, min_drop=min_drop)

    return jsonify({"series_id": series_id, "from": start_ts, "to": end_ts, **delta})

