# Put Vantage BTC online with Hetzner (no commands to type)

Time: about 20 minutes. Cost: Hetzner's smallest server with 4 GB RAM (a few euros a month) plus
the small charge for a public IPv4 address. Hetzner shows the exact monthly price before you click *Create*.

## 1. Create the Hetzner account
1. Go to **hetzner.com/cloud** → *Sign up*. Hetzner may ask for ID or a small card/PayPal
   verification for new accounts — this is normal.
2. In the **Cloud Console**, click **+ New project**, name it `Vantage`, and open it.

## 2. Create the server
Click **Add server** and choose:

| Setting | Choose |
|---|---|
| Location | Any. The closest one to India that is offered is fine; the app works from anywhere. |
| Image | **Ubuntu 24.04** |
| Type | **Shared vCPU** — the cheapest plan with **4 GB RAM** (x86 or Arm both work). 2 GB also works. |
| Networking | Keep **Public IPv4** and IPv6 on |
| SSH keys | Optional (you can also use the web console later) |
| Firewalls | Create one: allow **TCP 80**, **TCP 443**, **UDP 443**, and **TCP 22** (SSH). Nothing else. |
| Backups | Optional (+20%). The app already makes its own daily database backup. |
| **Cloud config** | Paste the whole file [`deploy/hetzner-cloud-config.yaml`](../deploy/hetzner-cloud-config.yaml), then change `ADMIN_EMAIL` and `ADMIN_PASSWORD` to your own. |
| Name | `vantage` |

Click **Create & Buy now**.

## 3. Open your site
1. Copy the server's **IPv4 address** (for example `116.203.10.20`).
2. Wait about 10 minutes, then open **https://116-203-10-20.sslip.io**. Use your IP, with dots replaced by dashes.
3. Sign in with the email and password you put in the Cloud config, then:
   - **System Audit → Security**: turn on two-factor login.
   - **Settings → Signal alerts**: alerts are **OFF** (dry run) until you turn them on.

If the page does not open after 15 minutes: in Hetzner, open the server → **Console**, log in as
`root` (Hetzner emailed the password if you did not add an SSH key) and run
`tail -50 /var/log/vantage-install.log` — send that text to whoever helps you.

## 4. Automatic updates from GitHub (optional, one time)
After you change code and push to GitHub, the server can update itself:
1. On your PC create a key: `ssh-keygen -t ed25519 -f vantage-deploy -N ""`
2. Hetzner Console → server → Console, run: `echo "PASTE contents of vantage-deploy.pub" >> /root/.ssh/authorized_keys`
3. GitHub → repo → Settings → Secrets and variables → Actions → add
   `SSH_HOST` = server IP, `SSH_USER` = `root`, `SSH_KEY` = contents of `vantage-deploy` (the private file).

## Useful later
- Alerts setup (Gmail, Telegram, phone call): README section 2. Edit `/opt/vantage/.env` via the console, then run `cd /opt/vantage && docker compose up -d`.
- Your own domain: point an **A record** to the server IP, set `DOMAIN=yourdomain.com` in `/opt/vantage/.env`, run `docker compose up -d`.
- Backups live in `/opt/vantage-backups` (14 days). Restore: `bash /opt/vantage/deploy/restore.sh <file>`.
