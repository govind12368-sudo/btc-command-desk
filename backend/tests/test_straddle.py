"""Straddle bot (owner's spec, 26 Nov 2026): replay of the owner's workbook, the rules, the entry / exit engine and the guards."""
import asyncio
import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx
import pytest

pytest.importorskip("mongomock_motor")
import motor.motor_asyncio as mm  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_db")
run = lambda c: asyncio.new_event_loop().run_until_complete(c)  # noqa: E731
C, P, K = "C-BTC-61000-261126", "P-BTC-61000-261126", 61000.0


# ---------------- the maths ----------------
def test_replay_reproduces_the_owner_workbook_to_the_dollar():
    import straddle_core as sc
    data = sc.load_seed()
    want = {"mark_fixed": 4361.89, "mark_compound": 55572.37, "trade_fixed": 4965.00, "trade_compound": 95922.09}
    for key, net in want.items():
        rows = sc.backtest(data, key)
        m = sc.metrics(rows, 1000.0, rows[0]["date"], rows[-1]["date"])
        assert abs(m["net"] - net) < 1.0, key
        assert m["days"] == m["trades"] == 878 and m["longest_streak"] == 6
    m = sc.metrics(sc.backtest(data, "mark_fixed"), 1000.0)
    assert (m["wins"], m["losses"], m["stop_exits"], m["expiry_exits"], m["max_lots"]) == (562, 316, 45, 833, 491)
    assert round(m["win_ratio"], 4) == 0.6401 and round(m["sharpe"], 2) == 3.53 and round(m["cagr"], 3) == 1.010
    assert round(m["max_dd"], 2) == -246.61 and round(m["profit_factor"], 3) == 1.630 and round(m["breakeven_win_ratio"], 4) == 0.5219
    mo = sc.monthly(sc.backtest(data, "mark_fixed"), 1000.0)
    assert len(mo) == 29 and round(mo[0]["net"], 2) == 466.29 and round(mo[-1]["after"], 2) == 5361.89 and sum(x["stops"] for x in mo) == 45


def test_lot_sizing_fee_formula_stop_and_pnl():
    import straddle_core as sc
    assert sc.lots_for(1000, 25, 62775) == 398 and sc.lots_for(1000, 25, 87738) == 284 and sc.lots_for(0, 25, 60000) == 0
    # fee = min(rate x notional, cap x premium) x 1.18
    assert abs(sc.leg_fee(0.0003, 0.1, 62750.3, 40.34634806, 0.398, 0.18) - 0.1 * 40.34634806 * 0.398 * 1.18) < 1e-12      # the cap is lower
    assert abs(sc.leg_fee(0.0001, 0.035, 61000, 900.0, 0.4, 0.18) - 0.0001 * 61000 * 0.4 * 1.18) < 1e-12                    # the rate is lower
    assert sc.stop_hit(200.0, 100.0, 2.0) and not sc.stop_hit(199.99, 100.0, 2.0) and sc.stop_level(120.5, 2.0) == 241.0
    assert sc.settle_values(62800, 62683.8) == (0.0, pytest.approx(116.2)) and sc.settle_values(62800, 62802.4) == (pytest.approx(2.4), 0.0)
    # the workbook's first day, by hand: 398 lots, expiry, put in the money
    d = {"spot": 62750.3, "entry_call": 40.34634806, "entry_put": 78.41537374, "exit_reason": "Expiry", "exit_call": 0.0, "exit_put": 116.1827586206928,
         "exit_spot": 62683.81724137931, "maker": 0.0003, "taker": 0.0003, "cap": 0.1}
    m = sc.pnl(d, 398, 0.18)
    assert round(m["premium"], 4) == 47.2672 and round(m["cost"], 4) == 46.2407 and round(m["entry_fees"], 4) == 5.5775 and round(m["exit_fees"], 4) == 5.4564
    assert round(m["net"], 4) == -10.0075


