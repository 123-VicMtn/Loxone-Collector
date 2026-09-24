#!/usr/bin/env python3
"""
Supprime toutes les séries (métadonnées + historique) d'un miniserver donné
de la base -- utile pour nettoyer les doublons laissés par un renommage de
miniserver entre deux tests (ex: "maison_externe" -> "MS-Arlopi" dans
config.external.yaml : les deux noms pointent vers le même miniserver
physique, mais chaque nom crée ses propres séries en base, d'où des
capteurs dupliqués dans le dashboard).

⚠️ Irréversible (sauf à re-poller). Arrête l'appli avant de lancer ce script
(un poll en cours pourrait réécrire des données pendant la suppression).

Usage :
    python scripts/remove_miniserver.py <config.yaml> <nom_miniserver>

Exemple :
    python scripts/remove_miniserver.py config.external.yaml maison_externe
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from config import load_config  # noqa: E402


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    config_path, miniserver_name = sys.argv[1], sys.argv[2]
    cfg = load_config(config_path)
    conn = db.get_connection(cfg.db_path)

    series_ids = [
        row[0] for row in conn.execute(
            "SELECT series_id FROM series_meta WHERE miniserver = ?", (miniserver_name,)
        ).fetchall()
    ]
    if not series_ids:
        print(f"Aucune série trouvée pour le miniserver '{miniserver_name}' "
              f"dans {cfg.db_path} -- rien à faire.")
        conn.close()
        return

    print(f"{len(series_ids)} série(s) trouvée(s) pour '{miniserver_name}' -- suppression...")

    prefix = f"{miniserver_name}:%"
    deleted_readings = conn.execute(
        "DELETE FROM readings WHERE series_id LIKE ?", (prefix,)
    ).rowcount
    deleted_hourly = conn.execute(
        "DELETE FROM readings_hourly WHERE series_id LIKE ?", (prefix,)
    ).rowcount
    deleted_meta = conn.execute(
        "DELETE FROM series_meta WHERE miniserver = ?", (miniserver_name,)
    ).rowcount
    conn.commit()
    db.checkpoint_wal(conn)
    conn.close()

    print(f"Supprimé : {deleted_meta} série(s), {deleted_readings} lecture(s) brute(s), "
          f"{deleted_hourly} moyenne(s) horaire(s).")
    print("Recharge le dashboard -- les doublons ne devraient plus apparaître.")


if __name__ == "__main__":
    main()
