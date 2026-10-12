#!/usr/bin/env bash
# Resonance backup (M4.5): a gzipped pg_dump in ./backups/, keeping the newest 14. Optionally copies the audio folder with rsync.
#
#   scripts/backup.sh                       # database only
#   RSYNC_TARGET=user@nas:/backups/audio scripts/backup.sh   # also rsync ./audio there
#
# Nightly at 03:30 from cron (crontab -e), using the absolute path to this repo:
#   30 3 * * *  cd /path/to/Resonance && scripts/backup.sh >> backups/backup.log 2>&1
#
# Restore into an empty database (the volume must be fresh, or use a new database name):
#   gunzip -c backups/resonance-YYYYmmdd-HHMMSS.sql.gz | docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
KEEP=${KEEP:-14}
DB=${BACKUP_DB:-$POSTGRES_DB}   # BACKUP_DB lets you dump another database (used to test restores)
mkdir -p backups
out="backups/resonance-$(date +%Y%m%d-%H%M%S).sql.gz"
trap 'rm -f "$out.part"' EXIT   # never leave a half-written dump behind
# dump to a temp name first so a failed run never leaves a truncated file that looks like a backup
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$DB" --no-owner | gzip > "$out.part"
gunzip -t "$out.part"
mv "$out.part" "$out"
echo "$(date '+%F %T') wrote $out ($(du -h "$out" | cut -f1))"
ls -1t backups/resonance-*.sql.gz | tail -n +$((KEEP + 1)) | while read -r old; do rm -f "$old"; echo "removed $old"; done
if [ -n "${RSYNC_TARGET:-}" ]; then
  rsync -a --delete "${AUDIO_DIR:-./audio}/" "$RSYNC_TARGET/"
  echo "$(date '+%F %T') synced audio to $RSYNC_TARGET"
fi