def test_journal_handles_skips_leg_risk_and_compounding():
    import straddle_core as sc
    base = {"spot": 60000.0, "strike": 60000.0, "entry_call": 60.0, "entry_put": 60.0, "exit_reason": "Expiry", "exit_time": "17:30", "exit_call": 0.0, "exit_put": 0.0,
            "exit_spot": 60000.0, "maker": 0.0001, "taker": 0.0001, "cap": 0.035}
    days = [{**base, "date": "2026-12-01"}, {"date": "2026-12-02", "skip_reason": "spread too wide", "leg_risk_cost": 3.0}, {**base, "date": "2026-12-03"}]
    cfg = {"starting_capital": 1000.0, "leverage": 25.0, "gst": 0.18, "stop_multiple": 2.0}
    rows = sc.journal(days, cfg, "compounded")
    assert [r["result"] for r in rows] == ["Win", "Skipped", "Win"] and rows[1]["lots"] == 0
    assert rows[2]["sizing_equity"] == pytest.approx(rows[0]["equity"] - 3.0) and rows[2]["lots"] == sc.lots_for(rows[2]["sizing_equity"], 25, 60000.0)
    m = sc.metrics(rows, 1000.0)
    assert m["trades"] == 2 and m["skipped"] == 1 and m["final_equity"] == pytest.approx(rows[-1]["equity"])
    assert sc.journal(days, cfg, "fixed")[2]["sizing_equity"] == 1000.0


def test_workbook_checks_give_plain_reasons():
    import straddle_core as sc
    with pytest.raises(ValueError, match="not an Excel"):
        sc.parse_workbook(b"hello")
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.title = "Other"
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError, match="J_Mark_Fixed is missing"):
        sc.parse_workbook(buf.getvalue())


# ---------------- the engine, on a simulated exchange ----------------
class Env:
    pass


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "straddle", "straddle_exec", "delta_broker", "autotrade"):
        sys.modules.pop(m, None)
    st, sx = importlib.import_module("straddle"), importlib.import_module("straddle_exec")
    e = Env()
    e.st, e.sx, e.sc, e.db = st, sx, importlib.import_module("straddle_core"), importlib.import_module("core").db
    e.now = [datetime(2026, 11, 26, 11, 29, 59, tzinfo=timezone.utc)]
    e.mono, e.alerts, e.on_wait = [0.0], [], []

    async def fake_event(text, level="info", alert=None, mode=None, cfg=None):
        e.alerts.append((alert, text))
    monkeypatch.setattr(st, "event", fake_event)

    class FakeFeed(sx.Feed):
        async def start(self, symbols):
            self.symbols = list(symbols)

        async def stop(self):
            pass

        async def rest_refresh(self, symbols=None):
            for s in (symbols or self.symbols):
                if s in self.q:
                    self.q[s]["at"] = sx.time.time()
            self.spot_at = sx.time.time()
    e.feed = FakeFeed("LIVE")
    e.feed.connected = True

    async def sleep(sec):
        e.now[0] += timedelta(seconds=sec)
        e.mono[0] += sec
    eng = st.Engine(clock=lambda: e.now[0], sleep=sleep, feed_factory=lambda env_: e.feed)
    eng.mono = lambda: e.mono[0]

    async def wait_update(timeout):
        await sleep(timeout)
        if e.on_wait:
            e.on_wait.pop(0)()
    eng.wait_update = wait_update
    eng.feed, eng.broker = e.feed, sx.PaperBroker(e.feed)
    e.eng, e.cfg = eng, dict(st.DEFAULTS)
    leg = lambda s: {"symbol": s, "product_id": 1 if s == C else 2, "tick": 0.1, "maker": 0.0001, "taker": 0.0001, "cap": 0.035, "lot": 0.001, "tradable": True,  # noqa: E731
                     "lev": 200.0, "size_limit": 50000}
    e.legs = {K: {"call": leg(C), "put": leg(P)}}
    e.doc = lambda: {"_id": "2026-11-26:PAPER", "date": "2026-11-26", "mode": "PAPER", "status": "PREFLIGHT"}
    return e


def market(e, call=(100.0, 99.5, 100.5), put=(100.0, 99.5, 100.5), depth=5000, spot=61010.0):
    """(mark, bid, ask) per leg with `depth` lots on each side."""
    for s, (mark, bid, ask) in ((C, call), (P, put)):
        e.feed.put_quote(s, mark, bid, ask, depth, depth, spot)
        e.feed.put_book(s, [(bid, depth)] if bid else [], [(ask, depth)] if ask else [])


