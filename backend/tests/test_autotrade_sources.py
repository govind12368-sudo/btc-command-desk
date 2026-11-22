"""Auto-Trade takes only Flush Short and Candle Runs trades (admin request 21 Nov 2026)."""
import asyncio
import importlib
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("mongomock_motor")
import motor.motor_asyncio as mm  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_db")
T0 = datetime(2026, 11, 22, 6, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t

    def add(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "autotrade", "delta_broker", "daytype", "broker_keys", "flush", "runs", "regime", "plan_a", "support"):
        sys.modules.pop(m, None)
    core = importlib.import_module("core")
    at = importlib.import_module("autotrade")
    fl, rn = importlib.import_module("flush"), importlib.import_module("runs")
    clock, alerts = Clock(), []

    async def alert(mode, title, text):
        alerts.append((mode, title, text))
    eng = at.Engine(clock=clock, alert=alert)
    return type("Env", (), {"core": core, "at": at, "fl": fl, "rn": rn, "clock": clock, "eng": eng, "db": core.db})


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


def runs_pos(t, period="Live"):
    return {"id": f"{t}-SHORT", "entry_t": t, "period": period, "side": "SHORT", "entry": 72000.0, "sl": 73000.0, "d": 1000.0, "members": ["P&L #5", "P&L #1"],
            "parts": [{"wb": "P&L #5", "rr": 2.0, "tp": 70000.0}, {"wb": "P&L #1", "rr": 3.0, "tp": 69000.0}]}


def test_candle_runs_market_entry_largest_target_7_day_exit(env):
    async def go():
        await env.eng.tick(72000)                                     # first tick: Auto-Trade starts here; older positions are never taken
        env.clock.add(minutes=10)
        t = int(env.clock().timestamp()) - 300
        env.rn.STATE["data"] = {"positions": [runs_pos(t), runs_pos(t - 86400 * 3, "Replay")]}
        await env.db.signals.insert_one({"_id": "SCALP-SHORT-1", "strategy": "scalp", "direction": "SHORT", "entry": 72000, "stop": 72300, "target": 71500,
                                         "captured_at": env.core.iso(env.clock())})
        await env.eng.tick(72100)                                     # 100 points worse than entry: inside the 200-point guard (0.2 x 1000)
        sigs = [s async for s in env.db.autotrade_signals.find({})]
        assert [s["_id"] for s in sigs] == [f"RUNS-{t}-SHORT"]          # replay rows and scalp signals are not taken
        tr = await env.db.autotrade_trades.find_one({"_id": f"RUNS-{t}-SHORT"})
        assert tr["status"] == "OPEN" and tr["bot_dir"] == "SHORT" and tr["stop"] == 73000 and tr["target"] == 69000 and tr["plan"] == "RUNS"
        assert tr["time_exit_min"] == 7 * 24 * 60
        env.clock.add(minutes=7 * 24 * 60 - 1)
        await env.eng.tick(71900)
        assert (await env.db.autotrade_trades.find_one({"_id": tr["_id"]}))["status"] == "OPEN"
        env.clock.add(minutes=1)
        await env.eng.tick(71900)
        x = await env.db.autotrade_trades.find_one({"_id": tr["_id"]})
        assert x["status"] == "CLOSED" and x["exit_reason"] == "TIME"
    run(go())


def test_candle_runs_slippage_guard(env):
    async def go():
        await env.eng.tick(72000)
        env.clock.add(minutes=10)
        t = int(env.clock().timestamp()) - 300
        env.rn.STATE["data"] = {"positions": [runs_pos(t)]}
        await env.eng.tick(71700)                                     # SHORT entry 72000, price already 300 lower: chasing, skip
        s = await env.db.autotrade_signals.find_one({"_id": f"RUNS-{t}-SHORT"})
        assert s["status"] == "SKIPPED" and "SLIPPAGE" in s["reason"] and not await env.db.autotrade_trades.find_one({})
    run(go())


def test_flush_short_waits_for_entry_and_exits_after_6_hours(env):
    async def go():
        await env.eng.tick(71900)
        env.clock.add(minutes=10)
        cap = int(env.clock().timestamp()) - 60
        env.fl.STATE["data"] = {"rows": [{"signal_id": "SCALP-SHORT-9", "cap_t": cap, "period": "Live", "trade_side": "SHORT", "entry": 72000.0, "d": 400.0, "reason": "ok"},
                                         {"signal_id": "SCALP-SHORT-8", "cap_t": cap, "period": "Live", "rule": "No trade", "entry": 72000.0, "d": 400.0}]}
        await env.eng.tick(71900)
        s = await env.db.autotrade_signals.find_one({"_id": "FLUSH-SCALP-SHORT-9"})
        assert s["status"] == "ARMED" and s["stop"] == 72400 and s["target"] == 71500 and await env.db.autotrade_signals.count_documents({}) == 1
        await env.eng.tick(72000)                                     # price comes up to the SHORT entry
        tr = await env.db.autotrade_trades.find_one({"_id": "FLUSH-SCALP-SHORT-9"})
        assert tr["status"] == "OPEN" and tr["time_exit_min"] == 360 and tr["plan"] == "FLUSH"
        env.clock.add(minutes=360)
        await env.eng.tick(71950)
        assert (await env.db.autotrade_trades.find_one({"_id": tr["_id"]}))["exit_reason"] == "TIME"
    run(go())


def test_only_flush_and_runs_alert(env):
    import alert_mode
    assert alert_mode.ALERT_SOURCES == {"flush", "runs", "autotrade", "elite"}   # Elite 4 Edge emails added 23 Nov 2026
    assert not alert_mode.source_on("scalp") and not alert_mode.source_on("regime") and not alert_mode.source_on("observer")
    assert not alert_mode.source_on("daily_recap")


def test_candle_runs_emails_once(env, monkeypatch):
    async def go():
        import alert_mode
        import alerts
        sent = []

        async def fake_send(**k):
            sent.append(k["subject"]); return "id"
        monkeypatch.setattr(alerts, "send_email", fake_send)

        async def armed():
            return {"effective_armed": True, "kill_switch": False, "channels": {"email": True}}
        monkeypatch.setattr(alert_mode, "get_mode", armed)
        monkeypatch.setenv("ALERT_EMAIL", "me@example.com")
        monkeypatch.setenv("FRONTEND_URL", "https://x.example")
        now = int(env.clock().timestamp())
        p = {**runs_pos(now - 600), "entry_ist": "2026-11-22 11:20", "market": "Down", "chg30": -0.2, "reason": "4 red", "time_exit_ist": "2026-11-29 11:20",
             "parts": [{"strategy": "pl5", "wb": "P&L #5", "rr": 2.0, "tp": 70000.0, "status": "CLOSED", "exit_t": now - 60, "exit_ist": "x", "exit_price": 70000.0,
                        "result": "Target", "net": 100.0, "share": 0.5},
                       {"strategy": "pl1", "wb": "P&L #1", "rr": 3.0, "tp": 69000.0, "status": "OPEN"}]}
        await env.rn.notify([p, {**p, "id": "old", "period": "Replay"}], now)
        await env.rn.notify([p], now)
        assert len(sent) == 2 and "Candle Runs — SHORT" in sent[0] and "P&L #5 Target" in sent[1]
    run(go())
