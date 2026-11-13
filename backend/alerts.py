"""Signal alerts. Recipient comes only from server config (ALERT_EMAIL).

Email works with any free provider — pick ONE in the server .env:
  * SMTP (e.g. Gmail with an App Password, Brevo SMTP, Zoho):  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
  * Resend HTTP API (free 3,000 emails/month):                  RESEND_API_KEY
  * Brevo HTTP API (free 300 emails/day):                       BREVO_API_KEY
EMAIL_FROM is the sender address (must be allowed by the provider). EMAIL_FROM_NAME defaults to APP_NAME.
Signal alerts only go out while alert mode is ARMED (see alert_mode.py).
"""
import os
import re
import ipaddress
import logging
import httpx
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse
from core import db, now_utc, iso, parse_iso, APP_NAME
import alert_mode
from channel_config import CFG

logger = logging.getLogger("alerts")

_SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "goo.gl", "rebrand.ly")
_CRED_ASK = ("reply with your password", "reply with the code", "send your password", "cvv",
             "send us your password", "enter your password below", "confirm your card number",
             "your full card number", "seed phrase", "recovery phrase", "verify your card",
             "social security number", "confirm your bank details")
_HOSTISH = re.compile(r"\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)


def _host_ok(host):
    if not host or "xn--" in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return not any(host == s or host.endswith("." + s) for s in _SHORTENERS)


def _same_site(shown, real):
    return shown == real or real.endswith("." + shown) or shown.endswith("." + real)


class _EmailScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.urls, self.anchors = set(), [], []
        self._href, self._text = None, []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag.lower())
        self.urls += [v for k, v in attrs if k.lower() in ("href", "src") and v]
        if tag.lower() == "a":
            self._href = dict((k.lower(), v) for k, v in attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text)))
            self._href, self._text = None, []


def _assert_safe_email(subject, html):
    scan = _EmailScan(); scan.feed(html)
    if scan.tags & {"form", "input", "textarea", "select"}:
        raise ValueError("No forms or input fields in email (G2)")
    body = f"{subject}\n{html}".lower()
    for p in _CRED_ASK:
        if p in body:
            raise ValueError(f"Email asks the recipient for credentials: {p!r} (G2)")
    for url in scan.urls:
        low = url.strip().lower()
        if low.startswith(("mailto:", "tel:", "cid:", "#")):
            continue
        if not low.startswith("https://"):
            raise ValueError(f"Email links/assets must be absolute https: {url!r} (G3)")
        host = urlparse(low).hostname or ""
        if not _host_ok(host) or urlparse(low).username is not None:
            raise ValueError(f"Shortened, numeric-host or credential-bearing URL: {url!r} (G3)")
    for href, text in scan.anchors:
        real = urlparse(href.strip().lower()).hostname or ""
        if not real:
            continue
        for m in _HOSTISH.finditer(text):
            if not _same_site(m.group(1).lower(), real):
                raise ValueError(f"Anchor text {m.group(1)!r} ≠ real link host {real!r} (G3)")


def _from_name():
    return CFG.get("EMAIL_FROM_NAME") or APP_NAME


def email_provider():
    p = (CFG.get("EMAIL_PROVIDER") or "").strip().lower()
    if p in ("smtp", "resend", "brevo", "off"):
        return None if p == "off" else p
    if CFG.get("RESEND_API_KEY"):
        return "resend"
    if CFG.get("BREVO_API_KEY"):
        return "brevo"
    if CFG.get("SMTP_HOST"):
        return "smtp"
    return None


def email_status():
    p = email_provider()
    if not p:
        return "not configured — set SMTP_* (e.g. Gmail App Password), RESEND_API_KEY or BREVO_API_KEY"
    if not CFG.get("EMAIL_FROM") and p != "smtp":
        return f"incomplete — {p} needs EMAIL_FROM (a sender address verified with the provider)"
    return f"configured ({p.upper() if p == 'smtp' else p.title()})"


def _plain(html):
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"<[^>]+>", "\n", html.replace("<br>", "\n"))).strip()


