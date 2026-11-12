"""Alert-channel settings that the admin can enter in the website (Settings → Signal alerts),
so nobody has to edit the server's .env file.

Lookup order for every key: a non-empty server environment variable wins, otherwise the value saved
from the website. Secret values (passwords, tokens) are encrypted in the database with a key derived
from JWT_SECRET and are never sent back to the browser — the UI only learns whether they are set.
"""
import base64
import hashlib
import os
from collections.abc import Mapping

from core import db, now_utc, iso

DOC_ID = "alert_channels"
FIELDS = {
    # key: (secret?, label)
    "ALERT_EMAIL": (False, "Send alerts to (email)"),
    "SMTP_HOST": (False, "SMTP server"),
    "SMTP_PORT": (False, "SMTP port"),
    "SMTP_USER": (False, "SMTP login (your Gmail address)"),
    "SMTP_PASSWORD": (True, "SMTP password (Gmail App Password)"),
    "EMAIL_FROM": (False, "Sender address"),
    "TELEGRAM_BOT_TOKEN": (True, "Telegram bot token"),
    "TELEGRAM_CHAT_ID": (False, "Telegram chat ID"),
    "CALLMEBOT_USER": (False, "CallMeBot Telegram username"),
    "PUSHOVER_USER_KEY": (True, "Pushover user key (auto-trade push alerts, optional)"),
    "PUSHOVER_APP_TOKEN": (True, "Pushover app token (auto-trade push alerts, optional)"),
}
_CACHE = {}


def _fernet():
    from cryptography.fernet import Fernet
    key = hashlib.sha256(("vantage-alert-channels:" + os.environ.get("JWT_SECRET", "")).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


class _Cfg(Mapping):
    """Drop-in for os.environ in the alert modules: environment first, then website settings."""

    def __getitem__(self, k):
        v = os.environ.get(k)
        if v not in (None, ""):
            return v
        if _CACHE.get(k) not in (None, ""):
            return _CACHE[k]
        if k in os.environ:
            return os.environ[k]
        raise KeyError(k)

    def __iter__(self):
        return iter(set(os.environ) | set(_CACHE))

    def __len__(self):
        return len(set(os.environ) | set(_CACHE))

    def get(self, k, default=None):
        try:
            v = self[k]
        except KeyError:
            return default
        return default if v == "" and default is not None else v


CFG = _Cfg()


async def load():
    doc = await db.settings.find_one({"_id": DOC_ID}) or {}
    vals = {}
    f = _fernet()
    for k, (secret, _) in FIELDS.items():
        v = (doc.get("values") or {}).get(k)
        if v in (None, ""):
            continue
        if secret:
            try:
                v = f.decrypt(v.encode()).decode()
            except Exception:
                continue  # JWT_SECRET changed — treat as not set
        vals[k] = v
    _CACHE.clear()
    _CACHE.update(vals)
    return doc


async def public():
    doc = await load()
    out = {}
    for k, (secret, label) in FIELDS.items():
        env = os.environ.get(k) not in (None, "")
        site = k in _CACHE
        out[k] = {"label": label, "secret": secret, "set": env or site, "source": "server .env" if env else "website" if site else None,
                  "value": None if secret or not (env or site) else CFG.get(k)}
    return {"fields": out, "updated_at": doc.get("updated_at"), "updated_by": doc.get("updated_by")}


async def save(values, by):
    """values: {KEY: str|None}. Empty string or None for a secret keeps the saved secret;
    the special value "__clear__" removes it. Non-secret empty string clears the field."""
    doc = await db.settings.find_one({"_id": DOC_ID}) or {}
    cur = dict(doc.get("values") or {})
    f = _fernet()
    for k, v in (values or {}).items():
        if k not in FIELDS:
            continue
        secret = FIELDS[k][0]
        v = "" if v is None else str(v).strip()
        if len(v) > 300:
            raise ValueError(f"{k} is too long")
        if v == "__clear__":
            cur.pop(k, None)
        elif secret:
            if k == "SMTP_PASSWORD" and str((values or {}).get("SMTP_USER") or cur.get("SMTP_USER", "")).lower().endswith("@gmail.com"):
                v = v.replace(" ", "")  # Google shows App Passwords in groups of four letters
            if v:
                cur[k] = f.encrypt(v.encode()).decode()
        else:
            if v:
                cur[k] = v
            else:
                cur.pop(k, None)
    # Gmail convenience: a Gmail login implies Gmail's SMTP server and sender.
    user = cur.get("SMTP_USER", "")
    if user.lower().endswith(("@gmail.com", "@googlemail.com")):
        cur.setdefault("SMTP_HOST", "smtp.gmail.com")
        cur.setdefault("SMTP_PORT", "587")
        cur.setdefault("EMAIL_FROM", user)
    await db.settings.update_one({"_id": DOC_ID}, {"$set": {"values": cur, "updated_at": iso(now_utc()), "updated_by": by}}, upsert=True)
    return await public()