def test_entry_sells_both_legs_at_the_bid_never_at_mark_and_places_backup_stops(env):
    async def go():
        market(env)
        doc = env.doc()
        assert await env.eng.enter(doc, env.legs, env.cfg)
        assert doc["status"] == "OPEN" and doc["strike"] == K and doc["lots"] == env.sc.lots_for(1000, 25, 61010.0) == 409
        assert doc["entry_call"] == 99.5 and doc["entry_put"] == 99.5                      # paper fills at the real bid, not at mark 100
        assert doc["entry_slip"] == pytest.approx(0.005) and not doc["size_reduced"] and not doc["liquidity_cap"]
        assert env.eng.broker.pos == {C: -409, P: -409}
        assert env.eng.broker.stops[C]["stop"] == pytest.approx(597.0) and env.eng.broker.stops[P]["size"] == 409      # 3 x P on each leg, on the exchange side
        assert env.mono[0] <= 4.0                                                          # one 3-second re-price was enough
        saved = await env.db.straddle_trades.find_one({"_id": doc["_id"]})
        assert saved["status"] == "OPEN" and saved["maker"] == 0.0001 and saved["cap"] == 0.035
    run(go())


def test_entry_never_sells_below_mark_minus_max_slip_and_skips_without_a_fill(env):
    async def go():
        market(env, call=(100.0, 95.0, 105.0), put=(100.0, 95.0, 105.0))                 # bids 5% under mark; the floor is 2%
        env.cfg["max_spread_pct"] = 20.0
        doc = env.doc()
        with pytest.raises(env.st.Skip, match="size is below 1 lot"):                      # no bid depth inside 2% -> nothing may be sold
            await env.eng.enter(doc, env.legs, env.cfg)
        assert env.eng.broker.pos == {}
    run(go())


def test_single_leg_fill_is_bought_back_at_once_and_the_day_is_skipped(env):
    async def go():
        market(env)
        doc = env.doc()
        real_place = env.eng.broker.place

        async def place(leg, side, size, price=None, tif="gtc", reduce_only=False, coid=None):
            if leg["symbol"] == P and side == "sell":
                env.feed.put_book(P, [], [(100.5, 5000)])                                 # the put's bids vanish as the order arrives
            return await real_place(leg, side, size, price, tif, reduce_only, coid)
        env.eng.broker.place = place
        with pytest.raises(env.st.Skip, match="only one leg filled"):
            await env.eng.enter(doc, env.legs, env.cfg)
        assert env.eng.broker.pos[C] == 0 and env.eng.broker.pos.get(P, 0) == 0           # flat: never a naked leg
        assert doc["leg_risk"][0]["leg"] == "call" and doc["leg_risk"][0]["sold"] == 99.5 and doc["leg_risk"][0]["bought"] == 100.5
        assert doc["leg_risk_cost"] > 409 * 0.001 * 1.0                                   # the 1.0 lost per BTC plus fees
        assert env.mono[0] >= env.cfg["entry_deadline_sec"]
    run(go())


def test_liquidity_cap_spread_and_stale_data_rules(env):
    async def go():
        market(env, depth=120)
        doc = env.doc()
        assert await env.eng.enter(doc, env.legs, env.cfg)
        assert doc["lots"] == 120 and doc["liquidity_cap"] and doc["lots_wanted"] == 409
        assert any(a == "Size reduced" and "SIZE REDUCED" in t for a, t in env.alerts)
        env.eng.broker = env.sx.PaperBroker(env.feed)
        market(env, call=(100.0, 90.0, 101.0))
        with pytest.raises(env.st.Skip, match="call bid-ask spread is 11.0%"):
            await env.eng.enter(env.doc(), env.legs, env.cfg)
        market(env)
        env.feed.q[C]["at"] -= 10

        async def dead(symbols=None):
            pass
        env.feed.rest_refresh = dead
        env.feed.spot_at = env.sx.time.time()
        with pytest.raises(env.st.Skip, match="call price data is older than 2 s"):
            await env.eng.enter(env.doc(), env.legs, env.cfg)
    run(go())