async def _send_smtp(to, subject, html):
    import aiosmtplib
    host = CFG["SMTP_HOST"]
    port = int(CFG.get("SMTP_PORT") or 587)
    user = CFG.get("SMTP_USER") or None
    sender = CFG.get("EMAIL_FROM") or user
    if not sender:
        raise RuntimeError("EMAIL_FROM (or SMTP_USER) not configured")
    msg = EmailMessage()
    msg["From"] = formataddr((_from_name(), sender))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    msg.set_content(_plain(html))
    msg.add_alternative(html, subtype="html")
    implicit_tls = port == 465
    await aiosmtplib.send(msg, hostname=host, port=port, username=user, password=CFG.get("SMTP_PASSWORD") or None,
                          use_tls=implicit_tls, start_tls=not implicit_tls, timeout=30)
    return msg["Message-ID"]


async def _send_http(provider, to, subject, html):
    sender = CFG.get("EMAIL_FROM")
    if not sender:
        raise RuntimeError("EMAIL_FROM not configured")
    async with httpx.AsyncClient(timeout=30) as c:
        if provider == "resend":
            r = await c.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {CFG['RESEND_API_KEY']}"},
                             json={"from": formataddr((_from_name(), sender)), "to": [to], "subject": subject, "html": html})
        else:
            r = await c.post("https://api.brevo.com/v3/smtp/email", headers={"api-key": CFG["BREVO_API_KEY"], "accept": "application/json"},
                             json={"sender": {"name": _from_name(), "email": sender}, "to": [{"email": to}], "subject": subject, "htmlContent": html})
    if r.status_code >= 400:
        raise RuntimeError(f"{provider} refused ({r.status_code} {r.text[:120]})")
    j = r.json() if r.content else {}
    return j.get("id") or j.get("messageId")


async def send_email(*, to, subject, html):
    _assert_safe_email(subject, html)
    p = email_provider()
    if not p:
        raise RuntimeError("email not configured (set SMTP_*, RESEND_API_KEY or BREVO_API_KEY)")
    if p == "smtp":
        return await _send_smtp(to, subject, html)
    return await _send_http(p, to, subject, html)


def _template(sig, test=False):
    app = CFG["FRONTEND_URL"].split(",")[0].rstrip("/")
    name = escape(_from_name())
    if test:
        subject = f"{_from_name()}: test alert"
        rows = "<p>This is a test of signal alerts. Real alerts are sent only when a Scalping or Swing signal passes every active check (11; F31 excluded).</p>"
    else:
        subject = f"{_from_name()}: {sig['strategy'].upper()} {sig['direction']} — all {len(sig.get('checks') or []) or 11} active checks passed"
        rows = "".join(f"<tr><td style='padding:4px 12px 4px 0;color:#555'>{escape(k)}</td><td style='padding:4px 0'><b>{escape(str(v))}</b></td></tr>" for k, v in [
            ("Signal ID", sig["signal_id"]), ("Strategy", sig["strategy"]), ("Direction", sig["direction"]),
            ("Entry (USD)", sig["entry"]), ("Entry order", sig.get("entry_type") or "MARKET") , ("Valid until (UTC)", sig.get("valid_until") or "—"),
            ("Stop (USD)", sig["stop"]), ("Target (USD)", sig["target"]),
            ("Quantity (BTC)", sig["qty_btc"]), ("Net R after costs", round(sig["net_r"], 3)),
            ("Captured (UTC)", sig["captured_at"]), ("Venue", "Delta Exchange India BTCUSD perpetual"), ("Day type (last 12 hours)", _day_tag(sig))])
        rows = f"<table role='presentation'>{rows}</table>"
    html = (f"<table role='presentation' width='100%'><tr><td style='padding:24px;font-family:Arial,sans-serif;color:#111'>"
            f"<h2 style='margin:0 0 12px'>{name}</h2>{rows}"
            f"<p>Open the desk to review: <a href='{escape(app)}'>{escape(app)}</a></p>"
            f"<p style='font-size:12px;color:#888'>Sent by {name}. Model signal only — no order was placed. Verify on the exchange before trading.</p></td></tr></table>")
    return subject, html


