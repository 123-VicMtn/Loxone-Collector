"""
Contrôle d'accès commun aux routes : configuration et base de l'application
en cours, sites visibles par le compte connecté, rôle admin, accès à une
série. Et l'heure des routes (`maintenant`), en un seul point.
"""

from __future__ import annotations

import time
from functools import wraps

from flask import abort, current_app, jsonify
from flask_login import current_user

from loxone_collector import db
from loxone_collector.api import auth
from loxone_collector.config import AppConfig


def maintenant() -> int:
    """Heure courante (epoch) utilisée par les routes. Un seul point, que
    les tests figent."""
    return int(time.time())


def current_cfg() -> AppConfig:
    return current_app.config["LOXONE_CFG"]


def read_conn():
    return db.get_connection(current_cfg().db_path)


def allowed_miniservers() -> list[str]:
    """Sites que le compte courant peut voir -- tous les sites configurés
    pour un 'admin', uniquement les siens pour un 'user' (voir CLAUDE.md,
    "Rôles utilisateurs"). Filtré par les sites RÉELLEMENT configurés :
    un site accordé à un 'user' puis retiré de config.yaml ne doit pas
    ressusciter côté accès."""
    configured = [ms.name for ms in current_cfg().miniservers]
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


def check_series_access(conn, series_id: str) -> None:
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


def resolve_miniserver(name: str | None) -> str:
    """Valide (ou choisit par défaut) le site sur lequel scoper un appel
    /api/decompte ou /api/tarifs. Un décompte n'a de sens que rattaché à UN
    site physique (miniserver) : mélanger les zones de deux immeubles dans
    un même calcul fausserait consommations ET montants facturés -- voir
    CLAUDE.md, "Décompte de charges".

    Scopé par allowed_miniservers() (pas tous les sites configurés) : un
    compte 'user' ne doit jamais pouvoir calculer le décompte d'un site qui
    ne lui a pas été accordé, même en devinant/forçant le paramètre
    `miniserver` dans l'URL -- 403 (site existant mais pas autorisé),
    distinct du 400 (site qui n'existe pas du tout)."""
    configured = [ms.name for ms in current_cfg().miniservers]
    allowed = allowed_miniservers()
    if name is None:
        if not allowed:
            abort(403, "aucun site accessible pour ce compte")
        return allowed[0]
    if name not in configured:
        abort(400, f"miniserver invalide: {name!r}. Valeurs possibles: {configured}")
    if name not in allowed:
        abort(403, f"accès refusé au site {name!r}")
    return name

