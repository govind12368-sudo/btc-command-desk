"""Phone pop-up notifications (Web Push) for the installed Vantage BTC app.

The server signs each message with its own VAPID key and hands it to the phone maker's push service
(Apple for iPhone, Google for Android/Chrome), which wakes the phone even when the app is closed.
Free, no third-party account. Only admin accounts can register a device, because the messages carry
trade details.

The VAPID private key is generated once, stored encrypted in the database (same key as the alert
channel secrets) and never leaves the server. Subscriptions are accepted only for the known push
services, so the server can never be pointed at an arbitrary URL.
"""
import asyncio
import hashlib
import json
import logging
import os
from urllib.parse import urlparse

from core import db, now_utc, iso

log = logging.getLogger("webpush")
PUSH_HOSTS = ("push.apple.com", "fcm.googleapis.com", "push.services.mozilla.com", "notify.windows.com")
_KEYS = {}


def allowed_endpoint(url):
    try:
        u = urlparse(url)
    except Exception:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def _contact():
    """VAPID contact: the site's own address (Apple requires one); no personal email is sent."""
    base = (os.environ.get("FRONTEND_URL") or "").split(",")[0].strip().rstrip("/")
    return base if base.startswith("https://") else "https://github.com/govind12368-sudo/btc-command-desk"


async def keys():
    """(Vapid02 signer, base64url public key). Created on first use, then reused forever."""
    if _KEYS:
        return _KEYS["vapid"], _KEYS["public"]
    import py_vapid
    from cryptography.hazmat.primitives import serialization
    from channel_config import _fernet
    doc = await db.settings.find_one({"_id": "vapid"})
    if not doc:
        v = py_vapid.Vapid02()
        v.generate_keys()
        await db.settings.update_one({"_id": "vapid"}, {"$setOnInsert": {"pem": _fernet().encrypt(v.private_pem()).decode(), "created_at": iso(now_utc())}}, upsert=True)
        doc = await db.settings.find_one({"_id": "vapid"})  # if two starts raced, both use the stored one
    v = py_vapid.Vapid02.from_pem(_fernet().decrypt(doc["pem"].encode()))
    pub = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    _KEYS.update(vapid=v, public=py_vapid.b64urlencode(pub))
    return _KEYS["vapid"], _KEYS["public"]


def sub_id(endpoint):
    return hashlib.sha256(endpoint.encode()).hexdigest()[:40]


async def subscribe(sub, user, user_agent=""):
    ep = (sub or {}).get("endpoint") or ""
    k = (sub or {}).get("keys") or {}
    if not allowed_endpoint(ep) or not k.get("p256dh") or not k.get("auth") or len(ep) > 1000:
        raise ValueError("This device's notification address was not accepted")
    await db.push_subs.update_one({"_id": sub_id(ep)}, {"$set": {"endpoint": ep, "keys": {"p256dh": k["p256dh"][:200], "auth": k["auth"][:100]},
                                                                "user_id": str(user["_id"] if "_id" in user else user["id"]), "email": user.get("email"),
                                                                "device": (user_agent or "")[:160], "updated_at": iso(now_utc())},
                                                       "$setOnInsert": {"created_at": iso(now_utc())}}, upsert=True)
    return await db.push_subs.count_documents({})


async def unsubscribe(endpoint):
    await db.push_subs.delete_one({"_id": sub_id(endpoint or "")})


def _send_one(sub, payload, vapid):
    from pywebpush import webpush
    return webpush({"endpoint": sub["endpoint"], "keys": sub["keys"]}, json.dumps(payload), vapid_private_key=vapid,
                   vapid_claims={"sub": _contact()}, ttl=6 * 3600, timeout=15, headers={"Urgency": "high"})


async def _admins_only(subs):
    ids = {s.get("user_id") for s in subs}
    ok = set()
    async for u in db.users.find({}, {"role": 1, "status": 1}):
        if str(u["_id"]) in ids and u.get("role") == "admin" and u.get("status", "active") == "active":
            ok.add(str(u["_id"]))
    return [s for s in subs if s.get("user_id") in ok]


async def send(title, body, url="/", tag=None, user_id=None):
    """Send to every registered admin device (or one user's devices). Never raises."""
    try:
        q = {"user_id": user_id} if user_id else {}
        subs = await _admins_only([s async for s in db.push_subs.find(q)])
        if not subs:
            return {"sent": 0, "failed": 0, "devices": 0}
        vapid, _ = await keys()
        payload = {"title": title[:120], "body": body[:400], "url": url if url.startswith("/") else "/", "tag": tag}
        sent = failed = 0
        for s in subs:
            try:
                await asyncio.to_thread(_send_one, s, payload, vapid)
                sent += 1
                await db.push_subs.update_one({"_id": s["_id"]}, {"$set": {"last_ok": iso(now_utc())}, "$unset": {"last_error": ""}})
            except Exception as e:
                failed += 1
                code = getattr(getattr(e, "response", None), "status_code", None)
                if code in (404, 410):  # the phone removed the app or turned notifications off
                    await db.push_subs.delete_one({"_id": s["_id"]})
                else:
                    await db.push_subs.update_one({"_id": s["_id"]}, {"$set": {"last_error": f"{code or ''} {str(e)[:150]}".strip(), "last_error_at": iso(now_utc())}})
                log.warning("push to %s failed: %s %s", s.get("device", "")[:40], code, e)
        return {"sent": sent, "failed": failed, "devices": len(subs)}
    except Exception as e:
        log.warning("push send failed: %s", e)
        return {"sent": 0, "failed": 0, "devices": 0, "error": str(e)[:160]}


async def status(user_id=None):
    devs = [s async for s in db.push_subs.find({}, {"device": 1, "created_at": 1, "last_ok": 1, "last_error": 1, "user_id": 1})]
    return {"devices": [{"device": d.get("device"), "since": d.get("created_at"), "last_ok": d.get("last_ok"), "last_error": d.get("last_error"),
                         "mine": d.get("user_id") == user_id} for d in devs]}