def _day_tag(sig):
    """Range / trend / unclear at the moment of the signal, and what the day-type playbook does with it."""
    try:
        import daytype
        d = daytype.public()
        pb = {"FOLLOW": "follow the signal", "PLAN_A": "Plan A (reverse it)", "STAND_ASIDE": "stand aside"}[daytype.playbook(d, sig["direction"])]
        return f"{d['label']} → {pb}"
    except Exception:
        return "not available"


async def notify(sig, test=False):
    import messaging
    if not test:
        mode = await alert_mode.get_mode()
        if not mode["effective_armed"]:
            reason = "kill switch ALERTS_KILL_SWITCH is on" if mode["kill_switch"] else "alert mode is OFF"
            await alert_mode.record_dry_run(sig["signal_id"], messaging.signal_text(sig), reason)
            return {"result": f"dry run ({reason})", "dry_run": True}
        channels = mode["channels"]
    else:
        channels = {c: True for c in alert_mode.CHANNELS}
    to = CFG.get("ALERT_EMAIL")
    entry = {"at": iso(now_utc()), "signal_id": None if test else sig["signal_id"], "channel": "email", "to": to, "test": test}
    if not channels.get("email"):
        entry["result"] = "skipped (email channel switched off)"
    else:
        try:
            if not to:
                raise RuntimeError("ALERT_EMAIL not configured")
            subject, html = _template(sig, test)
            entry["provider_id"] = await send_email(to=to, subject=subject, html=html)
            entry["result"] = "sent"
        except Exception as e:
            logger.warning(f"alert failed: {e}")
            entry["result"] = f"failed: {e}"
    await db.alerts_log.insert_one(dict(entry))
    text = f"{APP_NAME}: test alert. " + messaging.ALERT_TEXT if test else messaging.signal_text(sig)
    for ch, ok, fn in (("sms", messaging.sms_configured(), messaging.send_sms), ("telegram", messaging.telegram_configured(), messaging.send_telegram)):
        e2 = {"at": iso(now_utc()), "signal_id": entry["signal_id"], "channel": ch, "test": test}
        if not ok:
            entry[ch] = "not configured"
            continue
        if not channels.get(ch):
            entry[ch] = "skipped (channel switched off)"
            continue
        try:
            e2["provider_id"] = await fn(text)
            e2["result"] = "sent"
        except Exception as ex:
            logger.warning(f"{ch} alert failed: {ex}")
            e2["result"] = f"failed: {ex}"
        entry[ch] = e2["result"]
        await db.alerts_log.insert_one(e2)
    entry["call"] = await _call(sig, test, entry["signal_id"]) if channels.get("call") else "skipped (call channel switched off)"
    import webpush
    if test:
        pr = await webpush.send("Vantage BTC test", "Test signal alert.", "/journal", "test")
    else:
        pr = await webpush.send(f"Signal: {sig['strategy'].upper()} {sig['direction']}", f"Entry {sig['entry']}, stop {sig['stop']}, target {sig['target']}. Day type: {_day_tag(sig)}.",
                                "/journal", sig["signal_id"])
    entry["push"] = f"sent to {pr['sent']} device(s)" if pr.get("sent") else "no device" if not pr.get("devices") else f"failed ({pr.get('failed')})"
    return entry


async def _call(sig, test, signal_id):
    """Phone-call alert (Telegram voice call or Twilio). Rate-limited so a burst of
    signals rings once, not repeatedly; the text channels above still carry every signal."""
    import messaging
    if not messaging.call_configured():
        return "not configured"
    if not test:
        cd = messaging.call_cooldown_seconds()
        last = await db.alerts_log.find_one({"channel": "call", "test": False, "result": "sent"}, sort=[("at", -1)])
        if last and cd and (now_utc() - parse_iso(last["at"])).total_seconds() < cd:
            return f"skipped (cooldown {cd}s)"
    e = {"at": iso(now_utc()), "signal_id": signal_id, "channel": "call", "test": test}
    try:
        e["provider_id"] = await messaging.place_call(messaging.TEST_CALL_TEXT if test else messaging.call_text(sig))
        e["result"] = "sent"
    except Exception as ex:
        logger.warning(f"call alert failed: {ex}")
        e["result"] = f"failed: {ex}"
    await db.alerts_log.insert_one(e)
    return e["result"]

