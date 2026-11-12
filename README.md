# Vantage BTC

Live Bitcoin decision desk + installable mobile app (PWA). It watches Delta Exchange India BTCUSD
24/7 on your own server, runs the Scalping / Swing checks (F26–F37), the Five Pillars, the confirmed
15‑minute direction, four/six‑factor models, validation, journal, news and events — and **never
places exchange orders**.

This is the independent version of the earlier *BTC Command Desk* built on Emergent. It needs **no
Emergent account, no paid AI API and no paid hosting**.

| What | How it is free |
|---|---|
| Server (runs 24/7, your PC can be off) | Oracle Cloud *Always Free* VM (or any Ubuntu VPS) |
| Database | MongoDB in Docker on the same server |
| HTTPS + domain | Caddy + Let's Encrypt; free address `<your-ip>.sslip.io` or your own domain |
| Email alerts | Gmail App Password (SMTP), or Resend / Brevo free tiers |
| Telegram, phone-call alerts | Telegram bot + CallMeBot (free) |
| Updates | Push to GitHub → GitHub Actions deploys automatically (free minutes) |
| "Desk Brief" summary | Rule-based, runs on the server — no AI service, no API bill |

---

## 1. Put it online (about 30 minutes, one time)

### a) Create the free server
1. Sign up at **cloud.oracle.com** (Free Tier). Oracle asks for a card only to verify you; Always Free resources are not charged. Choose a home region close to you (e.g. *India West (Mumbai)* or *India South (Hyderabad)*) — it cannot be changed later.
2. **Compute → Instances → Create instance**
   - Image: **Ubuntu 24.04**
   - Shape: **VM.Standard.A1.Flex**, **1 OCPU, 4 GB RAM** (inside the free limit). If it says *Out of capacity*, try another availability domain or later; `VM.Standard.E2.1.Micro` (1 GB) also works — the installer adds swap.
   - Add your SSH key (download the generated one), then **Create**.
3. Open the website ports: instance → **Subnet → Security list → Add ingress rules**: source `0.0.0.0/0`, TCP, destination ports **80** and **443**.

> **Good to know about Oracle's free tier (2026):** the free Arm allowance is now 2 OCPU / 12 GB in total,
> free A1 capacity is sometimes sold out in busy regions, and Oracle may reclaim an Always Free VM that
> looks *idle* for 7 days (CPU, network **and** memory all under 20%). The 1 OCPU / 4 GB size above keeps
> memory use above that line because this app runs 24/7. Backups (section 4) let you rebuild on a new VM
> in minutes if anything happens. Any other Ubuntu VPS works with the same steps.

### b) Install the site
SSH in (`ssh -i your-key ubuntu@SERVER_IP`) and run:

```bash
sudo apt-get update && sudo apt-get install -y git
sudo git clone https://github.com/govind12368-sudo/btc-command-desk.git /opt/vantage
cd /opt/vantage && sudo bash deploy/install.sh
```

The installer asks for the web address (press Enter to use the free `xx-xx-xx-xx.sslip.io` address),
your admin email and password, then installs Docker, opens the firewall, creates `.env` with strong
secrets, starts everything, and schedules daily backups. Open the address it prints and sign in.

> Private repository? Clone with a GitHub personal access token:
> `sudo git clone https://<TOKEN>@github.com/govind12368-sudo/btc-command-desk.git /opt/vantage`

### c) Turn on two-factor login
After the first sign-in: **System Audit → Security → enable 2FA**, and change the admin password in **Account Settings**.

---

## 1b. Other hosting choices (same install command on all of them)

The installer works on **any Ubuntu 22.04/24.04 server** with a public IP. The app needs about
1 GB of memory; **2 GB is comfortable**. Open ports 80 and 443 in the provider's firewall, then run
the commands from step 1 (b).

| Option | Cost | Notes |
|---|---|---|
| **Hetzner Cloud** (Germany/Finland/USA) | about €4–6 / month for 2 vCPU · 4 GB | Best value. Server location does not matter for this app. |
| **Contabo / Hostinger VPS** | about $5–7 / month for 4–8 GB | Lots of memory for the price; slower disks. |
| **AWS Lightsail** (Mumbai available) | $12 / month for 2 GB | Simple fixed price. Firewall: instance → *Networking* → add HTTP and HTTPS. The $5 plan has only 512 MB — too small. Asia regions include half the data allowance (still far more than this app uses). |
| **AWS EC2** | new accounts: credits on the 6-month Free plan; afterwards ~$12–15 / month for t4g.small | The Free plan account closes when it ends unless you upgrade to the Paid plan. Firewall: *Security group* → allow 80 and 443. Pick an Arm (Graviton) Ubuntu image for t4g. |
| **Google Cloud e2-micro** | free forever (US regions only) | Only 1 GB memory and 1 GB free outbound data per month; the installer adds swap. Fine for one user, slow. |
| **Oracle Cloud Always Free** | free | Most memory for free; see the notes above about capacity and idle reclaim. |

Tip for every paid provider: set a monthly budget alert in the billing page.

## 2. Alerts — prepared, but OFF until you approve

All channels are built in. **Alert mode starts OFF**: when a signal passes every check, the desk
writes a *dry run* entry ("would have sent …") to the alert log instead of sending anything.

