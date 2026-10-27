"""
Lecture des Statistics Loxone pour les décomptes.

Le poller ne voit que le live pendant qu'il tourne. Les Statistics, elles,
sont enregistrées sur la carte SD du Miniserver même s'il est arrêté. Un
décompte (page, export) se calcule sur ces index, jamais sur `readings`.
Ici : quelles séries importer, et quand. Le protocole (requêtes, décodage)
est dans loxone/statistics.py.
"""
from __future__ import annotations

import logging

from loxone_collector import db
from loxone_collector.loxone.client import LoxoneAuthError, LoxoneError, client_for
from loxone_collector.loxone.statistics import fetch_raw_history, find_stat_mapping, get_statistic_info
from loxone_collector.services import billing

logger = logging.getLogger("miniserver_statistics")

# Un relevé de moins d'une heure avant « maintenant » (ou avant la fin de
# la période demandée) est assez frais : on ne rappelle pas le Miniserver.
FRAICHEUR_S = 3600


def series_du_decompte(series: list[dict], postes: bool = False) -> list[dict]:
    """Compteurs cumulatifs dont le décompte web a besoin : zones, bâtiment,
    et les candidats de production (pour choisir celui qui couvre le mois).
    `postes` : aussi le chauffage, l'eau chaude et la buanderie (export)."""
    zones = billing.resolve_zones(series)
    batiment = billing.resolve_batiment(series)
    ids: set[str] = set()
    for z in zones:
        for src in z["sources"].values():
            if src:
                ids.add(src["series_id"])
    if postes:
        for z in billing.resolve_postes(series):
            for srcs in z["compteurs"].values():
                ids.update(src["series_id"] for src in srcs)
    for src in batiment.values():
        if src:
            ids.add(src["series_id"])
    for s in series:
        if (s.get("apartment") or "").strip():
            continue
        if s.get("state_name") != "total":
            continue
        label = s.get("label") or ""
        if billing.SOLAIRE_BATIMENT_RE.search(label) or billing.BATIMENT_PRODUCTION_RE.search(label):
            ids.add(s["series_id"])
    by_id = {s["series_id"]: s for s in series}
    return [by_id[i] for i in sorted(ids) if i in by_id]


def reprendre_statistiques_archivees(conn, series_id: str) -> int:
    """Recopie des Statistics déjà importées quand le contrôle n'est plus
    dans la structure du Miniserver (compteur retiré, ex. « Production
    Solaire » à Sequoia après le 9 septembre 2026).

    Seules les heures qui sont un relevé unique (avg = max, un échantillon)
    sont reprises : c'est la forme écrite par l'import Statistics, pas une
    moyenne du collecteur."""
    if db.statistic_max_ts(conn, series_id) is not None:
        return 0
    rows = [
        (series_id, ts, value)
        for ts, value in conn.execute(
            """
            SELECT ts, max_value FROM readings_hourly
             WHERE series_id = ? AND sample_count = 1
               AND max_value IS NOT NULL AND avg_value = max_value
            """,
            (series_id,),
        )
    ]
    return db.upsert_statistic_samples(conn, rows)


def _a_rafraichir(conn, series_id: str, start_ts: int | None, end_ts: int | None,
                  now_ts: int) -> bool:
    """Faut-il rappeler le Miniserver pour cette série ?"""
    if (db.statistic_unavailable_fresh(conn, series_id, now_ts)
            and db.statistic_max_ts(conn, series_id) is not None):
        return False
    last = db.statistic_max_ts(conn, series_id)
    horizon = min(end_ts, now_ts) if end_ts else now_ts
    if last is None or last < horizon - FRAICHEUR_S:
        return True
    if start_ts is not None and db.query_statistic_at(conn, series_id, start_ts) is None:
        return True
    return False


