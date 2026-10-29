"""Connexion, déconnexion et session en cours (cookie Flask-Login)."""

from __future__ import annotations

import logging

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user, login_required, login_user, logout_user

from loxone_collector.api import auth, tentatives
from loxone_collector.api.access import (
    allowed_miniservers,
)

logger = logging.getLogger("app")

bp = Blueprint("auth", __name__)


def _user_payload() -> dict:
    """Forme renvoyée par /api/login et /api/me -- `miniservers` est déjà
    résolu via allowed_miniservers() (tous les sites configurés pour un
    admin, pas la liste brute de user_miniservers) : le frontend n'a pas à
    connaître la distinction admin/user pour savoir quels sites afficher,
    juste à lire ce champ."""
    return {
        "username": current_user.username,
        "role": current_user.role,
        "miniservers": allowed_miniservers(),
    }


@bp.route("/api/login", methods=["POST"])
def api_login():
    """Connexion par cookie de session (Flask-Login). Le frontend Vue
    affiche le formulaire ; ici on ne renvoie que du JSON, jamais de
    redirection -- ce n'est pas une route de page."""
    payload = request.get_json(force=True, silent=True) or {}
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    if not username or not password:
        abort(400, "username et password requis")

    cles = tentatives.cles(request.remote_addr, username)
    attente = tentatives.limiteur.attente(cles)
    if attente:
        logger.warning("Connexion refusée (trop de tentatives) : %r depuis %s", username,
                       request.remote_addr)
        res = jsonify({"error": "trop de tentatives, réessayez dans quelques minutes"})
        return res, 429, {"Retry-After": str(attente)}

    user = auth.verify_login(username, password)
    if user is None:
        tentatives.limiteur.echec(cles)
        logger.warning("Échec de connexion : %r depuis %s", username, request.remote_addr)
        return jsonify({"error": "identifiants invalides"}), 401

    # Seul le compteur de l'identifiant repart à zéro : se connecter à son
    # propre compte n'efface pas les échecs de cette IP sur d'autres comptes.
    tentatives.limiteur.reussite([("identifiant", username.lower())])
    # Session permanente : cookie valable `app.SESSION_DUREE`, au lieu de
    # tomber à la fermeture du navigateur. Pas de cookie « remember me »
    # de Flask-Login (un an par défaut).
    session.permanent = True
    login_user(user)
    return jsonify(_user_payload())


@bp.route("/api/logout", methods=["POST"])
@login_required
def api_logout():
    logout_user()
    return jsonify({"ok": True})


@bp.route("/api/me")
def api_me():
    """Session en cours, ou 401 -- interrogé par le frontend au chargement
    pour savoir s'il doit afficher la page de connexion. Volontairement SANS
    @login_required : un 401 ici est une réponse normale (pas connecté),
    pas une erreur d'accès à signaler comme les autres routes protégées."""
    if not current_user.is_authenticated:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(_user_payload())

