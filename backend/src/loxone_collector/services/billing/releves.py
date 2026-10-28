"""
Consommation d'un compteur sur une période : index de fin − index de
début, avec ce qui permet d'en juger la fiabilité (ruptures, trous).
"""

from __future__ import annotations

from loxone_collector import db

# Une baisse d'un compteur cumulatif signale un reset / un remplacement de
# compteur, JAMAIS une consommation négative. Seuil en kWh : les micro-baisses
# de l'ordre de 1e-3 sont un simple écart d'arrondi entre le poller live
# (pleine précision) et l'historique Statistics importé (arrondi par Loxone),
# elles ne doivent pas déclencher d'alerte.
RUPTURE_MIN_DROP_KWH = 0.5


RUPTURE_MIN_DROP_EAU_M3 = 0.05


def min_drop_for_resource_type(resource_type: str | None) -> float:
    """Seuil de détection de rupture adapté à l'échelle du type de
    ressource : un compteur d'eau (m^3) a des incréments bien plus petits
    qu'un compteur d'énergie (kWh). Utilisé par la route générique /api/series/<id>/range (dashboard),
    qui sert aussi bien des séries eau que chauffage/énergie."""
    if resource_type and resource_type.startswith("eau_"):
        return RUPTURE_MIN_DROP_EAU_M3
    return RUPTURE_MIN_DROP_KWH


def reading_delta(conn, series_id: str, start_ts: int, end_ts: int,
                   now_ts: int, min_drop: float = RUPTURE_MIN_DROP_KWH,
                   statistics: bool = False) -> dict:
    """Consommation d'une série cumulative sur [start_ts, end_ts[ : relevé de
    fin - relevé de début, plus tout ce qui permet de juger sa fiabilité.

    `now_ts` sert uniquement à la détection des trous de collecte : sur un
    mois EN COURS, la borne de fin est dans le futur, et comparer le dernier
    relevé à cette borne signalerait à tort des semaines de données
    manquantes.

    `min_drop` est le seuil de détection d'une rupture de compteur, dans
    l'unité de la série : la valeur par défaut est calibrée pour des kWh, un
    compteur d'eau en m^3 demande un seuil plus fin
    (`min_drop_for_resource_type`).

    `statistics=True` : l'index vient des Statistics du Miniserver
    (`statistic_samples`), pas du collecteur. C'est le mode des décomptes.
    """
    if statistics:
        start = db.query_statistic_at(conn, series_id, start_ts)
        end = db.query_statistic_at(conn, series_id, end_ts - 1)
    else:
        start = db.query_value_at(conn, series_id, start_ts)
        end = db.query_value_at(conn, series_id, end_ts - 1)

    res = {
        "kwh": None,
        "releve_debut": None,
        "releve_fin": None,
        "releve_debut_ts": None,
        "releve_fin_ts": None,
        "alertes": [],
    }
    if start is None:
        res["alertes"].append("pas de relevé avant le début de la période")
    else:
        res["releve_debut_ts"], res["releve_debut"] = start
    if end is None:
        res["alertes"].append("pas de relevé dans la période")
    else:
        res["releve_fin_ts"], res["releve_fin"] = end

    if start is None or end is None:
        return res

    # Un relevé de début trop antérieur à la borne signale un trou de
    # collecte : la consommation calculée déborde alors sur le mois
    # précédent et est surestimée.
    if start_ts - start[0] > 2 * 86400:
        days = (start_ts - start[0]) // 86400
        res["alertes"].append(f"relevé de début vieux de {days} j (trou de collecte)")
    ref_end = min(end_ts - 1, now_ts)
    if ref_end - end[0] > 2 * 86400:
        days = (ref_end - end[0]) // 86400
        res["alertes"].append(f"relevé de fin vieux de {days} j (trou de collecte)")

    res["kwh"] = end[1] - start[1]

    ruptures = _detect_ruptures(conn, series_id, start_ts, end_ts, min_drop, statistics)
    if ruptures:
        res["ruptures"] = ruptures
        res["alertes"].append(
            f"{len(ruptures)} rupture(s) de compteur dans la période -> consommation non calculable"
        )
        res["kwh"] = None
    elif res["kwh"] < 0:
        res["alertes"].append("relevé de fin inférieur au relevé de début (compteur remis à zéro)")
        res["kwh"] = None
    return res


