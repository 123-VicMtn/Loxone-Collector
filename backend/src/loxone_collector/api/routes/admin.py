"""Classification manuelle des séries (page /admin, admin seulement)."""

from __future__ import annotations

import logging
from contextlib import closing

from flask import Blueprint, abort, jsonify, request

from loxone_collector import db
from loxone_collector.api.access import (
    admin_required,
    read_conn,
)

logger = logging.getLogger("app")

bp = Blueprint("admin", __name__)


@bp.route("/api/series/<path:series_id>/classify", methods=["POST"])
@admin_required
def api_classify(series_id: str):
    payload = request.get_json(force=True, silent=True) or {}

    with closing(read_conn()) as conn:
        if payload.get("reset"):
            db.reset_series_classification(conn, series_id)
        else:
            apartment = payload.get("apartment")
            resource_type = payload.get("resource_type")
            if apartment is None and resource_type is None:
                abort(400, "apartment et/ou resource_type (ou reset:true) requis")
            db.set_series_classification(conn, series_id, apartment=apartment, resource_type=resource_type)

    return jsonify({"ok": True})

