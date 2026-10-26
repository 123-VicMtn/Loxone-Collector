"""
Lecture des Statistics Loxone pour les décomptes.

Le poller ne voit que le live pendant qu'il tourne. Les Statistics, elles,
sont enregistrées sur la carte SD du Miniserver même s'il est arrêté. Un
décompte (page, export) se calcule sur ces index, jamais sur `readings`.
"""
from __future__ import annotations

import json
import logging
import struct

import requests

import billing
import db
from loxone_client import LoxoneAuthError, LoxoneClient, LoxoneError

logger = logging.getLogger("miniserver_statistics")

# Un relevé de moins d'une heure avant « maintenant » (ou avant la fin de
# la période demandée) est assez frais : on ne rappelle pas le Miniserver.
FRAICHEUR_S = 3600


def find_stat_mapping(control: dict, state_name: str) -> tuple[str, str] | None:
    """(group_id, output) si ce state a un dataPoint statisticV2.

    Le champ est à la racine du contrôle, pas sous details. Repli sur
    details au cas où un autre firmware le rangerait là.
    """
    stat = control.get("statisticV2") or control.get("statistic") or {}
    if not stat:
        details = control.get("details") or {}
        stat = details.get("statisticV2") or details.get("statistic") or {}
    for group in stat.get("groups", []):
        for dp in group.get("dataPoints", []):
            if dp.get("output") == state_name:
                return str(group.get("id")), state_name
    return None


def get_statistic_info(client: LoxoneClient, control_uuid: str) -> dict[str, int]:
    """{group_id: activeSince} pour un contrôle."""
    resp = client._get(f"/jdev/sps/getStatisticInfo/{control_uuid}")
    try:
        payload = resp.json()
        raw_value = payload.get("LL", {}).get("value")
        entries = json.loads(raw_value) if isinstance(raw_value, str) else (raw_value or [])
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}
    result = {}
    for entry in entries:
        gid = entry.get("id")
        active_since = entry.get("activeSince")
        if gid is not None and active_since is not None:
            result[str(gid)] = int(active_since)
    return result


def fetch_raw_history(client: LoxoneClient, control_uuid: str, group_id: str,
                      output_name: str, from_ts: int, to_ts: int) -> list[tuple[int, float]]:
    """Historique brut d'un output : liste de (ts Unix UTC, valeur)."""
    path = (f"/jdev/sps/getStatistic/{control_uuid}/raw/{from_ts}/{to_ts}/"
            f"all/{group_id}/{output_name}")
    try:
        resp = client._session.get(
            f"{client.base_url}{path}", auth=client._auth, timeout=60,
            verify=client.verify_ssl if client.scheme == "https" else True,
        )
    except requests.RequestException as exc:
        raise LoxoneError(f"échec de connexion pour {path}: {exc}") from exc
    if resp.status_code != 200:
        raise LoxoneError(f"HTTP {resp.status_code} pour {path}")
    body = resp.content
    record_size = 12  # Uint32 ts + Float64, un seul output
    if len(body) % record_size != 0:
        raise LoxoneError(
            f"Taille de réponse inattendue ({len(body)} octets, pas un "
            f"multiple de {record_size}) pour {path}"
        )
    records = []
    for i in range(len(body) // record_size):
        chunk = body[i * record_size:(i + 1) * record_size]
        ts = struct.unpack_from("<I", chunk, 0)[0]
        value = struct.unpack_from("<d", chunk, 4)[0]
        records.append((ts, value))
    return records


def bucket_hourly(records: list[tuple[int, float]]) -> list[tuple[int, float, float, float, int]]:
    """(bucket_ts, avg, min, max, n) par heure. Sert au backfill des
    courbes du dashboard, pas au décompte."""
    buckets: dict[int, list[float]] = {}
    for ts, value in records:
        buckets.setdefault((ts // 3600) * 3600, []).append(value)
    return [
        (bucket, sum(values) / len(values), min(values), max(values), len(values))
        for bucket, values in buckets.items()
    ]


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

    client = LoxoneClient(
        name=ms.name, host=ms.host, username=ms.username, password=ms.password,
        port=ms.port, scheme=ms.scheme, verify_ssl=ms.verify_ssl, timeout=30,
    )
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
