"""
Lecture de l'historique Statistics d'un Miniserver Loxone.

C'est l'historique que le Miniserver enregistre lui-même (carte SD), pas
les mesures écrites par le poller. Seul module qui parle ce protocole :
le graphique de puissance de l'aperçu (`points_by_series`), le décompte
(`services/statistics_sync.py`) et le backfill
(`scripts/backfill_statistics.py`) passent tous par ici.
"""
from __future__ import annotations

import json
import struct
from concurrent.futures import ThreadPoolExecutor

import requests

from loxone_collector.loxone.client import LoxoneAuthError, LoxoneClient, LoxoneError

RECORD_SIZE = 12  # Uint32 timestamp + Float64, un seul output demandé


class StatisticUnavailable(LoxoneError):
    """Cet output n'a pas d'historique Statistics exploitable."""


def find_stat_mapping(control: dict, state_name: str) -> tuple[str, str] | None:
    """Retourne (group_id, output_name) si ce state a un dataPoint
    statisticV2, sinon None.

    Le champ est à la racine du contrôle (`control["statisticV2"]`), pas
    sous `details` — confirmé sur MS-Arlopi. Le repli sur `details` reste
    pour un firmware qui rangerait le bloc autrement.
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


def decode_statistic_body(body: bytes, chemin: str | None = None) -> list[tuple[int, float]]:
    """Décode la réponse binaire de getStatistic/.../raw/... pour un seul
    output : suites (timestamp Unix UTC, valeur). `chemin` complète le
    message d'erreur."""
    if len(body) % RECORD_SIZE != 0:
        raise LoxoneError(
            f"Taille de réponse inattendue ({len(body)} octets, pas un "
            f"multiple de {RECORD_SIZE})" + (f" pour {chemin}" if chemin else "")
        )
    records = []
    for offset in range(0, len(body), RECORD_SIZE):
        ts = struct.unpack_from("<I", body, offset)[0]
        value = struct.unpack_from("<d", body, offset + 4)[0]
        records.append((ts, value))
    return records


def statistic_path(control_uuid: str, group_id: str, output_name: str,
                   from_ts: int, to_ts: int) -> str:
    return (f"/jdev/sps/getStatistic/{control_uuid}/raw/{from_ts}/{to_ts}/"
            f"all/{group_id}/{output_name}")


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
    """Historique brut d'un output, via la session du client (appels
    séquentiels : décompte, backfill).

    Une coupure réseau lève une LoxoneError dont le texte contient « échec
    de connexion » : `sync_decompte` s'en sert pour abandonner le site
    plutôt que la seule série. Un HTTP autre que 200, refus d'auth compris,
    reste une LoxoneError simple : la série est seulement sautée."""
    path = statistic_path(control_uuid, group_id, output_name, from_ts, to_ts)
    try:
        resp = client._session.get(
            f"{client.base_url}{path}", auth=client._auth, timeout=60,
            verify=client.verify_ssl if client.scheme == "https" else True,
        )
    except requests.RequestException as exc:
        raise LoxoneError(f"échec de connexion pour {path}: {exc}") from exc
    if resp.status_code != 200:
        raise LoxoneError(f"HTTP {resp.status_code} pour {path}")
    return decode_statistic_body(resp.content, path)


def _fetch_once(client: LoxoneClient, control_uuid: str, group_id: str,
                output_name: str, from_ts: int, to_ts: int) -> list[tuple[int, float]]:
    """Un GET indépendant (sans la session partagée) pour pouvoir en
    lancer plusieurs en parallèle."""
    path = statistic_path(control_uuid, group_id, output_name, from_ts, to_ts)
    url = f"{client.base_url}{path}"
    try:
        resp = requests.get(
            url, auth=client._auth, timeout=30,
            verify=client.verify_ssl if client.scheme == "https" else True,
        )
    except requests.RequestException as exc:
        raise LoxoneError(f"échec de connexion à {url}: {exc}") from exc
    if resp.status_code == 401:
        raise LoxoneAuthError(f"authentification refusée pour {path}")
    if resp.status_code != 200:
        raise StatisticUnavailable(f"HTTP {resp.status_code} pour {path}")
    return decode_statistic_body(resp.content)


def align_hourly(records: list[tuple[int, float]], from_ts: int,
                 to_ts: int) -> list[tuple[int, float]]:
    """Une valeur par heure : la moyenne des échantillons du Miniserver
    tombés dans l'heure, horodatée au début de l'heure. Les compteurs d'un
    même immeuble se retrouvent ainsi sur les mêmes instants, et une
    puissance ne s'additionne pas à elle-même quand le Miniserver a
    enregistré plusieurs points dans l'heure."""
    buckets: dict[int, list[float]] = {}
    for ts, value in records:
        if ts < from_ts or ts > to_ts:
            continue
        bucket = (ts // 3600) * 3600
        buckets.setdefault(bucket, []).append(value)
    return [
        (bucket, sum(values) / len(values))
        for bucket, values in sorted(buckets.items())
    ]


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


def points_by_series(client: LoxoneClient, structure: dict, rows: list[dict],
                     from_ts: int, to_ts: int, workers: int = 6
                     ) -> tuple[dict[str, list[dict]], list[str]]:
    """Puissance Statistics de chaque série `actual`.

    Retourne (points par series_id, libellés sans historique). Une coupure
    de connexion ou un refus d'auth remonte : on n'affiche pas une somme
    partielle comme si l'immeuble avait peu consommé. Un output sans
    Statistics (HTTP autre que 200) est listé à part, sa série reste vide.
    """
    controls = structure.get("controls") or {}
    points: dict[str, list[dict]] = {}
    missing: list[str] = []
    jobs = []
    for row in rows:
        control = controls.get(row["control_uuid"]) or {}
        mapping = find_stat_mapping(control, row["state_name"])
        if mapping is None:
            points[row["series_id"]] = []
            missing.append(row["label"])
            continue
        group_id, output_name = mapping
        jobs.append((row, group_id, output_name))

    def run(job):
        row, group_id, output_name = job
        try:
            records = _fetch_once(
                client, row["control_uuid"], group_id, output_name, from_ts, to_ts,
            )
        except StatisticUnavailable:
            return row, None
        return row, align_hourly(records, from_ts, to_ts)

    if not jobs:
        return points, missing

    pool_size = max(1, min(workers, len(jobs)))
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        for row, hourly in pool.map(run, jobs):
            if hourly is None:
                points[row["series_id"]] = []
                missing.append(row["label"])
                continue
            points[row["series_id"]] = [
                {"ts": ts, "value": value} for ts, value in hourly
            ]
    return points, missing