def test_margin_estimate_and_max_lots_reduce_the_size(env):
    market(env)
    lots, notes = env.eng.plan_size(env.cfg, "PAPER", 1000.0, 61010.0, env.legs[K], available=100.0)
    assert lots == int(100.0 / (2 * 61010.0 * 0.001 / 200)) == 163 and notes["size_reduced"] and "margin" in notes["why"][0]
    lots, notes = env.eng.plan_size({**env.cfg, "max_lots": 50.0}, "PAPER", 1000.0, 61010.0, env.legs[K], available=1000.0)
    assert lots == 50 and notes["size_reduced"]
    lots, _ = env.eng.plan_size(env.cfg, "LIVE", 1000.0, 61010.0, env.legs[K], available=1000.0)
    assert lots == env.sc.lots_for(1000, 5, 61010.0) == 81                                 # LIVE starts at leverage 5 until the owner lifts it


async def _open(env, **cfg):
    env.cfg.update(cfg)
    market(env)
    doc = env.doc()
    assert await env.eng.enter(doc, env.legs, env.cfg)
    env.now[0] = datetime(2026, 11, 26, 11, 30, 5, tzinfo=timezone.utc)
    return doc


def test_parity_stop_waits_for_the_minute_close_then_buys_both_back(env):
    async def go():
        doc = await _open(env)                                                            # P = 199.0, stop at 398.0
        env.on_wait.append(lambda: market(env, call=(390.0, 389.0, 391.0), put=(12.0, 11.5, 12.5)))      # 402 mid-minute: above the stop
        t = {}
        orig = env.eng.flatten

        async def spy(d, cfg, why):
            t["at"], t["why"] = env.now[0], why
            await orig(d, cfg, why)
        env.eng.flatten = spy
        assert await env.eng.monitor(doc, env.cfg) == "stop"
        assert t["at"].minute == 31 and t["at"].second < 1 and "17:01 close" in t["why"]  # not before the 1-minute close
        assert doc["status"] == "CLOSED" and doc["exit_reason"] == "Stop" and doc["exit_time"] == "17:01"
        assert (doc["exit_call"], doc["exit_put"]) == (391.0, 12.5)                        # bought at the real ask
        assert doc["exit_slip"] == pytest.approx((403.5 - 402.0) / 402.0) and doc["parity_note"].startswith("1-minute rule: stop at 17:01")
        assert env.eng.broker.pos == {C: 0, P: 0} and env.eng.broker.stops == {}
        rows = await env.st.live_journal("PAPER", env.cfg)
        m = env.sc.pnl(doc, 409, 0.18)
        assert rows[-1]["net"] == pytest.approx(m["net"]) and rows[-1]["net"] < 0 and rows[-1]["exit_over_p"] == pytest.approx(403.5 / 199.0)
    run(go())


def test_tick_stop_acts_at_once_and_records_what_the_minute_rule_would_do(env):
    async def go():
        doc = await _open(env, stop_mode="tick")
        env.on_wait.append(lambda: market(env, call=(390.0, 389.0, 391.0), put=(12.0, 11.5, 12.5)))
        assert await env.eng.monitor(doc, env.cfg) == "stop"
        assert doc["exit_time"] == "17:00" and env.now[0].minute == 30                     # inside the first minute
    run(go())


def test_daily_loss_guard_kill_switch_and_backup_stop(env):
    async def go():
        doc = await _open(env, max_daily_loss_pct=2.0)                                    # $20 on $1,000
        env.on_wait.append(lambda: market(env, call=(150.0, 149.5, 150.5)))               # combined 250 -> loss 51 x 0.409 = $20.9, still under the 2 x P stop
        assert await env.eng.monitor(doc, env.cfg) == "stop" and "daily max loss" in doc["exit_note"]
        await env.db.straddle_trades.delete_many({})
        env.eng.broker = env.sx.PaperBroker(env.feed)
        doc = await _open(env, max_daily_loss_pct=15.0)
        env.eng.kill = True
        assert await env.eng.monitor(doc, env.cfg) == "stop" and doc["exit_note"] == "kill switch" and env.eng.broker.pos == {C: 0, P: 0}
        env.eng.kill = False
        await env.db.straddle_trades.delete_many({})
        env.eng.broker = env.sx.PaperBroker(env.feed)
        doc = await _open(env, stop_multiple=5.0, max_daily_loss_pct=100.0)
        env.on_wait.append(lambda: market(env, call=(600.0, 599.0, 601.0)))               # call mark above 3 x P: the exchange-side stop fires
        assert await env.eng.monitor(doc, env.cfg) == "stop"
        assert "backup stop" in doc["exit_note"] and env.eng.broker.pos == {C: 0, P: 0} and doc["exit_put"] == 100.5
    run(go())


