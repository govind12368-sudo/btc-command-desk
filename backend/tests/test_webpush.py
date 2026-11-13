"""Phone notifications: key creation, which addresses are accepted, admin-only delivery, dead devices removed."""
import asyncio
import importlib
import os
import sys

import pytest

pytest.importorskip("mongomock_motor")
pytest.importorskip("pywebpush")
import motor.motor_asyncio as mm  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_db")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-123")


@pytest.fixture()
def wp(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "webpush", "channel_config"):
        sys.modules.pop(m, None)
    return importlib.import_module("webpush"), importlib.import_module("core")


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


SUB = {"endpoint": "https://web.push.apple.com/QGabc123", "keys": {"p256dh": "BNc" + "A" * 84, "auth": "x" * 22}}


def test_endpoint_whitelist(wp):
    w, _ = wp
    assert w.allowed_endpoint("https://web.push.apple.com/abc") and w.allowed_endpoint("https://fcm.googleapis.com/fcm/send/x")
    assert not w.allowed_endpoint("http://web.push.apple.com/abc")
    assert not w.allowed_endpoint("https://evil.example.com/push.apple.com")
    assert not w.allowed_endpoint("https://push.apple.com.evil.com/x")
    assert not w.allowed_endpoint("https://169.254.169.254/latest")


def test_keys_are_created_once_and_stored_encrypted(wp):
    w, core = wp

    async def go():
        v, pub = await w.keys()
        doc = await core.db.settings.find_one({"_id": "vapid"})
        assert "BEGIN" not in doc["pem"] and len(pub) == 87
        w._KEYS.clear()
        assert (await w.keys())[1] == pub
    run(go())


def test_send_only_to_admins_and_drop_dead_devices(wp, monkeypatch):
    w, core = wp
    sent = []

    class Gone(Exception):
        response = type("R", (), {"status_code": 410})()

    def fake(sub, payload, vapid):
        if "dead" in sub["endpoint"]:
            raise Gone("gone")
        sent.append((sub["endpoint"], payload))

    monkeypatch.setattr(w, "_send_one", fake)

    async def go():
        await core.db.users.insert_many([{"_id": "a1", "role": "admin", "status": "active"}, {"_id": "u1", "role": "user", "status": "active"}])
        await w.subscribe(SUB, {"id": "a1", "email": "a@x"}, "iPhone")
        await w.subscribe({**SUB, "endpoint": "https://web.push.apple.com/dead"}, {"id": "a1"}, "old iPhone")
        await w.subscribe({**SUB, "endpoint": "https://web.push.apple.com/user"}, {"id": "u1"}, "someone")
        with pytest.raises(ValueError):
            await w.subscribe({**SUB, "endpoint": "https://example.com/x"}, {"id": "a1"})
        r = await w.send("Paper · Exit – TARGET", "net +$41", "/auto-trade")
        assert r == {"sent": 1, "failed": 1, "devices": 2}
        assert sent[0][0].endswith("QGabc123") and sent[0][1]["title"] == "Paper · Exit – TARGET" and sent[0][1]["url"] == "/auto-trade"
        assert await core.db.push_subs.count_documents({}) == 2   # the dead one was removed, the non-admin one never receives
        assert (await w.send("x", "y", "https://evil.com"))["sent"] == 1 and sent[-1][1]["url"] == "/"
    run(go())


def test_push_title(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    sys.modules.pop("autotrade", None)
    at = importlib.import_module("autotrade")
    assert at._push_title("[PAPER] Vantage BTC auto-trade: Exit – TARGET") == "Paper · Exit – TARGET"
    assert at._push_title("[LIVE] Vantage BTC auto-trade: Daily loss limit hit") == "Daily loss limit hit"
