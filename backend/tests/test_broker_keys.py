"""Delta API keys pasted in the site, and alerts limited to Day-type switch trades."""
import asyncio
import importlib
import os
import sys

import pytest

pytest.importorskip("mongomock_motor")
import motor.motor_asyncio as mm  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_db")
run = lambda c: asyncio.new_event_loop().run_until_complete(c)


@pytest.fixture()
def m(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    monkeypatch.setenv("JWT_SECRET", "unit-test-secret-unit-test-secret")
    monkeypatch.delenv("BROKER_KEYS_SECRET", raising=False)
    for k in ("DELTA_TRADE_API_KEY", "DELTA_TRADE_API_SECRET", "DELTA_TESTNET_API_KEY", "DELTA_TESTNET_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    for x in ("core", "broker_keys", "delta_broker", "autotrade", "daytype", "alerts", "alert_mode", "messaging"):
        sys.modules.pop(x, None)
    return type("M", (), {k: importlib.import_module(k) for k in ("core", "broker_keys", "delta_broker", "autotrade", "alerts")})


KEY, SECRET = "AbCdEfGhIjKlMn1234", "zYxWvUtSrQpOnMlKjIhGfEdCbA987654"


def test_encryption_and_nothing_secret_in_the_database(m):
    bk = m.broker_keys
    tok = bk.encrypt(SECRET)
    assert SECRET not in tok and bk.decrypt(tok) == SECRET

    async def ok(self, cfg):
        assert self._keys == (KEY, SECRET)                 # the candidate key is what gets checked, not a stored one
        return 52000.0
    m.delta_broker.DeltaBroker.available_inr = ok
    st = run(bk.save("LIVE", "  " + KEY + " ", SECRET + "\n", "me"))
    assert st["LIVE"]["saved"] and st["LIVE"]["active"] and st["LIVE"]["last4"] == "1234" and st["LIVE"]["checked_balance_inr"] == 52000.0
    assert "key" not in st["LIVE"] and "secret" not in st["LIVE"] and not st["TESTNET"]["saved"]
    raw = run(m.core.db.broker_keys.find_one({"_id": "LIVE"}))
    assert KEY not in str(raw) and SECRET not in str(raw)
    assert m.delta_broker.keys("LIVE") == (KEY, SECRET) and m.delta_broker.keys("TESTNET") is None
    bk.CACHE.clear()
    run(bk.load())                                         # after a restart the key is decrypted again
    assert m.delta_broker.keys("LIVE") == (KEY, SECRET)
    os.environ["DELTA_TRADE_API_KEY"], os.environ["DELTA_TRADE_API_SECRET"] = "envkey", "envsecret"
    try:
        assert m.delta_broker.keys("LIVE") == ("envkey", "envsecret")  # the server file still wins
    finally:
        del os.environ["DELTA_TRADE_API_KEY"], os.environ["DELTA_TRADE_API_SECRET"]
    st = run(bk.remove("LIVE", "me"))
    assert not st["LIVE"]["saved"] and m.delta_broker.keys("LIVE") is None


def test_bad_keys_are_never_stored(m):
    bk, db = m.broker_keys, m.core.db

    async def refused(self, cfg):
        raise m.delta_broker.DeltaError(401, "ip_not_whitelisted_for_api_key")
    m.delta_broker.DeltaBroker.available_inr = refused
    with pytest.raises(ValueError, match="whitelist"):
        run(bk.save("LIVE", KEY, SECRET, "me"))
    assert run(db.broker_keys.count_documents({})) == 0
    for k, s in (("", SECRET), (KEY, ""), ("short", SECRET), ("has space here!!", SECRET)):
        with pytest.raises(ValueError):
            run(bk.save("LIVE", k, s, "me"))
    with pytest.raises(ValueError):
        run(bk.save("MAINNET", KEY, SECRET, "me"))
    assert "secret is wrong" in bk.explain(Exception("Delta 401: Signature Mismatch"), "LIVE")
    assert "live Delta Exchange India" in bk.explain(Exception("Delta 401: invalid_api_key"), "LIVE")


def test_key_cannot_change_while_trading_in_that_mode(m):
    bk = m.broker_keys

    async def ok(self, cfg):
        return 1.0
    m.delta_broker.DeltaBroker.available_inr = ok
    run(m.autotrade.get_state())
    run(m.autotrade.set_state(mode="TESTNET"))
    with pytest.raises(ValueError, match="Switch it to PAPER"):
        run(bk.save("TESTNET", KEY, SECRET, "me"))
    with pytest.raises(ValueError, match="Switch it to PAPER"):
        run(bk.remove("TESTNET", "me"))
    assert run(bk.save("LIVE", KEY, SECRET, "me"))["LIVE"]["saved"]       # the other mode is unaffected


def test_wrong_server_secret_marks_the_key_broken(m, monkeypatch):
    bk = m.broker_keys

    async def ok(self, cfg):
        return 1.0
    m.delta_broker.DeltaBroker.available_inr = ok
    run(bk.save("LIVE", KEY, SECRET, "me"))
    monkeypatch.setenv("JWT_SECRET", "a-different-secret-a-different-secret")
    run(bk.load())
    st = run(bk.status())
    assert st["LIVE"]["broken"] and not st["LIVE"]["saved"] and m.delta_broker.keys("LIVE") is None


SIG = {"signal_id": "SCALP-SHORT-1", "strategy": "scalp", "direction": "SHORT", "entry": 64000.0, "stop": 64320.0, "target": 63360.0,
       "qty_btc": 0.15, "net_r": 1.8, "captured_at": "2026-11-16T10:00:00Z", "checks": [1] * 11}


def test_alerts_only_for_day_type_switch_trades(m, monkeypatch):
    import daytype
    al = m.alerts
    day = {"type": "RANGE", "label": "Range", "score": 0}
    monkeypatch.setattr(daytype, "public", lambda: day)
    v, why = run(al.switch_view(SIG))                      # range day: Plan A reverses the short into a long
    assert why is None and v["direction"] == "LONG" and v["stop"] == 63680.0 and v["target"] == 64640.0 and v["signal_direction"] == "SHORT"
    assert v["switch_plan"].startswith("Plan A") and v["switch_day"] == "Range" and SIG["direction"] == "SHORT"
    subject, html = al._template(v)
    assert "Day-type switch LONG" in subject and "Plan A" in html and "63680" in html
    day.update(type="TREND_DOWN", label="Trend (down)", score=4)
    v, _ = run(al.switch_view(SIG))                        # trend down + short signal: follow it unchanged
    assert v["direction"] == "SHORT" and v["stop"] == 64320.0 and v["target"] == 63360.0 and v["switch_plan"].startswith("Follow")
    assert run(al.switch_view({**SIG, "direction": "LONG"}))[0] is None   # long against a down trend: stand aside
    day.update(type="MIXED", label="Unclear", score=2)
    v, why = run(al.switch_view(SIG))
    assert v is None and "stand aside" in why
    assert run(al.switch_view({**SIG, "strategy": "swing"}))[0] is None
    r = run(al.notify(SIG))                                # unclear day: nothing is sent, the decision is logged
    assert r["result"].startswith("not sent") and r["signal_id"] == "SCALP-SHORT-1"
    assert run(m.core.db.alerts_log.find_one({}))["result"].startswith("not sent (day type is Unclear")


def test_tick_check_accepts_live_and_testnet(m):
    f = m.delta_broker.tick_fits
    assert f(0.5) and f(0.1) and f(0.25) and f(0.05)          # live 0.5, testnet 0.1: 0.5-step prices are valid on both
    assert not f(1.0) and not f(0.3) and not f(0) and not f(2.0)