def test_expiry_uses_the_official_settlement_and_matches_the_formula(env):
    async def go():
        doc = await _open(env)
        assert await env.eng.monitor(doc, env.cfg) == "expiry"
        assert env.now[0] >= datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc) and len(doc["parity_checked"]) == 29      # 17:01 … 17:29

        async def settlement(env_, symbol):
            return 61080.0
        env.eng.settlement = settlement
        assert await env.eng.settle(doc, env.cfg)
        assert (doc["exit_reason"], doc["exit_time"], doc["exit_call"], doc["exit_put"], doc["exit_spot"]) == ("Expiry", "17:30", 80.0, 0.0, 61080.0)
        r = (await env.st.live_journal("PAPER", env.cfg))[-1]
        pos = 409 * 0.001
        fees = (2 * min(0.0001 * 61010.0 * pos, 0.035 * 99.5 * pos) + min(0.0001 * 61080.0 * pos, 0.035 * 80.0 * pos)) * 1.18
        assert r["gross"] == pytest.approx((199.0 - 80.0) * pos) and r["fees"] == pytest.approx(fees) and r["result"] == "Win"
        assert r["parity_note"] == "1-minute rule: no stop" and r["mode"] == "PAPER" and r["equity"] == pytest.approx(1000 + r["net"])
    run(go())


def test_halts_need_manual_re_enable(env):
    async def go():
        base = {"mode": "PAPER", "status": "CLOSED", "spot": 60000.0, "strike": 60000.0, "call": C, "put": P, "entry_call": 60.0, "entry_put": 60.0, "exit_reason": "Stop",
                "exit_time": "17:05", "exit_call": 150.0, "exit_put": 100.0, "exit_spot": 60100.0, "maker": 0.0001, "taker": 0.0001, "cap": 0.035, "lots": 100, "stop_multiple": 2.0}
        for i in range(7):
            await env.db.straddle_trades.insert_one({**base, "_id": f"2026-11-{10 + i}:PAPER", "date": f"2026-11-{10 + i}"})
        await env.eng.after_close({**base, "date": "2026-11-16"}, {**env.cfg, "max_dd_pct": 99.0}, "x")
        s = await env.st.get_state()
        assert s["halted"] and "7 losing trades in a row" in s["halt_reason"] and any(a == "Trading halted" for a, _ in env.alerts)
        with pytest.raises(env.st.Skip, match="trading is halted"):
            await env.eng.preflight(env.doc(), s, env.cfg)
        await env.st.set_state(halted=False, halt_reason=None)
        await env.eng.after_close({**base, "date": "2026-11-16"}, {**env.cfg, "max_losing_streak": 50.0, "max_dd_pct": 5.0}, "x")
        assert "drawdown" in (await env.st.get_state())["halt_reason"]
        await env.st.set_state(halted=False, halt_reason=None)
        await env.db.straddle_trades.update_many({}, {"$set": {"entry_slip": 0.03, "exit_call": 0.0, "exit_put": 0.0, "exit_reason": "Expiry"}})
        await env.eng.after_close({**base, "date": "2026-11-16"}, env.cfg, "x")
        assert "entry slippage above 2% on 3 trading days" in (await env.st.get_state())["halt_reason"]
    run(go())


