"""Delta Exchange India API keys pasted by the admin in the site (Auto-Trade page).

* Stored encrypted (Fernet, AES-128 + HMAC) in db.broker_keys. The encryption key is derived from the server secret
  BROKER_KEYS_SECRET, or JWT_SECRET when that is not set; neither is in the database.
* No route ever returns a key or a secret: only "saved", the last 4 characters and when it was saved.
* A key is checked against Delta before it is stored, and it cannot be changed while the bot is trading in that mode.
* Keys in the server's .env file (DELTA_TRADE_API_KEY …) still work and take priority.
* Every connected key is re-checked every 10 minutes (and on "Check now"): a read-only balance call, no order, no key change.
  The result is kept in db.broker_checks, so the page shows the current free balance and any new error (e.g. IP whitelist removed).
"""
import asyncio
import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

from core import db, now_utc, iso

log = logging.getLogger("broker_keys")
MODES = ("LIVE", "TESTNET")
CHECK_EVERY = 600                                          # seconds between automatic balance re-checks
CACHE = {}                                                 # mode -> (key, secret), decrypted, in memory only
BROKEN = set()                                             # modes whose stored key can no longer be decrypted


def _fernet():
    secret = os.environ.get("BROKER_KEYS_SECRET") or os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("the server has no secret to encrypt API keys with")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("vantage-broker-keys|" + secret).encode()).digest()))


def encrypt(text):
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token):
    return _fernet().decrypt(token.encode()).decode()


def server_ip():
    """The address Delta must whitelist, read from the site address (157-180-33-43.sslip.io -> 157.180.33.43)."""
    import re
    from channel_config import CFG
    m = re.search(r"(\d{1,3})-(\d{1,3})-(\d{1,3})-(\d{1,3})\.sslip\.io", CFG.get("FRONTEND_URL") or "")
    return ".".join(m.groups()) if m else os.environ.get("SERVER_PUBLIC_IP") or None


async def load():
    """Decrypt the stored keys into memory (at startup and after a change)."""
    CACHE.clear()
    BROKEN.clear()
    async for d in db.broker_keys.find({}):
        try:
            CACHE[d["_id"]] = (decrypt(d["key"]), decrypt(d["secret"]))
        except (InvalidToken, RuntimeError, KeyError):
            BROKEN.add(d["_id"])
            log.warning("stored %s API key could not be decrypted (server secret changed?)", d["_id"])


async def status():
    import delta_broker
    out = {"server_ip": server_ip()}
    for mode in MODES:
        d = await db.broker_keys.find_one({"_id": mode}, {"key": 0, "secret": 0}) or {}
        c = await db.broker_checks.find_one({"_id": mode}) or {}
        env = bool(delta_broker.env_keys(mode))
        out[mode] = {"saved": bool(d) and mode not in BROKEN, "broken": mode in BROKEN, "last4": d.get("last4"), "saved_at": d.get("saved_at"),
                     "checked_balance_inr": c.get("balance_inr", d.get("checked_balance_inr")), "balance_at": c.get("ok_at") or d.get("saved_at"),
                     "checked_at": c.get("at") or d.get("saved_at"), "check_error": c.get("error"), "check_every_sec": CHECK_EVERY,
                     "from_env": env, "active": bool(delta_broker.keys(mode))}
    return out


async def check(mode):
    """Read the free balance again with the key in use (pasted or .env). Read-only: no order is sent and the key is not changed."""
    import autotrade
    import delta_broker
    if mode not in MODES:
        raise ValueError("Unknown mode")
    if not delta_broker.keys(mode):
        raise ValueError("No key is connected for this mode")
    now = iso(now_utc())
    try:
        bal = await delta_broker.get(mode).available_inr(await autotrade.settings())
        row = {"at": now, "ok_at": now, "balance_inr": round(bal, 2), "error": None}
    except Exception as e:
        row = {"at": now, "error": explain(e, mode)}          # the last good balance and its time are kept
    await db.broker_checks.update_one({"_id": mode}, {"$set": row}, upsert=True)
    return row


async def loop():
    """Re-check every connected key every 10 minutes, so the page never shows a balance from the day the key was pasted."""
    import delta_broker
    await asyncio.sleep(45)
    while True:
        for mode in MODES:
            try:
                if delta_broker.keys(mode):
                    await check(mode)
            except Exception as e:
                log.warning("key check %s: %s", mode, e)
        await asyncio.sleep(CHECK_EVERY)


async def save(mode, key, secret, by):
    """Check the key with Delta from this server, then store it encrypted. Raises ValueError with a plain reason."""
    import autotrade
    import delta_broker
    if mode not in MODES:
        raise ValueError("Unknown mode")
    key, secret = (key or "").strip(), (secret or "").strip()
    if not key or not secret:
        raise ValueError("Paste both the API key and the API secret")
    if len(key) < 10 or len(secret) < 10 or any(c.isspace() for c in key + secret):
        raise ValueError("That does not look like a Delta API key and secret (check for missing characters or spaces)")
    await _not_in_use(mode)
    broker = delta_broker.DeltaBroker(mode, keys_override=(key, secret))
    try:
        bal = await broker.available_inr(await autotrade.settings())
    except Exception as e:
        raise ValueError(explain(e, mode)) from None
    await db.broker_keys.update_one({"_id": mode}, {"$set": {"key": encrypt(key), "secret": encrypt(secret), "last4": key[-4:], "saved_at": iso(now_utc()),
                                                             "saved_by": by, "checked_balance_inr": round(bal, 2)}}, upsert=True)
    await db.broker_checks.delete_one({"_id": mode})      # the new key's own check is now the latest
    await load()
    delta_broker.forget(mode)
    return await status()


async def remove(mode, by):
    import delta_broker
    if mode not in MODES:
        raise ValueError("Unknown mode")
    await _not_in_use(mode)
    await db.broker_keys.delete_one({"_id": mode})
    await db.broker_checks.delete_one({"_id": mode})
    await load()
    delta_broker.forget(mode)
    return await status()


async def _not_in_use(mode):
    import autotrade
    st = await autotrade.get_state()
    if st.get("mode") == mode:
        raise ValueError(f"Auto-Trade is running in {mode} mode. Switch it to PAPER first, then change the key.")


def explain(e, mode):
    """Delta's error codes in plain words."""
    s = str(e)
    ip = server_ip() or "your server's IP address"
    if "ip_not_whitelisted" in s or "IpNotWhitelisted" in s or "ip_blocked" in s:
        return f"Delta refused this server's address. In the key's settings on Delta, whitelist {ip}, then try again."
    if "signature" in s.lower():
        return "Delta rejected the signature: the API secret is wrong or incomplete."
    if "invalid_api_key" in s or "InvalidApiKey" in s or "Delta 401" in s:
        return f"Delta did not accept this key. Check that it is a {'live Delta Exchange India' if mode == 'LIVE' else 'Delta testnet'} key and that the secret is complete."
    if "unauthorized" in s.lower() or "Delta 403" in s:
        return f"Delta says this key is not allowed to do that. Give it 'Read Data' and 'Trading' permission and whitelist {ip}."
    return f"Delta check failed: {s[:180]}"
