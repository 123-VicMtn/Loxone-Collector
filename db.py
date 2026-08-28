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
    apartment            TEXT DEFAULT '',
    apartment_manual     INTEGER DEFAULT 0,
    resource_type        TEXT DEFAULT '',
    resource_type_manual INTEGER DEFAULT 0,
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

-- Tarifs utilisés par la page de décompte de charges (/decompte).
-- Un tarif s'applique à partir de `valid_from` (date locale Europe/Zurich,
-- format 'YYYY-MM-DD') et jusqu'au tarif suivant. Stocker les tarifs en base
-- plutôt que dans le navigateur est ce qui rend un décompte REPRODUCTIBLE :
-- un mois déjà facturé garde ses prix d'origine même si les prix
-- changent ensuite, ce qui est indispensable si une facture est contestée.
CREATE TABLE IF NOT EXISTS tarifs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    valid_from   TEXT NOT NULL UNIQUE,  -- 'YYYY-MM-DD' (heure locale)
    prix_reseau  REAL NOT NULL DEFAULT 0,   -- CHF / kWh importé du réseau
    prix_solaire REAL NOT NULL DEFAULT 0,   -- CHF / kWh solaire autoconsommé
    taux_tva     REAL NOT NULL DEFAULT 0,   -- en %, ex: 8.1
    note         TEXT NOT NULL DEFAULT '',
    updated_at   INTEGER
);
"""


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Ajoute les colonnes manquantes sur une base déjà existante (créée par
    une version antérieure du projet). CREATE TABLE IF NOT EXISTS ne modifie
    pas une table déjà présente, donc les mises à jour de schéma passent par
    ici (ALTER TABLE ADD COLUMN, idempotent : ne s'applique que si la colonne
    n'existe pas encore)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(series_meta)").fetchall()}
    alters = []
    if "apartment" not in cols:
        alters.append("ALTER TABLE series_meta ADD COLUMN apartment TEXT DEFAULT ''")
    if "apartment_manual" not in cols:
        alters.append("ALTER TABLE series_meta ADD COLUMN apartment_manual INTEGER DEFAULT 0")
    if "resource_type" not in cols:
        alters.append("ALTER TABLE series_meta ADD COLUMN resource_type TEXT DEFAULT ''")
    if "resource_type_manual" not in cols:
        alters.append("ALTER TABLE series_meta ADD COLUMN resource_type_manual INTEGER DEFAULT 0")
    for stmt in alters:
        conn.execute(stmt)
    if alters:
        conn.commit()


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
    _migrate_schema(conn)
    return conn


def upsert_series_meta(conn: sqlite3.Connection, series_id: str, miniserver: str,
                        control_uuid: str, state_name: str, label: str,
                        room: str = "", category: str = "", control_type: str = "",
                        unit: str = "", apartment: str = "", resource_type: str = "") -> None:
    """Crée ou met à jour les métadonnées d'une série. `apartment` et
    `resource_type` sont ceux devinés automatiquement par classification.py
    à chaque poll : si l'utilisateur a corrigé l'un de ces champs à la main
    via /admin (apartment_manual/resource_type_manual = 1), la valeur
    manuelle est préservée et n'est jamais écrasée par une nouvelle
    devinette automatique."""
    conn.execute(
        """
        INSERT INTO series_meta
            (series_id, miniserver, control_uuid, state_name, label, room,
             category, control_type, unit, apartment, resource_type,
             apartment_manual, resource_type_manual, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
        ON CONFLICT(series_id) DO UPDATE SET
            miniserver=excluded.miniserver,
            control_uuid=excluded.control_uuid,
            state_name=excluded.state_name,
            label=excluded.label,
            room=excluded.room,
            category=excluded.category,
            control_type=excluded.control_type,
            unit=excluded.unit,
            apartment = CASE WHEN series_meta.apartment_manual = 1
                              THEN series_meta.apartment ELSE excluded.apartment END,
            resource_type = CASE WHEN series_meta.resource_type_manual = 1
                                  THEN series_meta.resource_type ELSE excluded.resource_type END,
            updated_at=excluded.updated_at
        """,
        (series_id, miniserver, control_uuid, state_name, label, room,
         category, control_type, unit, apartment, resource_type, int(time.time())),
    )


def set_series_classification(conn: sqlite3.Connection, series_id: str,
                               apartment: str | None = None,
                               resource_type: str | None = None) -> None:
    """Applique une correction manuelle depuis /admin. Seuls les champs
    fournis (non None) sont modifiés, et marqués comme "manuel" pour ne plus
    jamais être écrasés par le poller. Un appartement vide ('') est une
    valeur manuelle valide (= "aucun appartement"), distincte de None
    (= "ne pas toucher à ce champ")."""
    sets, params = [], []
    if apartment is not None:
        sets.append("apartment = ?, apartment_manual = 1")
        params.append(apartment)
    if resource_type is not None:
        sets.append("resource_type = ?, resource_type_manual = 1")
        params.append(resource_type)
    if not sets:
        return
    params.append(series_id)
    conn.execute(f"UPDATE series_meta SET {', '.join(sets)} WHERE series_id = ?", params)
    conn.commit()


def reset_series_classification(conn: sqlite3.Connection, series_id: str) -> None:
    """Repasse une série en classification automatique : au prochain cycle
    de poll, apartment/resource_type seront recalculés par classification.py
    et pourront à nouveau être écrasés par la logique automatique."""
    conn.execute(
        "UPDATE series_meta SET apartment_manual = 0, resource_type_manual = 0 "
        "WHERE series_id = ?",
        (series_id,),
    )
    conn.commit()


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
        "category, control_type, unit, apartment, apartment_manual, "
        "resource_type, resource_type_manual FROM series_meta ORDER BY room, label"
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


def upsert_hourly_batch(conn: sqlite3.Connection, rows: list[tuple]) -> int:
    """rows: liste de (series_id, ts, avg_value, min_value, max_value,
    sample_count), avec ts déjà aligné sur le début de l'heure (multiple de
    3600). Utilisé par scripts/backfill_statistics.py pour importer
    l'historique natif Loxone ("Statistics", carte SD du Miniserver) dans
    des périodes antérieures au démarrage du collecteur.

    Volontairement ON CONFLICT DO NOTHING (pas DO UPDATE comme
    downsample_and_prune) : si une ligne existe déjà pour cette heure (donc
    déjà écrite par le poller live, considéré plus fiable/plus précis), le
    backfill ne l'écrase jamais. Un backfill relancé plusieurs fois reste
    donc sans effet sur les heures déjà importées -- idempotent, et ne
    génère pas d'écriture disque inutile sur la carte SD."""
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO readings_hourly (series_id, ts, avg_value, min_value, max_value, sample_count)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(series_id, ts) DO NOTHING
        """,
        rows,
    )
    return len(rows)


def query_latest(conn: sqlite3.Connection, series_id: str) -> tuple[int, float] | None:
    """Dernière valeur connue d'une série, quel que soit son âge -- prend la
    plus récente entre `readings` (brut, récent) et `readings_hourly`
    (archivé). Contrairement à query_readings() sur toute la plage
    [0, maintenant], ceci ne scanne pas tout l'historique de la série (deux
    petits ORDER BY ... LIMIT 1 indexés, pas un UNION ALL + tri complet) --
    important pour des séries à 11+ mois d'historique horaire (des centaines
    de milliers de lignes après un backfill Statistics)."""
    row = conn.execute(
        "SELECT ts, value FROM readings WHERE series_id = ? AND value IS NOT NULL "
        "ORDER BY ts DESC LIMIT 1",
        (series_id,),
    ).fetchone()
    hourly_row = conn.execute(
        "SELECT ts, avg_value FROM readings_hourly WHERE series_id = ? "
        "ORDER BY ts DESC LIMIT 1",
        (series_id,),
    ).fetchone()
    candidates = [r for r in (row, hourly_row) if r is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r[0])


def query_daily_last(conn: sqlite3.Connection, series_id: str, start_ts: int,
                      end_ts: int) -> list[tuple[int, float]]:
    """Retourne, pour chaque jour (UTC) couvert par [start_ts, end_ts], la
    DERNIÈRE valeur connue de la série ce jour-là -- (jour_debut_epoch,
    valeur). Pensé pour un compteur cumulatif ("total", "totalDay", etc. :
    un index qui ne fait qu'augmenter) : pour ce genre de série, la valeur
    max de la journée est aussi sa valeur la plus récente (index croissant),
    donc MAX(value) par jour donne directement le relevé de fin de journée.

    Utilisé pour dériver une consommation journalière/mensuelle (delta entre
    deux relevés de fin de journée) à partir de l'historique "total" importé
    par scripts/backfill_statistics.py -- Loxone n'archive PAS d'historique
    pour totalDay/totalWeek/totalMonth/totalYear eux-mêmes (ce sont des
    compteurs vivants recalculés par le Miniserver, sans stockage SD long
    terme), donc pour une consommation passée on part toujours de "total" et
    on calcule la différence soi-même -- exactement la logique d'un décompte
    de charges classique (relevé de compteur à deux dates)."""
    cur = conn.execute(
        """
        SELECT CAST(ts / 86400 AS INTEGER) * 86400 AS day, MAX(value) FROM (
            SELECT ts, value FROM readings
             WHERE series_id = ? AND ts BETWEEN ? AND ? AND value IS NOT NULL
            UNION ALL
            SELECT ts, avg_value FROM readings_hourly
             WHERE series_id = ? AND ts BETWEEN ? AND ?
        )
        GROUP BY day
        ORDER BY day ASC
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


# --------------------------------------------------------------------------
# Décompte de charges (page /decompte)
# --------------------------------------------------------------------------

def query_value_at(conn: sqlite3.Connection, series_id: str, ts: int) -> tuple[int, float] | None:
    """Dernier relevé connu d'une série À la date `ts` ou AVANT -- retourne
    (ts_du_relevé, valeur), ou None si la série n'a aucune donnée avant `ts`.

    C'est la primitive d'un décompte de charges : la consommation d'une
    période = relevé à la fin - relevé au début, exactement comme un relevé
    de compteur physique à deux dates. On prend le dernier relevé AVANT la
    borne (et pas le premier après) pour ne jamais compter deux fois une
    consommation à cheval sur deux périodes de facturation.

    Comme query_latest(), la requête reste indexée (deux ORDER BY ... LIMIT 1)
    et ne scanne pas tout l'historique de la série.
    """
    raw = conn.execute(
        "SELECT ts, value FROM readings WHERE series_id = ? AND ts <= ? AND value IS NOT NULL "
        "ORDER BY ts DESC LIMIT 1",
        (series_id, ts),
    ).fetchone()
    hourly = conn.execute(
        "SELECT ts, avg_value FROM readings_hourly WHERE series_id = ? AND ts <= ? "
        "AND avg_value IS NOT NULL ORDER BY ts DESC LIMIT 1",
        (series_id, ts),
    ).fetchone()
    candidates = [r for r in (raw, hourly) if r is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r[0])


def list_tarifs(conn: sqlite3.Connection) -> list[dict]:
    """Tous les tarifs enregistrés, du plus ancien au plus récent."""
    cur = conn.execute(
        "SELECT id, valid_from, prix_reseau, prix_solaire, taux_tva, note, updated_at "
        "FROM tarifs ORDER BY valid_from ASC"
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def upsert_tarif(conn: sqlite3.Connection, valid_from: str, prix_reseau: float,
                  prix_solaire: float, taux_tva: float, note: str = "") -> None:
    """Crée ou remplace le tarif applicable à partir de `valid_from`
    ('YYYY-MM-DD'). Une seule ligne par date de prise d'effet (contrainte
    UNIQUE) : ré-enregistrer la même date corrige le tarif au lieu d'en
    empiler un doublon."""
    conn.execute(
        """
        INSERT INTO tarifs (valid_from, prix_reseau, prix_solaire, taux_tva, note, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(valid_from) DO UPDATE SET
            prix_reseau=excluded.prix_reseau,
            prix_solaire=excluded.prix_solaire,
            taux_tva=excluded.taux_tva,
            note=excluded.note,
            updated_at=excluded.updated_at
        """,
        (valid_from, prix_reseau, prix_solaire, taux_tva, note, int(time.time())),
    )
    conn.commit()


def delete_tarif(conn: sqlite3.Connection, tarif_id: int) -> None:
    conn.execute("DELETE FROM tarifs WHERE id = ?", (tarif_id,))
    conn.commit()