def test_settings_are_checked_and_change_logged_and_live_needs_typing_and_paper_days(env, monkeypatch):
    async def go():
        with pytest.raises(ValueError, match="leverage must be between"):
            await env.st.save_settings({"leverage": 500}, "a@x")
        with pytest.raises(ValueError, match="stop_mode must be one of"):
            await env.st.save_settings({"stop_mode": "fast"}, "a@x")
        cfg = await env.st.save_settings({"leverage": 10, "stop_mode": "tick", "unknown": 1}, "a@x")
        assert cfg["leverage"] == 10.0 and cfg["stop_mode"] == "tick" and "unknown" not in cfg
        log = [c async for c in env.db.straddle_changes.find({})]
        assert {(c["key"], c["old"], c["new"], c["by"]) for c in log} == {("leverage", 25.0, 10.0, "a@x"), ("stop_mode", "parity", "tick", "a@x")}
        import delta_broker
        monkeypatch.setattr(delta_broker, "keys", lambda mode: ("k", "s"))

        async def bal(self):
            return {"equity": 1.0, "available": 1.0}
        monkeypatch.setattr(env.sx.DeltaOptions, "balance", bal)
        with pytest.raises(ValueError, match="Type LIVE"):
            await env.eng.set_mode("LIVE", "", "a@x")
        with pytest.raises(ValueError, match="30 trading days first \\(0 so far\\)"):
            await env.eng.set_mode("LIVE", "LIVE", "a@x")
        await env.db.straddle_trades.insert_many([{"_id": f"d{i}:PAPER", "date": f"d{i}", "mode": "PAPER", "status": "SKIPPED"} for i in range(30)])
        await env.eng.set_mode("LIVE", "LIVE", "a@x")
        assert (await env.st.get_state())["mode"] == "LIVE"
        await env.db.straddle_trades.insert_one({"_id": "x:LIVE", "mode": "LIVE", "status": "OPEN"})
        with pytest.raises(ValueError, match="trade is in progress"):
            await env.eng.set_mode("PAPER", "", "a@x")
    run(go())


def test_scheduler_runs_the_day_and_marks_a_missed_day(env):
    async def go():
        seen = []

        async def run_day(doc, st, cfg):
            seen.append(doc["_id"])
            await env.eng._save(doc, status="SKIPPED", skip_reason="test")
        env.eng.run_day = run_day
        env.now[0] = datetime(2026, 11, 26, 11, 20, tzinfo=timezone.utc)
        assert await env.eng.step() == 30.0 and not seen                                   # before 16:55 IST: wait
        env.now[0] = datetime(2026, 11, 26, 11, 25, 1, tzinfo=timezone.utc)
        await env.eng.step()
        assert seen == ["2026-11-26:PAPER"]
        await env.eng.step()
        assert len(seen) == 1                                                              # one trade a day, no re-entry
        env.now[0] = datetime(2026, 11, 27, 11, 40, tzinfo=timezone.utc)
        await env.eng.step()
        d = await env.db.straddle_trades.find_one({"_id": "2026-11-27:PAPER"})
        assert d["status"] == "SKIPPED" and "not running at 16:55" in d["skip_reason"]
    run(go())


def test_dashboard_and_journals(env):
    async def go():
        d = await env.st.dashboard()
        assert d["mode"] == "PAPER" and d["enabled"] and not d["halted"] and d["settings"]["leverage"] == 25.0
        assert "backtest" not in d
        v = (await env.st.backtest_view())["variants"]
        assert set(v) == {"mark_fixed", "mark_compound", "trade_fixed", "trade_compound"} and round(v["mark_fixed"]["metrics"]["net"], 2) == 4361.89
        assert len(v["mark_fixed"]["curve"]) == 878 and len(v["mark_fixed"]["monthly"]) == 29 and "rows" not in v["mark_fixed"]
        assert d["live"]["metrics"]["trades"] == 0 and d["ready"]["paper_days_needed"] == 30
        json.dumps(d, default=str)
        page = await env.st.journal_page("mark_fixed", 0, 5)
        assert page["total"] == 878 and len(page["rows"]) == 5 and page["rows"][0]["date"] == "2026-11-25" and page["columns"][0] == {"key": "date", "label": "Date"}
        live = await env.st.journal_page("PAPER")
        assert live["total"] == 0 and any(c["key"] == "latency_ms" for c in live["columns"])
        csv_text = await env.st.journal_csv("mark_compound")
        assert csv_text.splitlines()[0].startswith("Date,Day,BTC spot") and len(csv_text.splitlines()) == 879
        with pytest.raises(ValueError):
            await env.st.journal_page("nope")
    run(go())


