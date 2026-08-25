"""
db.py
-----
Couche SQLite pour stocker les séries temporelles des valeurs Loxone.

Choix pensés pour un Raspberry Pi 4B (2 Go RAM) + carte SD 32 Go :
  - SQLite en mode WAL + synchronous=NORMAL : moins de fsync donc moins
    d'écritures physiques sur la carte SD qu'en mode journal par défaut.
  - Table `readings` : données brutes, conservées `RAW_RETENTION_DAYS` jours
    par défaut (voir config).
  - Table `readings_hourly` : moyennes horaires, produites par
    `downsample_and_prune()` à partir des données brutes trop anciennes, qui
    sont ensuite supprimées. Cela borne durablement la taille de la base
    tout en gardant un historique long terme (utile pour les décomptes de
    charges annuels).
  - `checkpoint_wal()` à appeler périodiquement pour éviter que le fichier
    -wal ne grossisse indéfiniment.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS series_meta (
    series_id     TEXT PRIMARY KEY,
    miniserver    TEXT NOT NULL,
    control_uuid  TEXT NOT NULL,
    state_name    TEXT NOT NULL,
    label         TEXT NOT NULL,
    room          TEXT,
    category      TEXT,
    control_type  TEXT,
    unit          TEXT,
    updated_at    INTEGER
);

CREATE TABLE IF NOT EXISTS readings (
    series_id  TEXT NOT NULL,
    ts         INTEGER NOT NULL,
    value      REAL,
    value_text TEXT,
    PRIMARY KEY (series_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_readings_series_ts ON readings (series_id, ts);

CREATE TABLE IF NOT EXISTS readings_hourly (
    series_id  TEXT NOT NULL,
    ts         INTEGER NOT NULL,  -- début de l'heure (epoch, UTC)
    avg_value  REAL,
    min_value  REAL,
    max_value  REAL,
    sample_count INTEGER,
    PRIMARY KEY (series_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_readings_hourly_series_ts ON readings_hourly (series_id, ts);
"""


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    with closing(conn.cursor()) as cur:
        cur.executescript(SCHEMA)
    conn.commit()
    return conn


def upsert_series_meta(conn: sqlite3.Connection, series_id: str, miniserver: str,
                        control_uuid: str, state_name: str, label: str,
                        room: str = "", category: str = "", control_type: str = "",
                        unit: str = "") -> None:
    conn.execute(
        """
        INSERT INTO series_meta
            (series_id, miniserver, control_uuid, state_name, label, room,
             category, control_type, unit, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(series_id) DO UPDATE SET
            miniserver=excluded.miniserver,
            control_uuid=excluded.control_uuid,
            state_name=excluded.state_name,
            label=excluded.label,
            room=excluded.room,
            category=excluded.category,
            control_type=excluded.control_type,
            unit=excluded.unit,
            updated_at=excluded.updated_at
        """,
        (series_id, miniserver, control_uuid, state_name, label, room,
         category, control_type, unit, int(time.time())),
    )


def insert_readings_batch(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    """rows: liste de (series_id, ts, value_float_or_None, value_text_or_None)"""
    if not rows:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO readings (series_id, ts, value, value_text) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )


def list_series(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT series_id, miniserver, control_uuid, state_name, label, room, "
        "category, control_type, unit FROM series_meta ORDER BY room, label"
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def query_readings(conn: sqlite3.Connection, series_id: str, start_ts: int,
                    end_ts: int) -> list[tuple[int, float | None]]:
    """Retourne les points (ts, value) pour une série sur une plage de temps,
    en combinant automatiquement les données brutes récentes et les moyennes
    horaires archivées (transparent pour l'appelant)."""
    cur = conn.execute(
        """
        SELECT ts, value FROM readings
         WHERE series_id = ? AND ts BETWEEN ? AND ? AND value IS NOT NULL
        UNION ALL
        SELECT ts, avg_value FROM readings_hourly
         WHERE series_id = ? AND ts BETWEEN ? AND ?
        ORDER BY ts ASC
        """,
        (series_id, start_ts, end_ts, series_id, start_ts, end_ts),
    )
    return cur.fetchall()


def downsample_and_prune(conn: sqlite3.Connection, raw_retention_days: int = 30) -> int:
    """Agrège en moyennes horaires les données brutes plus vieilles que
    `raw_retention_days`, les insère dans `readings_hourly`, puis supprime
    les lignes brutes correspondantes. Retourne le nombre de lignes brutes
    supprimées. À appeler périodiquement (ex: une fois par jour)."""
    cutoff = int(time.time()) - raw_retention_days * 86400

    cur = conn.execute(
        """
        SELECT series_id, CAST(ts / 3600 AS INTEGER) * 3600 AS bucket,
               AVG(value), MIN(value), MAX(value), COUNT(*)
          FROM readings
         WHERE ts < ? AND value IS NOT NULL
         GROUP BY series_id, bucket
        """,
        (cutoff,),
    )
    buckets = cur.fetchall()
    if buckets:
        conn.executemany(
            """
            INSERT INTO readings_hourly (series_id, ts, avg_value, min_value, max_value, sample_count)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(series_id, ts) DO UPDATE SET
                avg_value=excluded.avg_value,
                min_value=excluded.min_value,
                max_value=excluded.max_value,
                sample_count=excluded.sample_count
            """,
            buckets,
        )

    deleted = conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff,)).rowcount
    conn.commit()
    return deleted


def prune_hourly(conn: sqlite3.Connection, hourly_retention_days: int) -> int:
    """Supprime les moyennes horaires plus vieilles que hourly_retention_days.
    Laisser hourly_retention_days=0 (défaut recommandé) pour conserver
    l'historique horaire indéfiniment (utile pour les décomptes de charges
    annuels) — la table agrégée grossit très lentement."""
    if not hourly_retention_days:
        return 0
    cutoff = int(time.time()) - hourly_retention_days * 86400
    deleted = conn.execute("DELETE FROM readings_hourly WHERE ts < ?", (cutoff,)).rowcount
    conn.commit()
    return deleted


def checkpoint_wal(conn: sqlite3.Connection) -> None:
    """Force l'écriture du journal WAL dans le fichier principal et le
    tronque, pour éviter que loxone.db-wal ne grossisse indéfiniment."""
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")


def vacuum(conn: sqlite3.Connection) -> None:
    """Compacte le fichier .db. Opération lourde en écriture : à réserver à
    une exécution manuelle ou mensuelle (cron), jamais à chaque cycle."""
    conn.execute("VACUUM;")
