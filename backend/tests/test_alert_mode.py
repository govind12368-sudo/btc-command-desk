"""Alert master switch: nothing is sent for real signals until an admin arms alert mode."""
import asyncio
import importlib
import os
import sys

import pytest

pytest.importorskip("mongomock_motor")
import motor.motor_asyncio as mm  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

SIG = {"signal_id": "SCALP-LONG-1", "strategy": "scalp", "direction": "LONG", "entry": 90000, "stop": 89550,
       "target": 90675, "qty_btc": 0.05, "net_r": 1.2, "captured_at": "2026-11-12T08:00:00Z", "checks": [1] * 11}


@pytest.fixture()
def mods(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for k in ("ALERTS_KILL_SWITCH", "RESEND_API_KEY", "BREVO_API_KEY", "SMTP_HOST", "EMAIL_PROVIDER",
              "TELEGRAM_BOT_TOKEN", "CALLMEBOT_USER", "TWILIO_ACCOUNT_SID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALERT_EMAIL", "me@example.com")
    for m in ("core", "alert_mode", "messaging", "alerts"):
        sys.modules.pop(m, None)
    core = importlib.import_module("core")
    return core, importlib.import_module("alert_mode"), importlib.import_module("alerts")


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


def test_default_is_disarmed_and_signal_is_dry_run(mods):
    core, am, al = mods

    async def go():
        m = await am.get_mode()
        assert m["armed"] is False and m["effective_armed"] is False
        r = await al.notify(SIG)
        assert r["dry_run"] is True
        rows = [x async for x in core.db.alerts_log.find()]
        assert len(rows) == 1 and rows[0]["dry_run"] and "SCALP LONG" in rows[0]["preview"]
    run(go())


def test_arm_disarm_versioning(mods):
    _, am, _ = mods

    async def go():
        m = await am.set_mode(armed=True, channels=None, version=0, by="admin@x")
        assert m["effective_armed"] and m["armed_by"] == "admin@x" and m["version"] == 1
        assert await am.set_mode(armed=False, channels=None, version=0, by="admin@x") is None  # stale version
        m = await am.set_mode(armed=False, channels={"sms": False}, version=1, by="admin@x")
        assert not m["armed"] and m["channels"]["sms"] is False and m["channels"]["email"] is True
    run(go())


def test_kill_switch_overrides_armed(mods, monkeypatch):
    _, am, al = mods

    async def go():
        await am.set_mode(armed=True, channels=None, version=0, by="admin@x")
        monkeypatch.setenv("ALERTS_KILL_SWITCH", "true")
        assert (await am.get_mode())["effective_armed"] is False
        assert (await al.notify(SIG))["dry_run"] is True
    run(go())


def test_email_provider_detection(mods, monkeypatch):
    _, _, al = mods
    assert al.email_provider() is None
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    assert al.email_provider() == "smtp"
    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    assert al.email_provider() == "resend"
    monkeypatch.setenv("EMAIL_PROVIDER", "off")
    assert al.email_provider() is None
