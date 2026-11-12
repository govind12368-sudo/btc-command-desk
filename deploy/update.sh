#!/usr/bin/env bash
# Pull the latest code from GitHub and rebuild. Data in MongoDB is kept.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose up -d --build --remove-orphans
docker image prune -f >/dev/null
docker compose ps
