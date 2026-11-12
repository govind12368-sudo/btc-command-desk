"""SMS (Twilio REST) and Telegram Bot API senders. Credentials and recipients come only from server env."""
import os
import httpx
from core import APP_NAME
from channel_config import CFG

ALERT_TEXT = "All active strategy checks passed; review the signal in your account (F31 excluded)"


def sms_configured():
    return all(CFG.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "ALERT_PHONE"))


def telegram_configured():
    return all(CFG.get(k) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"))


async def send_sms(body):
    if not sms_configured():
        raise RuntimeError("SMS not configured (Twilio SID, auth token and sender number needed)")
    sid = CFG["TWILIO_ACCOUNT_SID"]
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                         auth=(sid, CFG["TWILIO_AUTH_TOKEN"]),
                         data={"From": CFG["TWILIO_FROM_NUMBER"], "To": CFG["ALERT_PHONE"], "Body": body[:600]})
    if r.status_code >= 400:
        try:
            j = r.json()
            msg = f"{j.get('code')} {j.get('message')}"
        except ValueError:
            msg = r.text[:120]
        raise RuntimeError(f"Twilio refused ({r.status_code}: {msg})")
    return r.json().get("sid")


async def send_telegram(text):
    if not telegram_configured():
        raise RuntimeError("Telegram not configured (bot token and chat ID needed)")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"https://api.telegram.org/bot{CFG['TELEGRAM_BOT_TOKEN']}/sendMessage",
                         json={"chat_id": CFG["TELEGRAM_CHAT_ID"], "text": text[:4000], "disable_web_page_preview": True})
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code >= 400 or not j.get("ok"):
        raise RuntimeError(f"Telegram refused ({r.status_code}: {j.get('description') or r.text[:120]})")
    return str(j["result"]["message_id"])


async def telegram_find_chat():
    """Reads recent messages sent to the bot so the user can find their chat ID."""
    tok = CFG.get("TELEGRAM_BOT_TOKEN")
    if not tok:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not configured")
    async with httpx.AsyncClient(timeout=20) as c:
        j = (await c.get(f"https://api.telegram.org/bot{tok}/getUpdates")).json()
    if not j.get("ok"):
        raise RuntimeError(j.get("description", "Telegram error"))
    chats = {}
    for u in j.get("result", []):
        ch = (u.get("message") or {}).get("chat")
        if ch:
            chats[str(ch["id"])] = ch.get("first_name") or ch.get("title") or ch.get("username")
    return chats


# ---------------------------------------------------------------------------
# Phone-call alerts
#   Telegram voice call via CallMeBot (free): set CALLMEBOT_USER to your Telegram
#   @username or +91 phone number, after sending /start to @CallMeBot_txtbot once.
#   Or a normal phone call via Twilio Voice: set ALERT_CALL_PROVIDER=twilio and reuse
#   the Twilio SMS credentials + ALERT_PHONE.
# ---------------------------------------------------------------------------
CALLMEBOT_URL = "https://api.callmebot.com/start.php"
_CALL_FAIL_WORDS = ("error", "not authorized", "not authorised", "authorize callmebot", "spam", "invalid", "blocked")


def call_provider():
    p = (CFG.get("ALERT_CALL_PROVIDER") or "").strip().lower()
    if p == "off":
        return None
    if p == "twilio":
        return "twilio" if sms_configured() else None
    return "telegram" if CFG.get("CALLMEBOT_USER") else None


def call_configured():
    return call_provider() is not None


def call_status():
    p = call_provider()
    if p == "telegram":
        return "configured (Telegram voice call via CallMeBot)"
    if p == "twilio":
        return "configured (Twilio phone call)"
    if (CFG.get("ALERT_CALL_PROVIDER") or "").strip().lower() == "off":
        return "not configured — switched off (ALERT_CALL_PROVIDER=off)"
    return "not configured — set CALLMEBOT_USER (your Telegram @username or +91 number)"


def call_cooldown_seconds():
    try:
        return max(0, int(CFG.get("ALERT_CALL_COOLDOWN_SEC", "300")))
    except ValueError:
        return 300


async def place_call(spoken):
    """Rings the user and reads `spoken` aloud. Returns a short provider reference."""
    p = call_provider()
    if p is None:
        raise RuntimeError("Phone-call alert not configured")
    spoken = " ".join(str(spoken).split())[:250]
    if p == "twilio":
        from xml.sax.saxutils import escape as _x
        sid = CFG["TWILIO_ACCOUNT_SID"]
        twiml = f"<Response><Say>{_x(spoken)}</Say><Pause length=\"1\"/><Say>{_x(spoken)}</Say></Response>"
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json",
                             auth=(sid, CFG["TWILIO_AUTH_TOKEN"]),
                             data={"From": CFG["TWILIO_FROM_NUMBER"], "To": CFG["ALERT_PHONE"], "Twiml": twiml})
        if r.status_code >= 400:
            try:
                j = r.json()
                msg = f"{j.get('code')} {j.get('message')}"
            except ValueError:
                msg = r.text[:120]
            raise RuntimeError(f"Twilio call refused ({r.status_code}: {msg})")
        return r.json().get("sid")
    params = {"user": CFG["CALLMEBOT_USER"].strip(), "text": spoken,
              "lang": CFG.get("CALLMEBOT_LANG", "en-GB-Standard-B"),
              "rpt": CFG.get("CALLMEBOT_REPEAT", "2"),
              "cc": CFG.get("CALLMEBOT_CC", "missed")}
    async with httpx.AsyncClient(timeout=45) as c:
        r = await c.get(CALLMEBOT_URL, params=params)
    body = r.text or ""
    low = body.lower()
    if r.status_code >= 400 or any(w in low for w in _CALL_FAIL_WORDS):
        import re as _re
        snippet = " ".join(_re.sub(r"<[^>]+>", " ", body).split())[:160]
        raise RuntimeError(f"CallMeBot refused ({r.status_code}: {snippet or 'no detail'})")
    return "callmebot"


def _speak_price(v):
    try:
        return f"{float(v):,.0f}".replace(",", " ")
    except (TypeError, ValueError):
        return str(v)


def call_text(sig):
    """Short, speakable version of the signal (CallMeBot cuts at 256 characters)."""
    side = "long" if str(sig.get("direction", "")).upper().startswith(("L", "B")) else "short"
    return (f"{APP_NAME} alert. {str(sig.get('strategy', '')).title()} {side} signal. "
            f"Entry {_speak_price(sig.get('entry'))}. Stop {_speak_price(sig.get('stop'))}. "
            f"Target {_speak_price(sig.get('target'))}. Check Telegram and verify before trading.")


TEST_CALL_TEXT = f"{APP_NAME} test call. Phone call alerts are working. No signal, no action needed."


def signal_text(sig):
    return (f"{APP_NAME}: {ALERT_TEXT}. {sig['strategy'].upper()} {sig['direction']} "
            f"{'limit entry' if sig.get('entry_type') == 'POST-ONLY LIMIT' else 'entry'} {sig['entry']}"
            f"{' (cancel if not filled by ' + sig['valid_until'][11:16] + ' UTC)' if sig.get('valid_until') else ''} "
            f"stop {sig['stop']} target {sig['target']} (Delta BTCUSD). Model signal only, no order placed.")
