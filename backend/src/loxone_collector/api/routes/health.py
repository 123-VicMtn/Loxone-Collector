"""État de la collecte : /health (public, supervision) et /api/collecte."""

from __future__ import annotations

import logging
from contextlib import closing

from flask import Blueprint, jsonify
from flask_login import login_required

from loxone_collector import db
from loxone_collector.api import access
from loxone_collector.api.access import (
    allowed_miniservers,
    current_cfg,
    read_conn,
)

logger = logging.getLogger("app")

bp = Blueprint("health", __name__)


def _etat_collecte(sites: list[str], now: int) -> list[dict]:
    """Dernier relevé de chaque site, lu en base (pas dans `poller.state`, vidé à
    chaque redémarrage), et retard au-delà de `stale_alert_minutes`.
    Jamais le message d'erreur du poll : il est dans le log du serveur."""
    seuil_s = current_cfg().stale_alert_minutes * 60
    with closing(read_conn()) as conn:
        derniers = db.last_reading_by_miniserver(conn)
    out = []
    for name in sites:
        dernier = derniers.get(name)
        etat = {
            "miniserver": name,
            "dernier_releve_ts": dernier,
            "en_retard": dernier is None or now - dernier > seuil_s,
        }
        out.append(etat)
    return out


@bp.route("/health")
def health():
    """Public, pour une supervision externe et le healthcheck Docker :
    `status` seul, qui passe à "degrade" dès qu'un site ne remonte plus
    (voir _etat_collecte). La réponse reste un 200 : l'API, elle,
    fonctionne. Ni noms de sites ni état des polls : le détail est dans
    /api/collecte (connecté, sites du compte), les erreurs dans le log."""
    collecte = _etat_collecte([ms.name for ms in current_cfg().miniservers], access.maintenant())
    return jsonify({"status": "degrade" if any(s["en_retard"] for s in collecte) else "ok"})


@bp.route("/api/collecte")
@login_required
def api_collecte():
    """État de la collecte des sites accessibles au compte (bandeau du
    dashboard) : dernier relevé et retard."""
    return jsonify({
        "seuil_minutes": current_cfg().stale_alert_minutes,
        "sites": _etat_collecte(allowed_miniservers(), access.maintenant()),
    })