# ---------------- the real-order adapter (no network) ----------------
def test_delta_order_bodies_signing_and_no_blind_resend(env, monkeypatch):
    import delta_broker
    monkeypatch.setattr(delta_broker, "keys", lambda mode: ("key123", "secret"))
    seen = []

    def handler(req):
        body = json.loads(req.content) if req.content else None
        seen.append((req.method, req.url.path, body, dict(req.headers)))
        if req.method == "POST" and body.get("client_order_id") == "LOST":
            raise httpx.ConnectTimeout("lost")
        order = {"id": 77, "size": 10, "unfilled_size": 4, "average_fill_price": "99.5", "state": "open", "side": "sell", "client_order_id": "LOST", "updated_at": "1795670782259341"}
        return httpx.Response(200, json={"success": True, "result": order})
    br = env.sx.DeltaOptions("TESTNET", transport=httpx.MockTransport(handler))
    leg = {"symbol": C, "product_id": 166316, "tick": 0.1}

    async def go():
        o = await br.place(leg, "sell", 10, 99.5, "gtc", coid="A1")
        assert seen[0][:2] == ("POST", "/v2/orders")
        assert seen[0][2] == {"product_id": 166316, "size": 10, "side": "sell", "order_type": "limit_order", "reduce_only": "false", "limit_price": "99.5", "time_in_force": "gtc", "client_order_id": "A1"}
        h = seen[0][3]
        assert h["api-key"] == "key123" and len(h["signature"]) == 64 and h["timestamp"].isdigit()
        assert (o["filled"], o["avg"], o["open"], o["fill_ts"]) == (6, 99.5, True, 1795670782259)
        await br.place(leg, "buy", 10, None, "ioc", reduce_only=True)
        assert seen[1][2] == {"product_id": 166316, "size": 10, "side": "buy", "order_type": "market_order", "reduce_only": "true"}
        await br.place_stop(leg, 10, 298.5, coid="S1")
        assert seen[2][2] == {"product_id": 166316, "size": 10, "side": "buy", "order_type": "market_order", "stop_order_type": "stop_loss_order", "stop_price": "298.5",
                              "stop_trigger_method": "mark_price", "reduce_only": "true", "client_order_id": "S1"}
        o = await br.place(leg, "sell", 10, 99.5, "gtc", coid="LOST")                      # the answer was lost: look the order up, never send it twice
        assert [x[:2] for x in seen[3:]] == [("POST", "/v2/orders"), ("GET", "/v2/orders/client_order_id/LOST")] and o["id"] == 77
        await br.cancel_all([leg])
        assert seen[-1][:3] == ("DELETE", "/v2/orders/all", {"product_id": 166316, "cancel_limit_orders": "true", "cancel_stop_orders": "true"})
    run(go())


def test_book_walk_and_tick_rounding(env):
    sx = env.sx
    assert sx.walk([(100.5, 10), (101.0, 10)], 15, None, True) == (15, pytest.approx((10 * 100.5 + 5 * 101.0) / 15))
    assert sx.walk([(100.5, 10), (101.0, 10)], 15, 100.5, True) == (10, 100.5)             # a limit stops the walk
    assert sx.walk([(99.5, 5), (99.0, 50)], 20, 99.4, False) == (5, 99.5)
    assert sx.tick_round(292.713, 0.1) == 292.7 and sx.tick_round(292.71, 0.1, "up") == 292.8 and sx.tick_round(0.01, 0.1) == 0.1
    assert sx.env_of("PAPER") == "LIVE" and sx.env_of("TESTNET") == "TESTNET"


def test_server_straddle_is_admin_only(monkeypatch):
    from test_access_control import load_server
    server = load_server()
    from fastapi.testclient import TestClient
    from auth_core import current_user
    role = {"r": "user"}
    server.app.dependency_overrides[current_user] = lambda: {"id": "u1", "email": "x@example.com", "role": role["r"]}

    async def dash():
        return {"mode": "PAPER"}
    monkeypatch.setattr(server.straddle, "dashboard", dash)
    try:
        c = TestClient(server.app, raise_server_exceptions=False)
        for method, path, body in (("get", "/api/straddle", None), ("get", "/api/straddle/backtest", None), ("get", "/api/straddle/journal", None), ("get", "/api/straddle/journal.csv", None),
                                   ("post", "/api/straddle/settings", {"patch": {}}), ("post", "/api/straddle/mode", {"mode": "LIVE", "confirm": "LIVE"}),
                                   ("post", "/api/straddle/control", {"action": "kill"}), ("post", "/api/straddle/backtest/import", None)):
            r = getattr(c, method)(path, json=body) if body is not None else getattr(c, method)(path)
            assert r.status_code == 403, path
        role["r"] = "admin"
        assert c.get("/api/straddle").json() == {"mode": "PAPER"}
        assert c.get("/api/straddle/journal?src=nope").status_code == 400
    finally:
        server.app.dependency_overrides.clear()