def delta_compteurs_successifs(conn, sources: list[dict], start_ts: int, end_ts: int,
                               now_ts: int, statistics: bool = False) -> dict:
    """Consommation quand plusieurs compteurs se relaient sur la période.

    Chaque compteur est lu jusqu'au premier relevé du suivant : les index
    ne sont pas comparables d'un compteur à l'autre (changement de
    « Production Solaire » vers « Solaire » à Sequoia), on additionne les
    écarts. Un seul compteur reste un relevé de fin moins relevé de début.
    """
    if len(sources) <= 1:
        src = sources[0] if sources else None
        if not src:
            return _absent()
        return reading_delta(conn, src["series_id"], start_ts, end_ts, now_ts,
                             statistics=statistics)

    bornes = []
    for src in sources:
        if statistics:
            row = conn.execute(
                "SELECT MIN(ts) FROM statistic_samples "
                "WHERE series_id = ? AND ts >= ? AND ts < ?",
                (src["series_id"], start_ts, end_ts),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT MIN(ts) FROM readings_hourly "
                "WHERE series_id = ? AND ts >= ? AND ts < ?",
                (src["series_id"], start_ts, end_ts),
            ).fetchone()
        if row and row[0] is not None:
            bornes.append((int(row[0]), src))
    bornes.sort(key=lambda item: item[0])
    if len(bornes) <= 1:
        src = bornes[0][1] if bornes else (sources[0] if sources else None)
        if not src:
            return _absent()
        return reading_delta(conn, src["series_id"], start_ts, end_ts, now_ts,
                             statistics=statistics)

    total = 0.0
    got = False
    alertes = []
    debut_ts = debut = fin_ts = fin = None
    for i, (first, src) in enumerate(bornes):
        seg_start = start_ts if i == 0 else first
        seg_end = bornes[i + 1][0] if i + 1 < len(bornes) else end_ts
        if seg_start >= seg_end:
            continue
        d = reading_delta(conn, src["series_id"], seg_start, seg_end, now_ts,
                          statistics=statistics)
        if i == 0:
            debut_ts, debut = d["releve_debut_ts"], d["releve_debut"]
        if d["releve_fin_ts"] is not None:
            fin_ts, fin = d["releve_fin_ts"], d["releve_fin"]
        if d["kwh"] is not None:
            total += d["kwh"]
            got = True
        alertes.extend(d["alertes"])
    return {
        "kwh": total if got else None,
        "releve_debut": debut,
        "releve_fin": fin,
        "releve_debut_ts": debut_ts,
        "releve_fin_ts": fin_ts,
        "alertes": alertes,
    }


def _detect_ruptures(conn, series_id: str, start_ts: int, end_ts: int,
                     min_drop: float = RUPTURE_MIN_DROP_KWH,
                     statistics: bool = False) -> list[dict]:
    """Jours où le compteur cumulatif a BAISSÉ de plus de `min_drop` (par
    défaut RUPTURE_MIN_DROP_KWH) : reset, remplacement de compteur, ou
    dépassement de capacité. Un décompte calculé à cheval sur un tel jour est
    faux, donc on préfère ne rien afficher plutôt qu'un chiffre plausible
    mais faux."""
    if statistics:
        rows = conn.execute(
            """
            SELECT CAST(ts / 86400 AS INTEGER) * 86400 AS day, MAX(value)
              FROM statistic_samples
             WHERE series_id = ? AND ts BETWEEN ? AND ?
             GROUP BY day ORDER BY day
            """,
            (series_id, start_ts, end_ts - 1),
        ).fetchall()
    else:
        rows = db.query_daily_last(conn, series_id, start_ts, end_ts - 1)
    out = []
    for i in range(1, len(rows)):
        prev_value = rows[i - 1][1]
        day_ts, value = rows[i]
        if prev_value - value > min_drop:
            out.append({"date_ts": day_ts, "avant": prev_value, "apres": value})
    return out


def _absent() -> dict:
    return {
        "kwh": None, "releve_debut": None, "releve_fin": None,
        "releve_debut_ts": None, "releve_fin_ts": None,
        "alertes": ["aucune série trouvée pour cette colonne"],
    }