def sync_decompte(cfg, conn, ms_name: str, series: list[dict], now_ts: int,
                  start_ts: int | None = None, end_ts: int | None = None,
                  toutes_les_series: bool = False, postes: bool = False) -> dict:
    """Importe les index Statistics des compteurs du décompte.

    `toutes_les_series` : tous les total/totalNeg du site (export CLI, qui
    couvre aussi l'eau et le chauffage). `postes` : séries du décompte
    électrique plus chauffage, eau chaude et buanderie (export web). Sinon,
    seulement les séries du décompte électrique.

    Ne lit pas `readings`. Si le Miniserver ne répond pas, `ok` est False
    et les échantillons déjà en base restent utilisables.
    """
    if toutes_les_series:
        targets = [
            s for s in series
            if s.get("state_name") in ("total", "totalNeg")
        ]
    else:
        targets = series_du_decompte(series, postes=postes)
    besoin = [
        s for s in targets
        if _a_rafraichir(conn, s["series_id"], start_ts, end_ts, now_ts)
    ]
    if not besoin:
        return {"ok": True, "erreur": None, "series": 0, "points": 0}

    ms = next((m for m in cfg.miniservers if m.name == ms_name), None)
    if ms is None:
        return {"ok": False, "erreur": f"miniserver inconnu: {ms_name}",
                "series": 0, "points": 0}

    client = client_for(ms, timeout=30)
    points = 0
    fetched = 0
    sans_stats: list[str] = []
    try:
        structure = client.fetch_structure()
        controls = structure.get("controls", {}) or {}
        info_cache: dict[str, dict[str, int]] = {}
        for s in besoin:
            control_uuid = s.get("control_uuid") or ""
            label = s.get("label") or s["series_id"]
            mapping = find_stat_mapping(controls.get(control_uuid, {}), s.get("state_name") or "")
            if mapping is None:
                n = reprendre_statistiques_archivees(conn, s["series_id"])
                if n:
                    logger.info("Statistics archivées %s : %s point(s)", label, n)
                else:
                    sans_stats.append(label)
                db.mark_statistic_sync(conn, s["series_id"], now_ts, False)
                continue
            group_id, output_name = mapping
            if control_uuid not in info_cache:
                info_cache[control_uuid] = get_statistic_info(client, control_uuid)
            active_since = info_cache[control_uuid].get(group_id)
            if active_since is None:
                db.mark_statistic_sync(conn, s["series_id"], now_ts, False)
                sans_stats.append(label)
                continue

            last = db.statistic_max_ts(conn, s["series_id"])
            ouverture_manquante = (
                start_ts is not None
                and db.query_statistic_at(conn, s["series_id"], start_ts) is None
            )
            if last is None or ouverture_manquante:
                from_ts = active_since
            else:
                from_ts = max(active_since, last - 3600)
            to_ts = max(now_ts, end_ts or now_ts)
            if from_ts >= to_ts:
                continue
            try:
                records = fetch_raw_history(
                    client, control_uuid, group_id, output_name, from_ts, to_ts,
                )
            except LoxoneAuthError:
                raise
            except LoxoneError as exc:
                if "échec de connexion" in str(exc):
                    raise
                logger.warning("Statistics %s : %s", label, exc)
                sans_stats.append(label)
                continue
            if records:
                db.upsert_statistic_samples(
                    conn, [(s["series_id"], ts, value) for ts, value in records],
                )
                points += len(records)
            db.mark_statistic_sync(conn, s["series_id"], now_ts, True)
            fetched += 1
            logger.info("Statistics %s : %s point(s)", label, len(records))
        conn.commit()
    except (LoxoneAuthError, LoxoneError) as exc:
        conn.commit()
        logger.warning("Statistics %s injoignables : %s", ms_name, exc)
        return {"ok": False, "erreur": str(exc), "series": fetched, "points": points}
    finally:
        client.close()

    if sans_stats:
        logger.info("Sans Statistics (%s) : %s", ms_name, ", ".join(sans_stats))
    return {"ok": True, "erreur": None, "series": fetched, "points": points}