To get ready (any time):
1. Edit `/opt/vantage/.env` (`sudo nano /opt/vantage/.env`) and fill the channels you want:
   - **Email (Gmail, free):** turn on 2‑Step Verification → create an *App Password* → set
     `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER=you@gmail.com`, `SMTP_PASSWORD=<app password>`, `EMAIL_FROM=you@gmail.com`, `ALERT_EMAIL=where alerts go`.
   - **Telegram (free):** create a bot with *@BotFather* → `TELEGRAM_BOT_TOKEN=…` → send your bot a message → Settings → *Find my Telegram chat ID* → `TELEGRAM_CHAT_ID=…`.
   - **Phone call (free via Telegram):** send `/start` to *@CallMeBot_txtbot* → `CALLMEBOT_USER=@yourname`.
2. Apply: `cd /opt/vantage && sudo docker compose up -d`
3. In the site: **Settings → Signal alerts → Send test alert** (works while OFF).

**When you approve:** Settings → Signal alerts → type `ARM` → **Turn ON alerts**. You can switch
single channels on/off there, and turn everything OFF with one click.
Emergency stop from the server: set `ALERTS_KILL_SWITCH=true` in `.env` and run `sudo docker compose up -d`.

The 8 PM IST daily recap runs from a timer inside the server and follows the same ON/OFF switch.

---

## 3. Making changes later (no Emergent, no paid platform)

* Edit the code (GitHub website, VS Code, or Claude Code with your normal Claude plan) and push to `main`.
* GitHub Actions runs the tests and, if you added the server secrets once, deploys automatically:
  repository **Settings → Secrets and variables → Actions**: `SSH_HOST` (server IP), `SSH_USER` (`ubuntu`), `SSH_KEY` (your private SSH key).
* Or deploy by hand: `cd /opt/vantage && sudo bash deploy/update.sh`
* Change the site name: `APP_NAME=` in `.env`, then `sudo docker compose up -d --build`.

## 4. Everyday commands (on the server)

| Task | Command |
|---|---|
| Status | `cd /opt/vantage && sudo docker compose ps` |
| Live logs | `sudo docker compose logs -f backend` |
| Restart | `sudo docker compose restart` |
| Backup now | `sudo bash deploy/backup.sh` (daily automatic at 02:30, 14 kept in `/opt/vantage-backups`) |
| Restore | `sudo bash deploy/restore.sh /opt/vantage-backups/<file>` |
| Health | `https://YOUR-ADDRESS/api/health` — add it to a free uptime monitor (e.g. UptimeRobot) to get an email if the server goes down |

Self-healing: containers restart automatically after crashes or a server reboot, and a watchdog
restarts the backend if its 2‑second calculation loop ever stalls.

---

## 5. Security built in
HTTPS only (HSTS), strict Content-Security-Policy, httpOnly + SameSite cookies, bcrypt passwords,
optional TOTP 2FA, login/IP rate limits, admin approval for new sign-ups, role-based access
(viewers never see P&L or limits), audit log of security events, database not exposed to the
internet, only ports 80/443 open, secrets only in `.env` (never in Git), automatic OS security updates.

## 6. Run on your own computer (developers)
```bash
docker run -d --name vantage-mongo -p 127.0.0.1:27017:27017 mongo:7
cd backend && pip install -r requirements-dev.txt
MONGO_URL=mongodb://localhost:27017 DB_NAME=dev JWT_SECRET=$(openssl rand -hex 32) \
ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD='Choose-A-Strong-Pass1!' FRONTEND_URL=http://localhost:3000 \
uvicorn server:app --port 8001
cd ../frontend && yarn install && yarn start    # http://localhost:3000 (proxies /api to :8001)
cd ../backend && python -m pytest -q             # offline test suite
```

## Layout
- `backend/` FastAPI: `sources.py` data adapters + Delta WebSocket · `indicators.py` · `strategy.py` (F26–F37, rounding, costs, sizing) · `models_calc.py` (pillars, direction, four/six-factor, readiness) · `direction_state.py` · `journal.py` · `validation.py` (30‑day 5m history, backtest) · `engine.py` (2 s loop, swing lifecycle, auto-capture) · `recovery.py` · `planner.py` · `insights.py` (Desk Brief) · `alert_mode.py` + `alerts.py` + `messaging.py` + `daily_summary.py` (alerts) · `accounts.py` / `auth_core.py` / `security.py` · `server.py` (API).
- `frontend/` React PWA (12 pages + login/registration/recovery).
- `deploy/` installer, update, backup, restore, Caddy config. `docker-compose.yml` runs everything.
- `reference/` original Excel workbook (read-only archive shown in System Audit). `docs/PRD.md` product notes.

## Data sources
Delta India WebSocket + REST (execution market), OKX BTC-USDT-SWAP, Fed H.10 / H.15, EIA Brent,
Alternative.me Fear & Greed, Bitbo ETF flows, multi-exchange funding (OKX, Deribit, Bitget, Gate, Delta),
BLS/BEA/Fed calendars, CoinDesk/Cointelegraph/Decrypt/Fed/BBC RSS. Optional: CoinGlass key, Delta read-only API key.

## License
MIT — © 2026 Govind Bhendegawe. Educational use; not financial advice. Verify every trade on the exchange yourself.
