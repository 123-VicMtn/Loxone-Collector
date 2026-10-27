"""Décompte de charges : calcul, exports Excel et CSV, tarifs."""

from __future__ import annotations

import csv
import io
import logging
import re
from contextlib import closing

from flask import Blueprint, abort, jsonify, request, send_file
from flask_login import current_user, login_required

from loxone_collector import db
from loxone_collector.api import access
from loxone_collector.api.access import (
    admin_required,
    current_cfg,
    read_conn,
    resolve_miniserver,
)
from loxone_collector.exports import decompte as decompte_export
from loxone_collector.exports import decompte_xlsx
from loxone_collector.services import billing, statistics_sync

logger = logging.getLogger("app")

bp = Blueprint("decompte", __name__)


# Message affiché quand les Statistics du Miniserver n'ont pas pu être
# relues. Le message technique (adresse, exception) reste dans le log
# (services/statistics_sync), jamais dans la réponse.
MESSAGE_STATISTIQUES_INJOIGNABLES = (
    "Miniserver injoignable : les derniers jours peuvent manquer."
)


def _decompte_payload(ms_name: str, now: int, mois: str | None = None) -> dict:
    """Décompte complet d'un site. `from` / `to` (clés YYYY-MM) bornent
    les mois ; sans eux, tous les mois couverts par les données.

    `mois` (export) : ajoute pour ce mois le chauffage, l'eau chaude et la
    buanderie (`payload["postes"]`). Leurs Statistics ne sont lues qu'ici,
    pas à chaque affichage de la page."""
    with closing(read_conn()) as conn:
        series = [s for s in db.list_series(conn) if s["miniserver"] == ms_name]
        tarifs = db.list_tarifs(conn, ms_name)
        start_hint = end_hint = None
        try:
            if request.args.get("from"):
                year, month = billing.parse_period_key(request.args["from"])
                start_hint = billing.period_bounds(year, month)[0]
            if request.args.get("to"):
                year, month = billing.parse_period_key(request.args["to"])
                end_hint = billing.period_bounds(year, month)[1]
            periode_export = None
            if mois:
                year, month = billing.parse_period_key(mois)
                start, end = billing.period_bounds(year, month)
                periode_export = {"key": mois, "start": start, "end": end}
                start_hint = start if start_hint is None else start_hint
                end_hint = end if end_hint is None else end_hint
        except ValueError as exc:
            abort(400, str(exc))
        # Les index viennent des Statistics du Miniserver, pas du collecteur.
        sync_info = statistics_sync.sync_decompte(
            current_cfg(), conn, ms_name, series, now, start_hint, end_hint,
            postes=periode_export is not None,
        )
        zones = billing.resolve_zones(series)
        batiment_src = billing.resolve_batiment(series)

        def finaliser(payload: dict) -> dict:
            payload["miniserver"] = ms_name
            payload["statistiques_erreur"] = (
                MESSAGE_STATISTIQUES_INJOIGNABLES if sync_info.get("erreur") else None
            )
            if periode_export:
                payload["postes"] = {mois: billing.compute_postes(
                    conn, billing.resolve_postes(series), periode_export, now,
                )}
            return payload

        rng = billing.available_range(conn, zones, batiment_src)
        if rng is None:
            return finaliser(billing.compute_decompte(conn, series, [], tarifs, now))
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
        return finaliser(billing.compute_decompte(conn, series, periods, tarifs, now))


@bp.route("/api/decompte")
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
    ms_name = resolve_miniserver(request.args.get("miniserver"))
    return jsonify(_decompte_payload(ms_name, access.maintenant()))


@bp.route("/api/decompte/export.csv")
@login_required
def api_decompte_csv():
    """Mois demandé (`mois=YYYY-MM`) : une section par tableau
    (électricité, chauffage, eau chaude, buanderie)."""
    ms_name = resolve_miniserver(request.args.get("miniserver"))
    mois = (request.args.get("mois") or "").strip()
    try:
        billing.parse_period_key(mois)
    except ValueError as exc:
        abort(400, str(exc))
    payload = _decompte_payload(ms_name, access.maintenant(), mois=mois)
    try:
        rows = decompte_export.export_csv_lignes(payload, mois)
    except KeyError:
        abort(400, f"aucune donnée de décompte pour {mois}")
    safe_site = re.sub(r"[^A-Za-z0-9._-]+", "_", ms_name).strip("_") or "site"
    buf = io.StringIO()
    csv.writer(buf, delimiter=";").writerows(rows)
    data = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    return send_file(
        data, mimetype="text/csv; charset=utf-8", as_attachment=True,
        download_name=f"decompte-{safe_site}-{mois}.csv",
    )


@bp.route("/api/decompte/export.xlsx")
@login_required
def api_decompte_xlsx():
    """Même mois que le CSV. Onglet Électricité : kWh, buanderie, prix et
    montants. Onglets Chauffage, Eau chaude, Buanderie quand le site en a.
    Onglet Frais supplémentaires : tableau vide par zone, à remplir.
    Onglet Relevés : un point horaire des statistiques par ligne."""
    ms_name = resolve_miniserver(request.args.get("miniserver"))
    mois = (request.args.get("mois") or "").strip()
    try:
        billing.parse_period_key(mois)
    except ValueError as exc:
        abort(400, str(exc))
    payload = _decompte_payload(ms_name, access.maintenant(), mois=mois)
    try:
        with closing(read_conn()) as conn:
            wb = decompte_xlsx.build_decompte_workbook(payload, mois, ms_name, conn)
    except KeyError:
        abort(400, f"aucune donnée de décompte pour {mois}")
    safe_site = re.sub(r"[^A-Za-z0-9._-]+", "_", ms_name).strip("_") or "site"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"decompte-{safe_site}-{mois}.xlsx",
    )


@bp.route("/api/tarifs", methods=["GET", "POST"])
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
        ms_name = resolve_miniserver(request.args.get("miniserver"))
        with closing(read_conn()) as conn:
            return jsonify(db.list_tarifs(conn, ms_name))

    if not current_user.is_admin:
        return jsonify({"error": "forbidden"}), 403

    payload = request.get_json(force=True, silent=True) or {}
    ms_name = resolve_miniserver(payload.get("miniserver"))
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

    with closing(read_conn()) as conn:
        db.upsert_tarif(
            conn, ms_name, valid_from, prix_reseau, prix_solaire, taux_tva,
            str(payload.get("note", "")),
        )
        return jsonify(db.list_tarifs(conn, ms_name))


@bp.route("/api/tarifs/<int:tarif_id>", methods=["DELETE"])
@admin_required
def api_tarif_delete(tarif_id: int):
    ms_name = resolve_miniserver(request.args.get("miniserver"))
    with closing(read_conn()) as conn:
        # Avant, la suppression ne regardait que l'identifiant : le tarif
        # d'un autre site partait, quel que soit `miniserver`.
        if not db.delete_tarif(conn, ms_name, tarif_id):
            abort(404, "tarif introuvable pour ce site")
        return jsonify(db.list_tarifs(conn, ms_name))

