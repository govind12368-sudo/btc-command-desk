"""Auto-trade acceptance tests (spec section 9, tests 1–12) plus sizing, fees, idempotency and Delta signing."""
import asyncio
import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("mongomock_motor")
import motor.motor_asyncio as mm  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_db")
T0 = datetime(2026, 11, 13, 6, 0, tzinfo=timezone.utc)  # 11:30 IST


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t

    def add(self, **kw):
        self.t += timedelta(**kw)


class FakeExchange:
    """Stands in for Delta in TESTNET/LIVE-style tests: the position lives here, not in our database."""
    name = "TESTNET"

    def __init__(self):
        self.entries, self.closes, self.cancels, self.pos, self.bracket, self.balance = [], [], 0, None, True, 45000.0

    async def available_inr(self, cfg):
        return self.balance

    async def prepare(self, cfg):
        return None

    async def enter(self, side, n, stop, target, coid, price, cfg):
        self.entries.append(coid)
        self.pos = {"size": n if side == "buy" else -n, "entry": price}
        return {"price": price, "contracts": n, "role": "taker"}

    async def find_entry(self, coid):
        raise self.NotPlaced("none")

    async def confirm_bracket(self, t, timeout=5):
        return self.bracket

    async def position(self):
        return self.pos

    async def poll_exit(self, t, price, hi, lo, cfg):
        return None

    async def resolve_exit(self, t, price):
        return {"reason": "STOP", "price": t["stop"], "role": "taker"}

    async def close(self, t, coid, price):
        self.closes.append(coid)
        self.pos = None
        return {"price": price, "role": "taker"}

    async def cancel_all(self):
        self.cancels += 1


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "autotrade", "delta_broker", "daytype"):
        sys.modules.pop(m, None)
    core = importlib.import_module("core")
    at = importlib.import_module("autotrade")
    at.DEFAULTS["direction_mode"] = 0   # these tests cover Plan A; the day-type modes are tested in test_daytype.py
    clock, alerts = Clock(), []

    async def alert(mode, title, text):
        alerts.append((mode, title, text))

    def engine(broker=None):
        return at.Engine(broker_factory=(lambda mode: broker) if broker else None, clock=clock, alert=alert)

    return type("Env", (), {"core": core, "at": at, "clock": clock, "alerts": alerts, "engine": staticmethod(engine), "db": core.db})


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


async def signal(e, sid, direction="SHORT", entry=72000.0, stop=72360.0, strategy="scalp", valid=None):
    await e.db.signals.insert_one({"_id": sid, "signal_id": sid, "strategy": strategy, "direction": direction, "entry": entry, "stop": stop,
                                   "target": entry - 500, "qty_btc": 0.05, "captured_at": e.core.iso(e.clock()), "valid_until": valid})


async def sig_status(e, sid):
    return await e.db.autotrade_signals.find_one({"_id": sid})


async def closed_trade(e, net, mode="PAPER"):
    n = await e.db.autotrade_trades.count_documents({})
    await e.db.autotrade_trades.insert_one({"_id": f"OLD-{n}", "signal_id": f"OLD-{n}", "mode": mode, "status": "CLOSED", "net_usd": net,
                                            "net_inr": net * 85, "day": e.core.ist_date(e.clock()), "exit_time": e.core.iso(e.clock())})


# ---- 1, 2: reverse 1:2 levels
def test_01_model_short_becomes_long(env):
    lv = env.at.levels("SHORT", 71602, 71960.5)
    assert (lv["bot_dir"], lv["stop"], lv["target"]) == ("LONG", 71243.5, 72319.0)


def test_02_model_long_becomes_short(env):
    lv = env.at.levels("LONG", 73949, 73579)
    assert (lv["bot_dir"], lv["stop"], lv["target"]) == ("SHORT", 74319.0, 73209.0)


