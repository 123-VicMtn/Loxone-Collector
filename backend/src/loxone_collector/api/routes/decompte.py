"""Décompte de charges : calcul, exports Excel et CSV, tarifs."""

from __future__ import annotations

import csv
import io
import logging
import re
from contextlib import closing
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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
from loxone_collector.services import billing, historique, statistics_sync

logger = logging.getLogger("app")

bp = Blueprint("decompte", __name__)


# Message affiché quand les Statistics du Miniserver n'ont pas pu être
# relues. Le message technique (adresse, exception) reste dans le log
# (services/statistics_sync), jamais dans la réponse.
MESSAGE_STATISTIQUES_INJOIGNABLES = (
    "Miniserver injoignable : les derniers jours peuvent manquer."
)


def _statistiques_erreur(ms_name: str) -> str | None:
    dernier = statistics_sync.derniers_resultats.get(ms_name)
    return MESSAGE_STATISTIQUES_INJOIGNABLES if dernier and not dernier["ok"] else None


def _series_du_site(conn, ms_name: str) -> list[dict]:
    """Séries du site qui peuvent entrer dans le décompte (sans les
    compteurs « hors-décompte »)."""
    return billing.series_du_decompte([s for s in db.list_series(conn) if s["miniserver"] == ms_name])


def _decompte_payload(ms_name: str, now: int) -> dict:
    """Décompte complet d'un site. `from` / `to` (clés YYYY-MM) bornent
    les mois ; sans eux, tous les mois couverts par les données.

    Ne lit que la base : les index Statistics y sont importés par la
    synchro de fond et par POST /api/decompte/actualiser, jamais ici (avant
    le 2026-10-28, chaque affichage attendait le Miniserver)."""
    with closing(read_conn()) as conn:
        series = _series_du_site(conn, ms_name)
        tarifs = db.list_tarifs(conn, ms_name)
        try:
            for cle in ("from", "to"):
                if request.args.get(cle):
                    billing.parse_period_key(request.args[cle])
        except ValueError as exc:
            abort(400, str(exc))
        zones = billing.resolve_zones(series)
        batiment_src = billing.resolve_batiment(series)

        def finaliser(payload: dict) -> dict:
            payload["miniserver"] = ms_name
            payload["statistiques_erreur"] = _statistiques_erreur(ms_name)
            payload["statistiques_jusqu_au"] = statistics_sync.index_jusqu_au(conn, series)
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


@bp.route("/api/historique")
@login_required
def api_historique():
    """Historique mensuel de l'immeuble pour les graphiques (consommation,
    production, autoconsommation, injection), mois incomplets compris :
    voir services/historique.py. Ne sert pas au décompte."""
    ms_name = resolve_miniserver(request.args.get("miniserver"))
    with closing(read_conn()) as conn:
        series = _series_du_site(conn, ms_name)
        mois = historique.historique_mensuel(
            conn, series, db.list_tarifs(conn, ms_name), access.maintenant(),
        )
    return jsonify({"miniserver": ms_name, "mois": mois})


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

    `du` / `au` (YYYY-MM-DD, `au` inclus) : le décompte de ces dates
    seulement, ramené à une seule période (même calcul que l'export,
    `periode_cle` donne sa clé). C'est ce que la page affiche pour une
    plage choisie dans son calendrier.
    """
    ms_name = resolve_miniserver(request.args.get("miniserver"))
    if request.args.get("du") or request.args.get("au"):
        start, end, suffixe = _periode_export()
        payload, cle = _export_payload(ms_name, access.maintenant(), start, end, suffixe,
                                       postes=False)
        payload["periode_cle"] = cle
        return jsonify(payload)
    return jsonify(_decompte_payload(ms_name, access.maintenant()))


@bp.route("/api/decompte/actualiser", methods=["POST"])
@login_required
def api_decompte_actualiser():
    """Relit tout de suite les Statistics du site sur le Miniserver, sans
    attendre la synchro de fond. Ouvert à tout compte qui a accès au site :
    rien n'est modifié, seuls des index plus récents sont importés. Les
    séries relues il y a moins d'une heure sont sautées."""
    payload = request.get_json(force=True, silent=True) or {}
    ms_name = resolve_miniserver(payload.get("miniserver") or request.args.get("miniserver"))
    with closing(read_conn()) as conn:
        res = statistics_sync.synchroniser_site(current_cfg(), conn, ms_name, access.maintenant())
        return jsonify({
            "miniserver": ms_name,
            "statistiques_erreur": None if res["ok"] else MESSAGE_STATISTIQUES_INJOIGNABLES,
            "statistiques_jusqu_au": statistics_sync.index_jusqu_au(
                conn, _series_du_site(conn, ms_name),
            ),
        })


# Au-delà, un export n'est plus un décompte de charges (et le classeur des
# relevés horaires devient trop lourd).
EXPORT_MAX_MOIS = 36


def _periode_export() -> tuple[int, int, str]:
    """Période demandée à l'export : `mois=YYYY-MM`, ou `du` et `au`
    (YYYY-MM-DD, `au` inclus : départ d'un locataire le 15.10 → `au` =
    2026-10-15). Retourne les bornes [début, fin[ et le suffixe du nom de
    fichier."""
    mois = (request.args.get("mois") or "").strip()
    du = (request.args.get("du") or "").strip()
    au = (request.args.get("au") or "").strip()
    try:
        if mois and not (du or au):
            year, month = billing.parse_period_key(mois)
            start, end = billing.period_bounds(year, month)
            return start, end, mois
        if not (du and au) or mois:
            abort(400, "préciser soit mois=AAAA-MM, soit du=AAAA-MM-JJ et au=AAAA-MM-JJ")
        start = billing.parse_jour(du)
        end = billing.parse_jour((date.fromisoformat(au) + timedelta(days=1)).isoformat())
    except ValueError as exc:
        abort(400, str(exc))
    if end <= start:
        abort(400, "la date de fin précède la date de début")
    if len(billing.periodes_plage(start, end)) > EXPORT_MAX_MOIS:
        abort(400, f"période limitée à {EXPORT_MAX_MOIS} mois")
    return start, end, f"{du}_{au}"


