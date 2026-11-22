"""Elite 4 Edge: one trade at a time for the account, priority P&L #1, Row 9, P&L #5, Row 18; rule filters; paper engine;
drawdown stop; Stop all; real-money switch guards (owner spec 23 Nov 2026)."""
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
T0 = datetime(2026, 11, 23, 8, 0, 20, tzinfo=timezone.utc)


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
    for k in ("ELITE_DELTA_API_KEY", "ELITE_DELTA_API_SECRET", "DELTA_TRADE_API_KEY", "DELTA_TRADE_API_SECRET",
              "ELITE_DELTA_TESTNET_API_KEY", "ELITE_DELTA_TESTNET_API_SECRET", "DELTA_TESTNET_API_KEY", "DELTA_TESTNET_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    for m in ("core", "autotrade", "delta_broker", "daytype", "broker_keys", "flush", "runs", "trio", "elite", "regime", "plan_a", "support"):
        sys.modules.pop(m, None)
    core = importlib.import_module("core")
    el = importlib.import_module("elite")
    clock = Clock()
    eng = el.Engine(clock=clock, notify=None)
    return type("Env", (), {"core": core, "el": el, "clock": clock, "eng": eng, "db": core.db})


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


def cand(key, t, side=1, d=500.0, ch=0.0, ok=True, skip=""):
    return {"t": t, "key": key, "sig": (t, side, d, f"{key} test signal", ch), "ok": ok, "skip": skip}


def flat_b5(t0, n, px=60000.0):
    return [{"t": t0 + i * 300, "o": px, "h": px + 10, "l": px - 10, "c": px} for i in range(n)]


# ---------------------------------------------------------------- pure rules and book
def test_rules_range_and_trend(env):
    el = env.el
    assert el.rule_ok("sma", (0, 1, 1, "", 0.01))[0] is False                  # Range market: Row 9 skipped
    assert el.rule_ok("sma", (0, 1, 1, "", 0.08))[0] is True
    assert el.rule_ok("sma", (0, 1, 1, "", None))[0] is True                   # first 30 days: no filter
    assert el.rule_ok("cci", (0, -1, 1, "", 0.08))[0] is False                 # SELL in an Up market
    assert el.rule_ok("cci", (0, 1, 1, "", -0.08))[0] is False                 # BUY in a Down market
    assert el.rule_ok("cci", (0, 1, 1, "", 0.08))[0] is True
    assert el.rule_ok("pl1", (0, -1, 1, "", 0.5))[0] is True and el.rule_ok("pl5", (0, 1, 1, "", -0.5))[0] is True
    assert el.ORDER == ["pl1", "sma", "pl5", "cci"] and "pl5" in el.STRATS     # P&L #5 kept in the code


def test_book_priority_and_one_at_a_time(env):
    el = env.el
    t0 = 1_790_000_000 - 1_790_000_000 % 14400
    b5 = flat_b5(t0, 400)
    # same candle: Row 18, P&L #5, Row 9 and P&L #1 all fire -> P&L #1 is taken, the other three are skipped
    cands = [cand(k, t0 + 14400) for k in ("cci", "pl5", "sma", "pl1")]
    cands.sort(key=lambda g: (g["t"], el.ORDER.index(g["key"])))
    rows, skips = el.book(cands, b5, 0, 60000.0)
    assert [r["strategy"] for r in rows] == ["pl1"]
    assert {s["key"] for s in skips} == {"sma", "pl5", "cci"} and all("trade already open" in s["reason"] for s in skips)
    # a rule-blocked signal never blocks the next one
    cands = [cand("sma", t0 + 3600, ok=False, skip="skipped – Range market"), cand("cci", t0 + 14400)]
    rows, skips = el.book(cands, b5, 0, 60000.0)
    assert [r["strategy"] for r in rows] == ["cci"] and skips[0]["reason"].startswith("skipped – Range")


def test_book_next_signal_after_close(env):
    el = env.el
    t0 = 1_790_000_000 - 1_790_000_000 % 14400
    b5 = flat_b5(t0, 600)
    k = (t0 + 14400 - t0) // 300 + 3                                    # target of a 1:3 LONG (stop 500) hit 15 min after entry
    b5[k] = {**b5[k], "h": 61600.0}
    cands = [cand("pl1", t0 + 14400), cand("sma", t0 + 14400 + 3600), cand("pl5", t0 + 14400 + 1800)]
    cands.sort(key=lambda g: g["t"])
    rows, skips = el.book(cands, b5, 0, 60000.0)
    assert rows[0]["result"] == "Target" and rows[0]["exit_t"] <= t0 + 14400 + 1800
    assert [r["strategy"] for r in rows] == ["pl1", "pl5"]               # P&L #5 at +30 min is free; Row 9 at +60 min waits behind it
    assert skips and skips[0]["key"] == "sma"


def test_tables_month_year_total(env):
    el = env.el
    r = lambda k, t, net: {"strategy": k, "status": "CLOSED", "entry_t": t, "exit_t": t + 3600, "net": net, "gross": net + 12, "fees": 12}
    rows = [r("pl1", el.runs._ts("2025-12-30 10:00"), 300), r("sma", el.runs._ts("2026-01-05 10:00"), -100), r("cci", el.runs._ts("2026-01-06 10:00"), -50)]
    tb = el.tables(rows)
    assert tb["combined"]["trades"] == 3 and tb["combined"]["net"] == 150 and tb["combined"]["max_dd"] == -150
    assert [y["year"] for y in tb["yearly"]] == ["2025", "2026", "Total till date"] and tb["yearly"][-1]["net"] == 150
    jan = next(m for m in tb["monthly"] if m["month"] == "2026-01")
    assert jan["net"] == -150 and jan["by_strategy"]["Row 9"] == -100 and jan["by_strategy"]["P&L #5"] == 0


# ---------------------------------------------------------------- live paper engine
def test_paper_engine_takes_first_signal_skips_while_open_then_target(env):
    el, eng, clock = env.el, env.eng, env.clock

    async def go():
        await eng.tick(60000.0)
        tc = int(clock().timestamp()) - 20
        await eng.on_candles([cand("pl1", tc - 14400)], tc - 3600)            # first start: old signals are never traded
        assert await env.db.elite_trades.count_documents({}) == 0
        await eng.on_candles([cand("cci", tc), cand("pl1", tc), cand("sma", tc)], tc)
        t = await env.db.elite_trades.find_one({"status": "OPEN"})
        assert t["strategy"] == "pl1" and t["entry"] == 60000.0 and t["size"] == 0.166 and t["sl"] == 59500.0 and t["tp"] == 61500.0
        sk = {s["key"]: s for s in [x async for x in env.db.elite_signals.find({"status": "SKIPPED"})]}
        assert set(sk) == {"sma", "cci"} and "trade already open" in sk["sma"]["reason"]
        clock.add(minutes=30)
        await eng.tick(61500.0, 61510.0, 61400.0)
        t = await env.db.elite_trades.find_one({"_id": t["_id"]})
        assert t["status"] == "CLOSED" and t["result"] == "Target" and t["net"] == pytest.approx(1500 * 0.166 - el.fee(60000, 0.166) - el.fee(61500, 0.166))
        tc2 = tc + 3600
        clock.add(minutes=31)
        await eng.on_candles([cand("sma", tc2, side=-1, ch=0.10)], tc2)
        assert (await env.db.elite_trades.find_one({"status": "OPEN"}))["strategy"] == "sma"
    run(go())


def test_stop_first_and_time_exit(env):
    el, eng, clock = env.el, env.eng, env.clock

    async def go():
        await eng.tick(60000.0)
        tc = int(clock().timestamp()) - 20
        await eng.on_candles([], tc - 300)
        await eng.on_candles([cand("pl5", tc, side=-1)], tc)
        await eng.tick(60100.0, 60600.0, 58900.0)                              # both touched in one tick: stop counts first
        t = await env.db.elite_trades.find_one({})
        assert t["result"] == "Stop" and t["exit_price"] == 60500.0
        tc2 = tc + 14400
        clock.add(hours=4)
        await eng.on_candles([cand("cci", tc2, side=1, ch=0.0)], tc2)
        clock.add(hours=168, seconds=1)
        await eng.tick(60200.0)
        t = await env.db.elite_trades.find_one({"strategy": "cci"})
        assert t["result"] == "Time exit" and t["exit_price"] == 60200.0
    run(go())


def test_late_signal_and_rule_skips(env):
    eng, clock = env.eng, env.clock

    async def go():
        await eng.tick(60000.0)
        tc = int(clock().timestamp()) - 20
        await eng.on_candles([], tc - 3600)
        await eng.on_candles([cand("sma", tc - 1800, ok=False, skip="skipped – Range market (BTC +1.0% over 30 days)")], tc - 1800)
        clock.add(minutes=20)
        await eng.on_candles([cand("pl1", tc)], tc)
        rows = {s["key"]: s["reason"] async for s in env.db.elite_signals.find({})}
        assert rows["sma"].startswith("skipped – Range") and "15 minutes" in rows["pl1"]
        assert await env.db.elite_trades.count_documents({}) == 0
    run(go())


def test_drawdown_stop_closed_plus_open(env, monkeypatch):
    el, eng, clock = env.el, env.eng, env.clock
    monkeypatch.setattr(el, "DD_LIMIT", -100.0)

    async def go():
        await eng.tick(60000.0)
        tc = int(clock().timestamp()) - 20
        await eng.on_candles([], tc - 300)
        await eng.on_candles([cand("pl1", tc, d=5000.0)], tc)
        await eng.tick(59700.0)                                                 # open loss about -$62: still trading
        assert not (await el.get_state()).get("halt")
        await eng.tick(59400.0)                                                 # open loss about -$112 <= -$100 from the 0 peak
        st = await el.get_state()
        assert st["halt"]["kind"] == "drawdown"
        t = await env.db.elite_trades.find_one({})
        assert t["status"] == "CLOSED" and t["result"] == "Drawdown stop"
        clock.add(hours=4)
        await eng.on_candles([cand("pl1", tc + 14400)], tc + 14400)
        s = await env.db.elite_signals.find_one({"_id": f"pl1-{tc + 14400}"})
        assert s["status"] == "SKIPPED" and "trading stopped" in s["reason"]
    run(go())


def test_stop_all_and_resume(env):
    el, eng, clock = env.el, env.eng, env.clock

    async def go():
        await eng.tick(60000.0)
        tc = int(clock().timestamp()) - 20
        await eng.on_candles([], tc - 300)
        await eng.on_candles([cand("sma", tc, ch=0.1)], tc)
        await eng.stop_all("owner@x")
        t = await env.db.elite_trades.find_one({})
        assert t["status"] == "CLOSED" and t["result"] == "Stop all" and (await el.get_state())["halt"]["kind"] == "manual"
        await eng.resume("owner@x")
        st = await el.get_state()
        assert st["halt"] is None and st["peak"] == pytest.approx(t["net"], abs=0.01)
    run(go())


# ---------------------------------------------------------------- real-money switch
def test_real_money_switch_guards(env, monkeypatch):
    el, eng = env.el, env.eng
    calls = []

    class Fake:
        async def preflight(self, cfg):
            calls.append(cfg)
            return {"available_inr": 500000.0}

    eng.broker_factory = lambda mode: el.PaperBroker() if mode == "PAPER" else Fake()

    async def go():
        assert (await el.get_state())["mode"] == "PAPER"                         # OFF by default
        with pytest.raises(ValueError, match="Type TESTNET"):
            await eng.set_mode("TESTNET", "", "owner@x")
        with pytest.raises(ValueError, match="server environment"):
            await eng.set_mode("TESTNET", "TESTNET", "owner@x")
        monkeypatch.setenv("ELITE_DELTA_TESTNET_API_KEY", "k"); monkeypatch.setenv("ELITE_DELTA_TESTNET_API_SECRET", "s")
        await env.db.autotrade.insert_one({"_id": "state", "mode": "TESTNET"})
        with pytest.raises(ValueError, match="Auto-Trade is in TESTNET"):
            await eng.set_mode("TESTNET", "TESTNET", "owner@x")
        await env.db.autotrade.update_one({"_id": "state"}, {"$set": {"mode": "LIVE"}})
        await eng.set_mode("TESTNET", "TESTNET", "owner@x")                   # Auto-Trade LIVE is a different account from the testnet
        assert (await el.get_state())["mode"] == "TESTNET" and calls and calls[0]["leverage"] == 10
        monkeypatch.setenv("ELITE_DELTA_API_KEY", "k"); monkeypatch.setenv("ELITE_DELTA_API_SECRET", "s")
        with pytest.raises(ValueError, match="Auto-Trade is in LIVE"):            # Elite can be Live only while Auto-Trade is not Live
            await eng.set_mode("LIVE", "LIVE", "owner@x")
        await env.db.autotrade.update_one({"_id": "state"}, {"$set": {"mode": "PAPER"}})
        with pytest.raises(ValueError, match="TESTNET trade"):
            await eng.set_mode("LIVE", "LIVE", "owner@x")
        # Auto-Trade cannot leave PAPER while Elite uses the Delta account
        at = importlib.import_module("autotrade")
        with pytest.raises(ValueError, match="Elite 4 Edge is in TESTNET"):
            await at.Engine(clock=env.clock).set_mode("TESTNET", "", "owner@x")
        await eng.set_mode("PAPER", None, "owner@x")
        assert (await el.get_state())["mode"] == "PAPER"
    run(go())


def test_env_keys_only(env, monkeypatch):
    el = env.el
    assert el.env_keys("LIVE") is None
    monkeypatch.setenv("DELTA_TRADE_API_KEY", "a"); monkeypatch.setenv("DELTA_TRADE_API_SECRET", "b")
    assert el.env_keys("LIVE") == ("a", "b")
    monkeypatch.setenv("ELITE_DELTA_API_KEY", "c"); monkeypatch.setenv("ELITE_DELTA_API_SECRET", "d")
    assert el.env_keys("LIVE") == ("c", "d")