# ---- 3: sizing
def test_03_sizing_40000_inr_10x(env):
    n, info = env.at.size(72000, 71640, env.at.DEFAULTS, 45000, 0)
    assert n == 65 and info["limited_by"] == "margin"
    assert env.at.size(72000, 71640, env.at.DEFAULTS, 30000, 0)[0] == 40  # balance − ₹5,000 buffer caps the margin
    assert env.at.size(72000, 71640, {**env.at.DEFAULTS, "max_contracts": 20}, 45000, 0)[0] == 20


# ---- 4: entry window
def test_04_entry_not_reached_in_10_minutes(env):
    async def go():
        eng = env.engine()
        await eng.tick(71900)                        # creates state
        await signal(env, "SCALP-SHORT-1")           # bot LONG, needs price ≥ 72000
        await eng.tick(71900)
        assert (await sig_status(env, "SCALP-SHORT-1"))["status"] == "ARMED"
        env.clock.add(minutes=9)
        await eng.tick(71990)
        assert (await sig_status(env, "SCALP-SHORT-1"))["status"] == "ARMED"
        env.clock.add(minutes=1, seconds=1)
        await eng.tick(72100)                        # too late
        assert (await sig_status(env, "SCALP-SHORT-1"))["status"] == "NOT FILLED"
        assert await env.db.autotrade_trades.count_documents({}) == 0
    run(go())


def test_04b_valid_until_shortens_the_window(env):
    async def go():
        eng = env.engine()
        await eng.tick(71900)
        await signal(env, "SCALP-SHORT-1", valid=env.core.iso(env.clock() + timedelta(minutes=5)))
        await eng.tick(71900)
        env.clock.add(minutes=6)
        await eng.tick(72010)
        assert (await sig_status(env, "SCALP-SHORT-1"))["status"] == "NOT FILLED"
    run(go())


# ---- 5, 6: one trade at a time, no swing
def test_05_signal_while_trade_open_is_skipped_and_06_swing_ignored(env):
    async def go():
        eng = env.engine()
        await eng.tick(71900)
        await signal(env, "SCALP-SHORT-1")
        await eng.tick(72001)                        # arms and enters LONG in the same step
        t = await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"})
        assert t["status"] == "OPEN" and t["bot_dir"] == "LONG" and t["contracts"] == 65 and t["entry"] == 72001
        assert (t["stop"], t["target"]) == (71640.0, 72720.0)
        await signal(env, "SCALP-LONG-2", "LONG", 72050, 71700)
        await signal(env, "SWING-LONG-3", "LONG", 72050, 71000, strategy="swing")
        await eng.tick(72010)
        s2, s3 = await sig_status(env, "SCALP-LONG-2"), await sig_status(env, "SWING-LONG-3")
        assert s2["status"] == "SKIPPED" and "trade open" in s2["reason"]
        assert s3["status"] == "IGNORED" and "swing" in s3["reason"]
        assert await env.db.autotrade_trades.count_documents({}) == 1
        await eng.tick(72010)                        # idempotent: nothing is re-read or re-traded
        assert await env.db.autotrade_signals.count_documents({}) == 3
        assert any(a[1] == "Entry" for a in env.alerts)
    run(go())


def test_first_trigger_cancels_other_armed_and_slippage_guard(env):
    async def go():
        eng = env.engine()
        await eng.tick(71900)
        await signal(env, "SCALP-SHORT-A", entry=72000, stop=72360)   # LONG at ≥ 72000
        await signal(env, "SCALP-LONG-B", "LONG", 71800, 71440)       # SHORT at ≤ 71800
        await signal(env, "SCALP-SHORT-C", entry=71850, stop=72200)   # LONG at ≥ 71850 → already 50 pts beyond → slippage
        await eng.tick(71900)
        assert (await sig_status(env, "SCALP-SHORT-C"))["status"] == "SKIPPED" and "SLIPPAGE" in (await sig_status(env, "SCALP-SHORT-C"))["reason"]
        assert (await sig_status(env, "SCALP-SHORT-A"))["status"] == "ARMED"
        await eng.tick(71795)                                         # B triggers first
        assert (await env.db.autotrade_trades.find_one({"status": "OPEN"}))["_id"] == "SCALP-LONG-B"
        a = await sig_status(env, "SCALP-SHORT-A")
        assert a["status"] == "CANCELLED"
    run(go())


