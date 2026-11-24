"""Page Paramètres : prix, TVA, clé PAC, location, buanderie (versions
datées) et frais accessoires. Voir docs/plan-page-parametres.md.

Droits : tout compte lit les paramètres des sites qui lui sont accordés et
les modifie (le `user` est le responsable de son site) ; les valeurs
globales (site '') ne se modifient que par l'admin
(`access.peut_modifier_parametres`)."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, jsonify, request
from flask_login import current_user, login_required

from loxone_collector import db
from loxone_collector.api import access
from loxone_collector.api.access import read_conn, resolve_miniserver
from loxone_collector.services import billing
from loxone_collector.services import parametres as par
from loxone_collector.services.billing import TIMEZONE

bp = Blueprint("parametres", __name__)


def _aujourdhui() -> str:
    return datetime.fromtimestamp(access.maintenant(), ZoneInfo(TIMEZONE)).date().isoformat()


def _versions(lignes: list[dict]) -> list[dict]:
    return [{**v, "valeur": par.depuis_texte(v["cle"], v["valeur"])} for v in lignes]


def _zones(conn, ms_name: str) -> list[dict]:
    """Zones du décompte du site (lots, Communs, PAC), pour les frais."""
    series = billing.series_du_decompte([s for s in db.list_series(conn) if s["miniserver"] == ms_name])
    return [{"zone": z["zone"], "label": z["label"]} for z in billing.resolve_zones(series)]


def _etat(conn, ms_name: str) -> dict:
    """Réponse commune à la lecture et aux modifications."""
    versions = db.list_parametres(conn, [ms_name, par.GLOBAL])
    return {
        "zones": _zones(conn, ms_name),
        "miniserver": ms_name,
        "aujourdhui": _aujourdhui(),
        "en_vigueur": par.valeurs_au(conn, ms_name, _aujourdhui()),
        "versions": _versions([v for v in versions if v["miniserver"] == ms_name]),
        "globales": _versions([v for v in versions if v["miniserver"] == par.GLOBAL]),
        "peut_modifier": access.peut_modifier_parametres(ms_name),
        "peut_modifier_global": access.peut_modifier_parametres(par.GLOBAL),
    }


def _corps() -> dict:
    return request.get_json(force=True, silent=True) or {}


@bp.route("/api/parametres/catalogue")
@login_required
def api_catalogue():
    return jsonify(par.catalogue())


@bp.route("/api/parametres")
@login_required
def api_parametres():
    ms_name = resolve_miniserver(request.args.get("miniserver"))
    with closing(read_conn()) as conn:
        return jsonify(_etat(conn, ms_name))


@bp.route("/api/parametres", methods=["POST"])
@login_required
def api_parametre_ajouter():
    """Ajoute une version (`cle`, `valid_from`, `valeur`, `note`) au site
    `miniserver`, ou à la valeur globale avec `global: true`. Même date =
    correction de la version existante."""
    corps = _corps()
    ms_name = resolve_miniserver(corps.get("miniserver"))
    cible = par.GLOBAL if corps.get("global") else ms_name
    if not access.peut_modifier_parametres(cible):
        abort(403, "valeurs globales réservées à l'administrateur")
    with closing(read_conn()) as conn:
        try:
            par.ajouter_version(conn, cible, str(corps.get("cle", "")), corps.get("valid_from"),
                                corps.get("valeur"), note=str(corps.get("note") or ""),
                                updated_by=current_user.username)
        except ValueError as exc:
            abort(400, str(exc))
        return jsonify(_etat(conn, ms_name))


@bp.route("/api/parametres/<int:parametre_id>", methods=["DELETE"])
@login_required
def api_parametre_supprimer(parametre_id: int):
    ms_name = resolve_miniserver(request.args.get("miniserver"))
    with closing(read_conn()) as conn:
        version = db.get_parametre(conn, parametre_id)
        # Une version d'un autre site répond comme une version inexistante.
        if version is None or version["miniserver"] not in (ms_name, par.GLOBAL):
            abort(404, "valeur introuvable pour ce site")
        if not access.peut_modifier_parametres(version["miniserver"]):
            abort(403, "valeurs globales réservées à l'administrateur")
        db.delete_parametre(conn, parametre_id)
        return jsonify(_etat(conn, ms_name))


def _frais(conn, ms_name: str) -> list[dict]:
    return [{**f, "tva": bool(f["tva"])} for f in db.list_frais(conn, ms_name)]


@bp.route("/api/frais")
@login_required
def api_frais():
    ms_name = resolve_miniserver(request.args.get("miniserver"))
    with closing(read_conn()) as conn:
        return jsonify(_frais(conn, ms_name))


@bp.route("/api/frais", methods=["POST"])
@login_required
def api_frais_ajouter():
    corps = _corps()
    ms_name = resolve_miniserver(corps.get("miniserver"))
    if not access.peut_modifier_parametres(ms_name):
        abort(403, "accès refusé")
    with closing(read_conn()) as conn:
        try:
            par.ajouter_frais(conn, ms_name, corps.get("du"), corps.get("au"),
                              str(corps.get("zone") or ""), str(corps.get("libelle") or ""),
                              corps.get("montant_ht"), corps.get("tva", True),
                              note=str(corps.get("note") or ""),
                              updated_by=current_user.username)
        except ValueError as exc:
            abort(400, str(exc))
        return jsonify(_frais(conn, ms_name))


@bp.route("/api/frais/<int:frais_id>", methods=["DELETE"])
@login_required
def api_frais_supprimer(frais_id: int):
    ms_name = resolve_miniserver(request.args.get("miniserver"))
    if not access.peut_modifier_parametres(ms_name):
        abort(403, "accès refusé")
    with closing(read_conn()) as conn:
        if not db.delete_frais(conn, ms_name, frais_id):
            abort(404, "frais introuvable pour ce site")
        return jsonify(_frais(conn, ms_name))
