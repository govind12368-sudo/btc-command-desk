#!/usr/bin/env bash
# Vantage BTC — one-command server setup (Ubuntu 22.04 / 24.04, x86 or ARM, e.g. Oracle Cloud Always Free).
#
#   sudo apt-get update && sudo apt-get install -y git
#   sudo git clone https://github.com/govind12368-sudo/btc-command-desk.git /opt/vantage
#   cd /opt/vantage && sudo bash deploy/install.sh
#
# Safe to run again: it never overwrites an existing .env and never deletes data.
set -euo pipefail
cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"
BACKUP_DIR="/opt/vantage-backups"

say() { printf '\n\033[1;33m▶ %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo bash deploy/install.sh"; exit 1; }

say "Installing Docker and basic tools"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl git cron iptables-persistent unattended-upgrades docker.io
apt-get install -y docker-compose-v2 2>/dev/null || apt-get install -y docker-compose-plugin
systemctl enable --now docker cron
dpkg-reconfigure -f noninteractive unattended-upgrades || true

say "Adding swap if the server has less than 2 GB RAM"
if [ "$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)" -lt 2000 ] && ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

say "Opening web ports 80 and 443 in the server firewall"
for p in 80 443; do
  iptables -C INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport "$p" -j ACCEPT
done
iptables -C INPUT -p udp --dport 443 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p udp --dport 443 -j ACCEPT
netfilter-persistent save || true

if [ ! -f .env ]; then
  say "Creating .env"
  cp .env.example .env
  PUBIP="$(curl -fsS4 https://ifconfig.me || curl -fsS4 https://api.ipify.org || echo '')"
  DEF_DOMAIN="${PUBIP//./-}.sslip.io"
  DOMAIN="${DOMAIN:-}"; ADMIN_EMAIL="${ADMIN_EMAIL:-}"; ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
  if [ -t 0 ]; then
    read -r -p "Web address (press Enter for ${DEF_DOMAIN}): " DOMAIN
    read -r -p "Admin email: " ADMIN_EMAIL
    read -r -s -p "Admin password (Enter = generate a strong one): " ADMIN_PASSWORD; echo
  fi
  DOMAIN="${DOMAIN:-$DEF_DOMAIN}"
  GEN_PW=""
  if [ -z "$ADMIN_PASSWORD" ]; then
    ADMIN_PASSWORD="Vb-$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 18)!7"; GEN_PW="$ADMIN_PASSWORD"
  fi
  [ -n "$ADMIN_EMAIL" ] || { echo "ADMIN_EMAIL is required (run again, or pass ADMIN_EMAIL=you@example.com)"; rm -f .env; exit 1; }
  setv() { local k="$1" v="$2"; v="${v//\\/\\\\}"; v="${v//&/\\&}"; v="${v//|/\\|}"; sed -i "s|^${k}=.*|${k}=${v}|" .env; }
  setv DOMAIN "$DOMAIN"
  setv ACME_EMAIL "$ADMIN_EMAIL"
  setv ADMIN_EMAIL "$ADMIN_EMAIL"
  setv ADMIN_PASSWORD "$ADMIN_PASSWORD"
  setv ALERT_EMAIL "$ADMIN_EMAIL"
  setv JWT_SECRET "$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 64)"
  setv WEBHOOK_CRON_SECRET "$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 40)"
  chmod 600 .env
else
  say ".env already exists — keeping it"
fi

say "Building and starting the site (first build takes 5–10 minutes)"
docker compose up -d --build

say "Scheduling daily database backup (02:30) and weekly clean-up"
mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR"
cat > /etc/cron.d/vantage-btc <<CRON
30 2 * * * root bash ${APP_DIR}/deploy/backup.sh >> /var/log/vantage-backup.log 2>&1
15 4 * * 0 root docker system prune -af --filter "until=168h" >> /var/log/vantage-prune.log 2>&1
CRON
chmod 644 /etc/cron.d/vantage-btc

DOMAIN_NOW="$(grep -E '^DOMAIN=' .env | cut -d= -f2-)"
say "Done"
echo "  Site:   https://${DOMAIN_NOW}   (HTTPS certificate appears within ~1 minute)"
echo "  Admin:  $(grep -E '^ADMIN_EMAIL=' .env | cut -d= -f2-)"
[ -n "${GEN_PW:-}" ] && echo "  Generated admin password: ${GEN_PW}   ← save it now, then change it after first login"
echo "  Alerts: OFF (dry run). Turn ON in Settings → Signal alerts when you approve."
echo "  Status: docker compose ps     Logs: docker compose logs -f backend"
