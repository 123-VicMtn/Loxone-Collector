#!/bin/sh
# Sauvegarde quotidienne de la base (cron de l'hôte, voir le plan § 7.3) :
# copie cohérente application en marche, contrôle d'intégrité, gzip,
# 14 jours en local, puis copie chiffrée hors du VPS avec restic si
# ~/.config/restic/env existe (RESTIC_REPOSITORY, RESTIC_PASSWORD et les
# identifiants OpenStack Swift de Swiss Backup, OS_* ; BACKUP_PING_URL
# facultatif).
set -eu
cd "$(dirname "$0")/.."
jour=$(date +%Y-%m-%d)
mkdir -p backups
# Sans ce contrôle, sqlite3 créerait une base vide et la sauvegarderait.
docker compose exec -T api test -s data/loxone.db
# API de sauvegarde SQLite : cohérente même pendant les écritures du poller.
docker compose exec -T api sqlite3 data/loxone.db ".backup data/backup-en-cours.db"
docker compose exec -T api sqlite3 data/backup-en-cours.db "PRAGMA integrity_check;" | grep -qx ok
mv backend/data/backup-en-cours.db "backups/loxone-$jour.db"
gzip -f "backups/loxone-$jour.db"
find backups -name 'loxone-*.db.gz' -mtime +14 -delete

env_restic="${RESTIC_ENV:-$HOME/.config/restic/env}"
if [ -f "$env_restic" ]; then
	set -a; . "$env_restic"; set +a
	restic backup --quiet --tag loxone "backups/loxone-$jour.db.gz" backend/config.yaml backend/.env .env
	restic forget --quiet --tag loxone --keep-daily 14 --keep-monthly 12 --prune
	# Signal « sauvegarde faite » (healthchecks.io) : alerte si elle ne vient plus.
	if [ -n "${BACKUP_PING_URL:-}" ]; then
		curl -fsS -m 10 --retry 3 "$BACKUP_PING_URL" >/dev/null
	fi
fi
echo "Sauvegarde du $jour terminée."
