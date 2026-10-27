#!/usr/bin/env python3
"""
Compacte la base SQLite (VACUUM). Opération lourde en écriture : à lancer
manuellement de temps en temps, ou via une tâche cron mensuelle, JAMAIS à
chaque cycle de poll (voir db.vacuum()).

Usage :
    python scripts/vacuum_db.py [chemin_config.yaml]

Exemple de cron (1er du mois à 4h) :
    0 4 1 * * /home/pi/loxone-collector/.venv/bin/python /home/pi/loxone-collector/scripts/vacuum_db.py
"""
import sys

from loxone_collector import db
from loxone_collector.config import load_config


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    cfg = load_config(config_path)
    conn = db.get_connection(cfg.db_path)
    print(f"VACUUM de {cfg.db_path} en cours...")
    db.vacuum(conn)
    conn.close()
    print("Terminé.")


if __name__ == "__main__":
    main()
