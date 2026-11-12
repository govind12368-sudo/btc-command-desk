#!/usr/bin/env bash
# One time: updates this server now and turns on automatic updates from GitHub (every 10 minutes).
set -euo pipefail
cd "$(dirname "$0")/.."
chmod +x deploy/*.sh
cat > /etc/cron.d/vantage-auto-update <<CRON
*/10 * * * * root bash $(pwd)/deploy/auto-update.sh >> /var/log/vantage-update.log 2>&1
CRON
chmod 644 /etc/cron.d/vantage-auto-update
docker compose up -d --build --remove-orphans
echo "Updated. Automatic updates are ON (log: /var/log/vantage-update.log)."
