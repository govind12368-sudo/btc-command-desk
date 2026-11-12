"""Alert master switch.

Every alert channel (email, Telegram, SMS, phone call, scheduled daily recap) is fully prepared,
but nothing is sent for real signals until an admin ARMS alert mode from Settings → Alerts.

While alert mode is OFF (the default) each would-be alert is written to alerts_log as a
"dry run" entry, so you can see exactly what would have been sent and when.

Safety:
  * Default is OFF. A fresh install never sends signal alerts on its own.
  * ALERTS_KILL_SWITCH=true in the server environment forces alert mode OFF, whatever the
    database says (useful in an emergency: edit .env, restart, done).
  * Arming requires an admin, a typed confirmation word and is recorded in the security log.
  * "Send test alert" is always available to the admin, because it is an explicit manual action.
"""
import os
from core import db, now_utc, iso

DOC_ID = "alert_mode"
CHANNELS = ("email", "telegram", "sms", "call", "daily_recap")
DEFAULT = {"armed": False, "channels": {c: True for c in CHANNELS}, "version": 0,
           "armed_at": None, "armed_by": None, "disarmed_at": None, "disarmed_by": None}
CONFIRM_WORD = "ARM"


def kill_switch():
    return (os.environ.get("ALERTS_KILL_SWITCH") or "").strip().lower() in ("1", "true", "yes", "on")


async def get_mode():
    doc = await db.settings.find_one({"_id": DOC_ID}) or {}
    mode = {**DEFAULT, **{k: v for k, v in doc.items() if k != "_id"}}
    mode["channels"] = {**DEFAULT["channels"], **(doc.get("channels") or {})}
    mode["kill_switch"] = kill_switch()
    mode["effective_armed"] = bool(mode["armed"]) and not mode["kill_switch"]
    return mode


async def channel_armed(channel):
    m = await get_mode()
    return m["effective_armed"] and bool(m["channels"].get(channel, False))


async def set_mode(*, armed, channels, version, by):
    """Optimistic-concurrency update. Returns the new mode, or None if the version is stale."""
    cur = await get_mode()
    if int(version) != int(cur["version"]):
        return None
    upd = {"armed": bool(armed), "channels": {c: bool((channels or {}).get(c, cur["channels"][c])) for c in CHANNELS},
           "version": cur["version"] + 1, "updated_at": iso(now_utc()), "updated_by": by}
    if bool(armed) and not cur["armed"]:
        upd.update(armed_at=iso(now_utc()), armed_by=by)
    if not bool(armed) and cur["armed"]:
        upd.update(disarmed_at=iso(now_utc()), disarmed_by=by)
    q = {"_id": DOC_ID, "version": cur["version"]} if cur["version"] else {"_id": DOC_ID}
    r = await db.settings.update_one(q, {"$set": upd}, upsert=not cur["version"])
    if not (r.modified_count or r.upserted_id):
        return None
    return await get_mode()


async def record_dry_run(signal_id, preview, reason):
    """Log the alert that WOULD have been sent, without sending anything."""
    await db.alerts_log.insert_one({"at": iso(now_utc()), "signal_id": signal_id, "channel": "all", "test": False,
                                    "result": f"dry run — not sent ({reason})", "dry_run": True, "preview": (preview or "")[:600]})
