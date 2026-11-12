#!/usr/bin/env bash
# Daily MongoDB backup (compressed). Keeps the last 14 days in /opt/vantage-backups.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="${BACKUP_DIR:-/opt/vantage-backups}"; mkdir -p "$DIR"
DB="$(grep -E '^DB_NAME=' .env | cut -d= -f2- || true)"; DB="${DB:-vantage_btc}"
OUT="$DIR/vantage-$(date -u +%Y%m%d-%H%M).archive.gz"
docker compose exec -T mongo mongodump --quiet --db "$DB" --archive --gzip > "$OUT"
find "$DIR" -name 'vantage-*.archive.gz' -mtime +14 -delete
echo "$(date -u +%FT%TZ) backup ok: $OUT ($(du -h "$OUT" | cut -f1))"
