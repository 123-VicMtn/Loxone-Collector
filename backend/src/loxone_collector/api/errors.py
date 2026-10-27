"""
Erreurs de l'API en JSON, `{"error": "<message>"}`, avec le code HTTP.

Sans ces gestionnaires, un `abort(400, ...)` renvoyait la page d'erreur HTML
de Flask alors que le frontend attend du JSON. Le message d'un `abort` est
écrit pour l'utilisateur ; les erreurs que Flask lève lui-même (route
inconnue, méthode non permise) reçoivent un message en français. Une
exception imprévue n'expose jamais son détail : il part dans le log, la
réponse dit seulement qu'une erreur interne est survenue.
"""

from __future__ import annotations

import logging

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

logger = logging.getLogger("app")

MESSAGES_PAR_DEFAUT = {
    400: "requête invalide",
    403: "accès refusé",
    404: "ressource introuvable",
    405: "méthode non autorisée",
    413: "requête trop volumineuse",
}
MESSAGE_ERREUR_INTERNE = "erreur interne, réessayez plus tard"


def _http(exc: HTTPException):
    # Description par défaut de werkzeug (texte anglais générique) : on la
    # remplace. Celle passée à `abort` est gardée telle quelle.
    if exc.description == type(exc).description:
        message = MESSAGES_PAR_DEFAUT.get(exc.code, exc.name.lower())
    else:
        message = exc.description
    return jsonify({"error": message}), exc.code


def _imprevue(exc: Exception):
    logger.exception("Erreur non gérée : %s", exc)
    return jsonify({"error": MESSAGE_ERREUR_INTERNE}), 500


def register_error_handlers(app: Flask) -> None:
    app.register_error_handler(HTTPException, _http)
    app.register_error_handler(Exception, _imprevue)
