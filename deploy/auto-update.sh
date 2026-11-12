#!/usr/bin/env bash
# Runs every 10 minutes from cron. Deploys new commits on GitHub main — but only after GitHub's
# automatic tests (CI) for that commit have passed. Logs to /var/log/vantage-update.log.
set -euo pipefail
cd "$(dirname "$0")/.."
git fetch -q origin main
NEW="$(git rev-parse origin/main)"
[ "$(git rev-parse HEAD)" = "$NEW" ] && exit 0
REPO="$(git remote get-url origin | sed -E 's#.*github.com[:/]##; s#\.git$##; s#^[^@]*@##')"
RUNS="$(curl -fsS -m 20 "https://api.github.com/repos/${REPO}/commits/${NEW}/check-runs" || true)"
if [ -z "$RUNS" ] || ! echo "$RUNS" | grep -q '"conclusion": *"success"' || echo "$RUNS" | grep -qE '"conclusion": *("failure"|"cancelled"|"timed_out")|"status": *"(queued|in_progress)"'; then
  echo "$(date -u +%FT%TZ) waiting: CI for ${NEW:0:7} not passed yet"; exit 0
fi
echo "$(date -u +%FT%TZ) deploying ${NEW:0:7}"
git pull --ff-only -q
docker compose up -d --build --remove-orphans
docker image prune -f >/dev/null
echo "$(date -u +%FT%TZ) deployed ${NEW:0:7}"