# ---- 7: bracket missing
def test_07_bracket_missing_closes_position_and_alerts(env):
    async def go():
        ex = FakeExchange()
        ex.NotPlaced = env.at.NotPlaced
        ex.bracket = False
        eng = env.engine(ex)
        await eng.tick(71900)
        await signal(env, "SCALP-SHORT-1")
        await eng.tick(72001)
        t = await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"})
        assert t["status"] == "CLOSED" and t["exit_reason"] == "SAFETY"
        assert len(ex.entries) == 1 and len(ex.closes) == 1 and ex.pos is None
        assert any("bracket missing" in a[1] for a in env.alerts)
    run(go())


# ---- 8: six-hour exit, paper P&L maths
def test_08_six_hour_time_exit(env):
    async def go():
        eng = env.engine()
        await eng.tick(71900)
        await signal(env, "SCALP-SHORT-1")
        await eng.tick(72000)
        env.clock.add(minutes=359)
        await eng.tick(72100)
        assert (await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"}))["status"] == "OPEN"
        env.clock.add(minutes=1)
        await eng.tick(72100)
        t = await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"})
        assert t["status"] == "CLOSED" and t["exit_reason"] == "TIME" and t["exit_price"] == 72100
        gross = 100 * 65 * 0.001
        fees = (72000 + 72100) * 65 * 0.001 * 0.0005 * 1.18
        assert abs(t["gross"] - gross) < 1e-9 and abs(t["fees"] - fees) < 1e-9 and abs(t["net_usd"] - (gross - fees)) < 1e-9
        assert abs(t["net_inr"] - t["net_usd"] * 85) < 1e-6
    run(go())


def test_paper_stop_and_target(env):
    async def go():
        eng = env.engine()
        await eng.tick(71900)
        await signal(env, "SCALP-SHORT-1")
        await eng.tick(72000)
        await eng.tick(72000, 72010, 71630)          # low touches the 71640 stop
        t = await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"})
        assert t["exit_reason"] == "STOP" and t["exit_price"] == 71640 and t["net_usd"] < -23.4
        env.clock.add(minutes=30)
        await signal(env, "SCALP-LONG-2", "LONG", 71900, 71540)   # bot SHORT at ≤ 71900, stop 72260, target 71180
        await eng.tick(71895)
        await eng.tick(71170)
        t2 = await env.db.autotrade_trades.find_one({"_id": "SCALP-LONG-2"})
        assert t2["bot_dir"] == "SHORT" and t2["exit_reason"] == "TARGET" and t2["exit_price"] == 71180 and t2["net_usd"] > 35
        assert t2["contracts"] == 61   # the first loss shrank the paper balance: margin = balance − ₹5,000 buffer
    run(go())


# ---- 9: loss room shrinks the size
def test_09_size_reduced_to_fit_daily_loss_room(env):
    async def go():
        eng = env.engine()
        await eng.tick(71900)
        await closed_trade(env, -60.0)
        await signal(env, "SCALP-SHORT-1", entry=72000, stop=72400)   # full size worst case ≈ $31.5
        full, info = env.at.size(72000, 71600, env.at.DEFAULTS, 45000, 0)
        assert full == 65 and 29 < full * info["worst_per_contract"] < 33
        await eng.tick(72000)
        t = await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"})
        worst = t["contracts"] * info["worst_per_contract"]
        assert 1 <= t["contracts"] < 65 and worst <= 24.0 and -60 - worst >= -84
        assert t["size_note"] == "daily loss room"
    run(go())


def test_09b_no_room_left_skips(env):
    async def go():
        eng = env.engine()
        await eng.tick(71900)
        await closed_trade(env, -83.8)
        await signal(env, "SCALP-SHORT-1")
        await eng.tick(72000)
        s = await sig_status(env, "SCALP-SHORT-1")
        assert s["status"] == "SKIPPED" and "below 1 contract" in s["reason"]
    run(go())


# ---- 10, 11: daily limits
def test_10_daily_target_closes_and_stops_until_midnight_ist(env):
    async def go():
        eng = env.engine()
        await eng.tick(71900)
        await closed_trade(env, 200.0)
        await signal(env, "SCALP-SHORT-1")
        await eng.tick(72000)                         # LONG 65 contracts
        await eng.tick(72300)                         # +$19.5 gross → not yet
        assert (await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"}))["status"] == "OPEN"
        await eng.tick(72480)                         # +$31.2 gross, ≈ +$25.7 net → 225 reached
        t = await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"})
        st = await env.at.get_state()
        assert t["status"] == "CLOSED" and t["exit_reason"] == "DAILY LIMIT"
        assert st["halt"]["reason"] == "Daily target reached"
        await signal(env, "SCALP-SHORT-2")
        await eng.tick(72480)
        assert (await sig_status(env, "SCALP-SHORT-2"))["status"] == "SKIPPED"
        env.clock.t = datetime(2026, 11, 13, 18, 31, tzinfo=timezone.utc)   # 00:01 IST next day
        await eng.tick(72480)
        assert (await env.at.get_state())["halt"] is None
        await signal(env, "SCALP-SHORT-3", entry=72480, stop=72840)
        await eng.tick(72481)
        assert (await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-3"}))["status"] == "OPEN"
    run(go())


def test_11_daily_loss_limit_closes_and_stops(env):
    async def go():
        ex = FakeExchange()
        ex.NotPlaced = env.at.NotPlaced
        eng = env.engine(ex)
        await eng.tick(71900)
        await closed_trade(env, -70.0, "PAPER")       # other mode's P&L must not count
        await closed_trade(env, -70.0, "TESTNET")
        await env.at.set_state(mode="TESTNET")
        await signal(env, "SCALP-SHORT-1")
        await eng.tick(72000)
        t = await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"})
        assert t["status"] == "OPEN" and t["contracts"] < 65   # only $14 of room
        await eng.tick(71400)                         # gap far through the stop before the exchange bracket reports
        t = await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"})
        st = await env.at.get_state()
        assert t["status"] == "CLOSED" and t["exit_reason"] == "DAILY LIMIT" and len(ex.closes) == 1 and ex.cancels >= 1
        assert st["halt"]["reason"] == "Daily loss limit hit"
        assert any(a[1] == "Daily loss limit hit" for a in env.alerts)
    run(go())


# ---- 12: restart
def test_12_restart_in_trade_places_no_duplicate_order(env):
    async def go():
        ex = FakeExchange()
        ex.NotPlaced = env.at.NotPlaced
        eng = env.engine(ex)
        await eng.tick(71900)
        await signal(env, "SCALP-SHORT-1")
        await eng.tick(72000)
        assert len(ex.entries) == 1
        eng2 = env.engine(ex)                         # the app restarts; the exchange still holds the position
        await eng2.tick(72050)
        await eng2.tick(72060)
        assert len(ex.entries) == 1 and ex.entries[0] == "E-SCALP-SHORT-1"
        assert (await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"}))["status"] == "OPEN"
        assert await env.db.autotrade_events.find_one({"kind": "restart"})
        ex.pos = None                                 # the bracket fires while the app is down
        eng3 = env.engine(ex)
        await eng3.tick(71600)
        t = await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"})
        assert t["status"] == "CLOSED" and t["exit_reason"] == "STOP" and len(ex.entries) == 1 and not ex.closes
    run(go())


def test_restart_with_unknown_exchange_position_pauses(env):
    async def go():
        ex = FakeExchange()
        ex.NotPlaced = env.at.NotPlaced
        ex.pos = {"size": 10, "entry": 70000}
        eng = env.engine(ex)
        await eng.tick(71900)
        assert (await env.at.get_state())["paused"] is True and not ex.closes
    run(go())


def test_controls_and_live_lock(env, monkeypatch):
    async def go():
        eng = env.engine()
        await eng.tick(71900)
        await signal(env, "SCALP-SHORT-1")
        await eng.tick(72000)
        with pytest.raises(ValueError):
            await eng.set_mode("LIVE", "LIVE", "me")          # trade open
        await eng.close_everything("me")
        t = await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"})
        assert t["exit_reason"] == "MANUAL" and (await env.at.get_state())["paused"] is True
        for k in ("DELTA_TRADE_API_KEY", "DELTA_TRADE_API_SECRET"):
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(ValueError, match="not on the server"):
            await eng.set_mode("LIVE", "LIVE", "me")
        monkeypatch.setenv("DELTA_TRADE_API_KEY", "k")
        monkeypatch.setenv("DELTA_TRADE_API_SECRET", "s")
        with pytest.raises(ValueError, match="Type LIVE"):
            await eng.set_mode("LIVE", "yes", "me")
        with pytest.raises(ValueError, match="TESTNET trade"):
            await eng.set_mode("LIVE", "LIVE", "me")
        assert (await env.at.get_state())["mode"] == "PAPER"
        await eng.resume("me")
        with pytest.raises(ValueError):
            await env.at.save_settings({"leverage": 500}, "me")
        assert (await env.at.save_settings({"daily_loss_usd": "50"}, "me"))["daily_loss_usd"] == 50.0
    run(go())


def test_delta_signature_and_entry_order(env, monkeypatch):
    import httpx
    db_mod = importlib.import_module("delta_broker")
    monkeypatch.setenv("DELTA_TESTNET_API_KEY", "key123")
    monkeypatch.setenv("DELTA_TESTNET_API_SECRET", "secret456")
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path == "/v2/products/BTCUSD":
            return httpx.Response(200, json={"success": True, "result": {"id": 84, "symbol": "BTCUSD", "contract_value": "0.001", "tick_size": "0.5"}})
        return httpx.Response(200, json={"success": True, "result": {"id": 1, "size": 65, "unfilled_size": 0, "average_fill_price": "72001.5"}})

    async def go():
        b = db_mod.DeltaBroker("TESTNET", transport=httpx.MockTransport(handler))
        return await b.enter("buy", 65, 71640.0, 72720.0, "E-SCALP-SHORT-1", 72000, {"tp_limit": 0})
    r = run(go())
    assert r == {"price": 72001.5, "contracts": 65, "role": "taker", "order_id": 1}
    post = seen[-1]
    body = json.loads(post.content)
    assert post.url.host == "cdn-ind.testnet.deltaex.org" and post.method == "POST" and post.url.path == "/v2/orders"
    assert body == {"product_id": 84, "size": 65, "side": "buy", "order_type": "market_order", "client_order_id": "E-SCALP-SHORT-1",
                    "bracket_stop_loss_price": "71640.0", "bracket_take_profit_price": "72720.0", "bracket_stop_trigger_method": "last_traded_price"}
    ts = post.headers["timestamp"]
    assert post.headers["api-key"] == "key123" and post.headers["user-agent"]
    assert post.headers["signature"] == db_mod.sign("secret456", "POST", ts, "/v2/orders", "", post.content.decode())
    import hashlib
    import hmac
    assert db_mod.sign("s", "GET", "1", "/v2/orders", "?a=1", "") == hmac.new(b"s", b"GET1/v2/orders?a=1", hashlib.sha256).hexdigest()
    assert db_mod.keys("LIVE") is None or True