def _export_payload(ms_name: str, now: int, start: int, end: int, cle: str,
                    postes: bool = True) -> tuple[dict, str]:
    """Décompte de la période [start, end[ pour l'export, et la clé de
    période sous laquelle les exports le lisent.

    Un mois (ou un morceau de mois) : le décompte de ce mois, comme la page.
    Plusieurs mois : chacun est calculé avec son verdict de répartition,
    puis additionné (`billing.cumul_periodes`) ; le détail par mois reste
    dans `payload["detail"]`. Le chauffage, l'eau chaude et la buanderie
    se lisent d'un bout à l'autre de la période (index de fin − index de
    début)."""
    with closing(read_conn()) as conn:
        series = _series_du_site(conn, ms_name)
        tarifs = db.list_tarifs(conn, ms_name)
        zones = billing.resolve_zones(series)
        rng = billing.available_range(conn, zones, billing.resolve_batiment(series))
        if rng is None or rng[0] >= end or rng[1] < start:
            abort(400, "aucune donnée de décompte sur cette période")
        # Bilan du bâtiment jugé sur toute la plage des données, comme la
        # page : un mois isolé peut boucler par hasard (Arlopi).
        mois_donnees = billing.periods_covering(*rng)
        fenetre = (min(mois_donnees[0]["start"], start), max(mois_donnees[-1]["end"], end))
        periods = billing.periodes_plage(start, end)
        payload = billing.compute_decompte(conn, series, periods, tarifs, now, fenetre=fenetre)
        if len(periods) == 1:
            cle = periods[0]["key"]
        else:
            tz = ZoneInfo(billing.TIMEZONE)
            label = (f"{datetime.fromtimestamp(start, tz):%d.%m.%Y} → "
                     f"{datetime.fromtimestamp(end - 1, tz):%d.%m.%Y}")
            payload = billing.cumul_periodes(payload, cle, label, start, end)
        payload["miniserver"] = ms_name
        payload["statistiques_erreur"] = _statistiques_erreur(ms_name)
        payload["statistiques_jusqu_au"] = statistics_sync.index_jusqu_au(conn, series)
        if postes:
            payload["postes"] = {cle: billing.compute_postes(
                conn, billing.resolve_postes(series), {"key": cle, "start": start, "end": end}, now,
            )}
        return payload, cle


def _nom_fichier(ms_name: str, suffixe: str, ext: str, zone: str | None = None) -> str:
    safe_site = re.sub(r"[^A-Za-z0-9._-]+", "_", ms_name).strip("_") or "site"
    if zone:
        safe_site += "-" + (re.sub(r"[^A-Za-z0-9._-]+", "_", zone).strip("_") or "zone")
    return f"decompte-{safe_site}-{suffixe}.{ext}"


def _export_demande(ms_name: str) -> tuple[dict, str, str, str, str | None]:
    """Décompte à exporter : période demandée et, avec `zone` (code de la
    zone, ex. APP35), une seule zone (départ d'un locataire). Retourne le
    payload, sa clé de période, le titre du site dans le fichier, le
    suffixe du nom de fichier et la zone."""
    start, end, suffixe = _periode_export()
    zone = (request.args.get("zone") or "").strip().upper() or None
    payload, cle = _export_payload(ms_name, access.maintenant(), start, end, suffixe)
    titre = ms_name
    if zone:
        try:
            payload = decompte_export.restreindre_a_zone(payload, zone)
        except KeyError:
            abort(400, "zone inconnue pour ce site")
        titre = f"{ms_name} — {payload['zone_unique']}"
    return payload, cle, titre, suffixe, zone


@bp.route("/api/decompte/export.csv")
@login_required
def api_decompte_csv():
    """Période demandée (`mois=YYYY-MM`, ou `du` / `au`) : une section par
    tableau (électricité, détail par mois s'il y en a plusieurs, chauffage,
    eau chaude, buanderie). `zone` : une seule zone."""
    ms_name = resolve_miniserver(request.args.get("miniserver"))
    payload, cle, _titre, suffixe, zone = _export_demande(ms_name)
    rows = decompte_export.export_csv_lignes(payload, cle, methode=current_user.is_admin)
    buf = io.StringIO()
    csv.writer(buf, delimiter=";").writerows(rows)
    data = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    return send_file(
        data, mimetype="text/csv; charset=utf-8", as_attachment=True,
        download_name=_nom_fichier(ms_name, suffixe, "csv", zone),
    )


@bp.route("/api/decompte/export.xlsx")
@login_required
def api_decompte_xlsx():
    """Même période que le CSV. Onglet Électricité : kWh, buanderie, prix et
    montants. Onglet Détail par mois si la période en couvre plusieurs.
    Onglets Chauffage, Eau chaude, Buanderie quand le site en a. Onglet
    Frais supplémentaires : tableau vide par zone, à remplir. Onglet
    Relevés : un point horaire des statistiques par ligne. `zone` : une
    seule zone, avec son nom dans les titres."""
    ms_name = resolve_miniserver(request.args.get("miniserver"))
    payload, cle, titre, suffixe, zone = _export_demande(ms_name)
    with closing(read_conn()) as conn:
        wb = decompte_xlsx.build_decompte_workbook(payload, cle, titre, conn,
                                                   methode=current_user.is_admin)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=_nom_fichier(ms_name, suffixe, "xlsx", zone),
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

