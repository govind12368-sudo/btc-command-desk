"""Phone-call alerts (offline): provider selection, CallMeBot / Twilio requests, failure detection,
speakable text, and the call cooldown in alerts._call. No network, no Mongo."""
import asyncio
import os
import sys
import types
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_ENV_KEYS = ("CALLMEBOT_USER", "CALLMEBOT_LANG", "CALLMEBOT_REPEAT", "CALLMEBOT_CC", "ALERT_CALL_PROVIDER",
             "ALERT_CALL_COOLDOWN_SEC", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "ALERT_PHONE")

SIG = {"signal_id": "S1", "strategy": "swing", "direction": "LONG", "entry": 98450.5, "stop": 97800, "target": 100100}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def _messaging():
    import messaging
    return messaging


def _mock_client(handler, seen):
    real = httpx.AsyncClient

    def factory(*a, **kw):
        def wrapped(req):
            seen.append(req)
            return handler(req)
        kw["transport"] = httpx.MockTransport(wrapped)
        return real(*a, **kw)
    return factory


# ------------------------------------------------------------ provider selection
def test_not_configured_by_default():
    m = _messaging()
    assert m.call_provider() is None and not m.call_configured()
    assert m.call_status().startswith("not configured")


def test_callmebot_selected_by_user(monkeypatch):
    m = _messaging()
    monkeypatch.setenv("CALLMEBOT_USER", "@govind")
    assert m.call_provider() == "telegram" and m.call_status().startswith("configured")


def test_off_switch(monkeypatch):
    m = _messaging()
    monkeypatch.setenv("CALLMEBOT_USER", "@govind")
    monkeypatch.setenv("ALERT_CALL_PROVIDER", "off")
    assert m.call_provider() is None and "switched off" in m.call_status()


def test_twilio_needs_credentials(monkeypatch):
    m = _messaging()
    monkeypatch.setenv("ALERT_CALL_PROVIDER", "twilio")
    assert m.call_provider() is None
    for k, v in (("TWILIO_ACCOUNT_SID", "AC1"), ("TWILIO_AUTH_TOKEN", "t"), ("TWILIO_FROM_NUMBER", "+1555"), ("ALERT_PHONE", "+9199")):
        monkeypatch.setenv(k, v)
    assert m.call_provider() == "twilio"


def test_cooldown_parsing(monkeypatch):
    m = _messaging()
    assert m.call_cooldown_seconds() == 300
    monkeypatch.setenv("ALERT_CALL_COOLDOWN_SEC", "60")
    assert m.call_cooldown_seconds() == 60
    monkeypatch.setenv("ALERT_CALL_COOLDOWN_SEC", "abc")
    assert m.call_cooldown_seconds() == 300


# ------------------------------------------------------------ text
def test_call_text_is_short_and_speakable():
    m = _messaging()
    t = m.call_text(SIG)
    assert "Swing long signal" in t and "Entry 98 450" in t and "Stop 97 800" in t and "Target 100 100" in t
    assert len(t) <= 250
    assert "short signal" in m.call_text(SIG | {"direction": "SHORT"})


# ------------------------------------------------------------ requests
def test_callmebot_request(monkeypatch):
    m = _messaging()
    monkeypatch.setenv("CALLMEBOT_USER", "@govind")
    seen = []
    with patch.object(m.httpx, "AsyncClient", _mock_client(lambda r: httpx.Response(200, text="<p>Call queued for @govind</p>"), seen)):
        assert asyncio.run(m.place_call("hello   world")) == "callmebot"
    q = seen[0].url.params
    assert seen[0].url.host == "api.callmebot.com" and seen[0].url.scheme == "https"
    assert q["user"] == "@govind" and q["text"] == "hello world" and q["cc"] == "missed" and q["rpt"] == "2"


def test_callmebot_error_page_is_failure(monkeypatch):
    m = _messaging()
    monkeypatch.setenv("CALLMEBOT_USER", "@govind")
    seen = []
    with patch.object(m.httpx, "AsyncClient", _mock_client(lambda r: httpx.Response(200, text="<b>ERROR:</b> You need to authorize CallMeBot"), seen)):
        with pytest.raises(RuntimeError, match="CallMeBot refused"):
            asyncio.run(m.place_call("x"))


def test_twilio_call_request(monkeypatch):
    m = _messaging()
    monkeypatch.setenv("ALERT_CALL_PROVIDER", "twilio")
    for k, v in (("TWILIO_ACCOUNT_SID", "AC1"), ("TWILIO_AUTH_TOKEN", "t"), ("TWILIO_FROM_NUMBER", "+1555"), ("ALERT_PHONE", "+9199")):
        monkeypatch.setenv(k, v)
    seen = []
    with patch.object(m.httpx, "AsyncClient", _mock_client(lambda r: httpx.Response(201, json={"sid": "CA9"}), seen)):
        assert asyncio.run(m.place_call("Entry <1> & go")) == "CA9"
    body = seen[0].content.decode()
    assert seen[0].url.path.endswith("/Accounts/AC1/Calls.json") and "To=%2B9199" in body
    assert "%26amp%3B" in body and "%26lt%3B1%26gt%3B" in body  # TwiML-escaped


# ------------------------------------------------------------ alerts._call cooldown (fake db)
class _FakeLog:
    def __init__(self):
        self.docs = []

    async def find_one(self, flt, sort=None):
        hits = [d for d in self.docs if all(d.get(k) == v for k, v in flt.items())]
        return sorted(hits, key=lambda d: d["at"])[-1] if hits else None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))


def _alerts(monkeypatch):
    os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
    os.environ.setdefault("DB_NAME", "test_db")
    import alerts
    log = _FakeLog()
    monkeypatch.setattr(alerts, "db", types.SimpleNamespace(alerts_log=log))
    return alerts, log


def test_call_skipped_when_not_configured(monkeypatch):
    alerts, log = _alerts(monkeypatch)
    assert asyncio.run(alerts._call(SIG, False, "S1")) == "not configured" and log.docs == []


def test_call_sent_then_cooldown(monkeypatch):
    alerts, log = _alerts(monkeypatch)
    m = _messaging()
    monkeypatch.setenv("CALLMEBOT_USER", "@govind")
    calls = []

    async def fake_place(text):
        calls.append(text)
        return "callmebot"
    monkeypatch.setattr(m, "place_call", fake_place)
    assert asyncio.run(alerts._call(SIG, False, "S1")) == "sent"
    assert asyncio.run(alerts._call(SIG | {"signal_id": "S2"}, False, "S2")).startswith("skipped (cooldown")
    assert len(calls) == 1 and "Swing long" in calls[0]
    # test calls ignore the cooldown
    assert asyncio.run(alerts._call(None, True, None)) == "sent" and "test call" in calls[-1]
    # cooldown expires
    log.docs[0]["at"] = alerts.iso(alerts.now_utc() - timedelta(seconds=301))
    assert asyncio.run(alerts._call(SIG | {"signal_id": "S3"}, False, "S3")) == "sent"


def test_call_failure_logged(monkeypatch):
    alerts, log = _alerts(monkeypatch)
    m = _messaging()
    monkeypatch.setenv("CALLMEBOT_USER", "@govind")

    async def boom(text):
        raise RuntimeError("CallMeBot refused (200: ERROR)")
    monkeypatch.setattr(m, "place_call", boom)
    r = asyncio.run(alerts._call(SIG, False, "S1"))
    assert r.startswith("failed: CallMeBot refused") and log.docs[-1]["channel"] == "call"
