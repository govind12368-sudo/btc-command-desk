#!/usr/bin/env bash
# Restore a backup made by backup.sh:   sudo bash deploy/restore.sh /opt/vantage-backups/vantage-YYYYMMDD-HHMM.archive.gz
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f "${1:-}" ] || { echo "usage: $0 <backup.archive.gz>"; exit 1; }
read -r -p "This replaces the current database with $1. Type RESTORE to continue: " ok
[ "$ok" = "RESTORE" ] || { echo "cancelled"; exit 1; }
docker compose exec -T mongo mongorestore --quiet --drop --archive --gzip < "$1"
docker compose restart backend
echo "restored"
