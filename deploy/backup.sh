#!/bin/sh
# Sauvegarde quotidienne de la base (cron de l'hôte, voir le plan § 7.3) :
# copie cohérente application en marche, contrôle d'intégrité, gzip,
# 14 jours en local. La copie hors du VPS (restic) vient ensuite.
set -eu
cd "$(dirname "$0")/.."
jour=$(date +%Y-%m-%d)
mkdir -p backups
# API de sauvegarde SQLite : cohérente même pendant les écritures du poller.
docker compose exec -T api sqlite3 data/loxone.db ".backup data/backup-en-cours.db"
docker compose exec -T api sqlite3 data/backup-en-cours.db "PRAGMA integrity_check;" | grep -qx ok
mv backend/data/backup-en-cours.db "backups/loxone-$jour.db"
gzip -f "backups/loxone-$jour.db"
find backups -name 'loxone-*.db.gz' -mtime +14 -delete
