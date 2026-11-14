"""Day type, the follow / Plan A / switch books, the scanner maths and the Auto-Trade direction modes."""
import asyncio
import importlib
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("mongomock_motor")
import motor.motor_asyncio as mm  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_db")


@pytest.fixture()
def mods(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "autotrade", "daytype", "plan_a", "scanner"):
        sys.modules.pop(m, None)
    return {m: importlib.import_module(m) for m in ("core", "daytype", "autotrade", "plan_a", "scanner")}


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


def bars(fn, n=144, t0=1_794_000_000):
    out, prev = [], fn(0)
    for i in range(n):
        c = fn(i + 1)
        out.append({"t": t0 + i * 300, "o": prev, "h": max(prev, c) + 5, "l": min(prev, c) - 5, "c": c, "v": 10.0})
        prev = c
    return out


def test_classify_trend_range_and_unknown(mods):
    DT = mods["daytype"]
    up = DT.classify(bars(lambda i: 70000 + i * 10.4), 1500)
    assert up["type"] == "TREND_UP" and up["dir"] == "UP" and up["score"] >= 4 and len(up["measures"]) == 5
    down = DT.classify(bars(lambda i: 70000 - i * 10.4), 1500)
    assert down["type"] == "TREND_DOWN"
    rng = DT.classify(bars(lambda i: 70000 + 100 * math.sin(i / 6)), 1500)
    assert rng["type"] == "RANGE" and rng["score"] <= 1
    assert DT.classify(bars(lambda i: 70000, n=50), 1500)["type"] == "NA"
    assert DT.classify(bars(lambda i: 70000), None)["type"] == "NA"


def test_playbook(mods):
    DT = mods["daytype"]
    assert DT.playbook({"type": "RANGE"}, "LONG") == "PLAN_A" and DT.playbook({"type": "RANGE"}, "SHORT") == "PLAN_A"
    assert DT.playbook({"type": "TREND_DOWN"}, "SHORT") == "FOLLOW" and DT.playbook({"type": "TREND_DOWN"}, "LONG") == "STAND_ASIDE"
    assert DT.playbook({"type": "TREND_UP"}, "LONG") == "FOLLOW" and DT.playbook({"type": "MIXED"}, "LONG") == "STAND_ASIDE"
    assert DT.playbook({"type": "NA"}, "LONG") == "STAND_ASIDE"
    ev = DT.evidence()
    assert ev["signals"] > 1000 and ev["follow"]["mean_r"] < 0 and ev["plan_a"]["mean_r"] < 0   # the evidence file ships with the site


def test_at_uses_only_past_candles_and_eod_label(mods):
    DT = mods["daytype"]
    t0 = 1_794_000_000 - 1_794_000_000 % 300
    hist = bars(lambda i: 70000 + 150 * math.sin(i / 6), n=288 * 12, t0=t0)
    trend = bars(lambda i: hist[-1]["c"] + i * 12, n=144, t0=t0 + 288 * 12 * 300)
    allb = hist + trend
    before = DT.at(trend[0]["t"], allb)
    after = DT.at(trend[-1]["t"] + 300, allb)
    assert before["type"] in ("RANGE", "MIXED") and after["type"] == "TREND_UP"
    assert DT.eod_label(bars(lambda i: 70000 + i * 6, n=288), 1500) == "TREND_UP"
    assert DT.eod_label(bars(lambda i: 70000 + 300 * math.sin(i / 10), n=288), 1500) == "RANGE"


def row(pa, sid, cap, fill, d0, playbook, f_res, f_net, p_res, p_net, hold=3600):
    return {"signal_id": sid, "cap_t": cap, "fill_status": "FILLED", "fill_t": fill, "model_dir": d0, "playbook": playbook, "captured_ist": pa._ist(cap),
            "follow_result": f_res, "follow_exit_t": fill + hold, "follow_exit_ist": pa._ist(fill + hold), "follow_net": f_net,
            "plan_result": p_res, "plan_exit_t": fill + hold, "exit_ist": pa._ist(fill + hold), "net": p_net}


def test_books_follow_plan_a_switch(mods):
    pa = mods["plan_a"]
    t = 1_794_000_000
    rows = [row(pa, "A", t, t + 60, "SHORT", "PLAN_A", "STOP", -60, "TARGET", 90),
            row(pa, "B", t + 600, t + 660, "SHORT", "FOLLOW", "TARGET", 66, "STOP", -61),       # overlaps A → busy in every book
            row(pa, "C", t + 7200, t + 7260, "SHORT", "FOLLOW", "TARGET", 66, "STOP", -61),
            row(pa, "D", t + 14400, t + 14460, "LONG", "STAND_ASIDE", "STOP", -60, "TIME", 20),
            {"signal_id": "E", "cap_t": t + 20000, "fill_status": "NOT FILLED"}]
    f, p, s = (pa.book(rows, m) for m in pa.BOOKS)
    assert (f["signals"], f["filled"], f["taken"], f["skipped"]) == (5, 4, 3, 1) and f["net"] == -60 + 66 - 60 and (f["target"], f["stop"]) == (1, 2)
    assert p["net"] == 90 - 61 + 20 and (p["target"], p["stop"], p["time"]) == (1, 1, 1) and round(p["win_rate"], 2) == 0.67
    assert s["taken"] == 2 and s["stood_aside"] == 1 and s["skipped"] == 1 and s["net"] == 90 + 66 and s["picks"] == {"A": "plan_a", "B": "busy", "C": "follow", "D": "aside"}
    assert f["max_drawdown"] == -60 and [c["v"] for c in s["curve"]] == [90, 156]
    late = pa.book(rows, "follow", since=t + 7000)
    assert late["signals"] == 3 and late["taken"] == 2 and late["net"] == 6


def test_scanner_options_and_decision(mods):
    sc = mods["scanner"]
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    rows = [{"instrument_name": "BTC-20NOV26-66500-P", "open_interest": 300, "mark_iv": 58.0, "underlying_price": 70000},
            {"instrument_name": "BTC-20NOV26-73500-C", "open_interest": 200, "mark_iv": 50.0, "underlying_price": 70000},
            {"instrument_name": "BTC-14NOV26-70000-C", "open_interest": 100, "mark_iv": 80.0, "underlying_price": 70000},
            {"instrument_name": "BTC-PERPETUAL", "open_interest": 999}]
    o = sc.parse_options(rows, now)
    assert o["put_call_oi"] == 1.0 and o["expiry"] == "20 Nov" and o["skew"] == 8.0 and (o["put_strike"], o["call_strike"]) == (66500, 73500)
    v = lambda x: {"vote": x}
    assert sc.decide([v(1), v(1), v(0)])["decision"] == "BUY" and sc.decide([v(-1), v(-1), v(1), v(-1)])["decision"] == "SELL"
    assert sc.decide([v(1), v(-1), v(1)])["decision"] == "WAIT"
    res = sc.compute({"flow": {"valid": True, "imbalance": -0.2, "label": "SELL-SIDE LEAN", "buy_usd": 1, "sell_usd": 2},
                      "pillars": {"positioning": {"valid": True, "score": -1, "detail": "funding high"}}, "six_factor": {"complete": True, "direction": "SHORT", "strength": "MODERATE", "score": -0.4, "contributions": {"four_factor": -0.05}}})
    assert res["intraday"]["decision"] == "SELL" and res["swing"]["decision"] == "SELL" and res["day_type"]["type"] == "NA" and len(res["sources"]) == 4


def test_autotrade_direction_modes(mods):
    at, core, DT = mods["autotrade"], mods["core"], mods["daytype"]
    assert at.follow_levels("SHORT", 71602, 71960.5, 71064.5) == {"bot_dir": "SHORT", "entry": 71602.0, "stop": 71960.5, "target": 71064.5, "d": 358.5}
    clock = [datetime(2026, 11, 13, 6, 0, tzinfo=timezone.utc)]

    async def alert(*a):
        return None

    async def sig(sid, d, e, s, t):
        await core.db.signals.insert_one({"_id": sid, "strategy": "scalp", "direction": d, "entry": e, "stop": s, "target": t, "captured_at": core.iso(clock[0]), "valid_until": None})

    async def go():
        eng = at.Engine(clock=lambda: clock[0], alert=alert)
        await eng.tick(71900)
        DT.STATE["now"] = {"type": "TREND_DOWN", "label": "Trend (down)"}
        await sig("SCALP-SHORT-1", "SHORT", 72000.0, 72360.0, 71460.0)   # with the trend → follow: SHORT at ≥ 72000
        await sig("SCALP-LONG-2", "LONG", 71800.0, 71441.0, 72338.5)     # against the trend → stand aside
        await eng.tick(71900)
        a1, a2 = await core.db.autotrade_signals.find_one({"_id": "SCALP-SHORT-1"}), await core.db.autotrade_signals.find_one({"_id": "SCALP-LONG-2"})
        assert a1["status"] == "ARMED" and a1["plan"] == "FOLLOW" and (a1["bot_dir"], a1["stop"], a1["target"]) == ("SHORT", 72360.0, 71460.0)
        assert a2["status"] == "SKIPPED" and "stand aside" in a2["reason"]
        await eng.tick(71950)                                             # not up at the entry yet
        assert await core.db.autotrade_trades.count_documents({}) == 0
        await eng.tick(72005)
        t = await core.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"})
        assert t["status"] == "OPEN" and t["bot_dir"] == "SHORT" and t["plan"] == "FOLLOW" and t["day_type"] == "Trend (down)"
        await eng.tick(71450)                                             # the signal's own target
        t = await core.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"})
        assert t["exit_reason"] == "TARGET" and t["exit_price"] == 71460.0 and t["net_usd"] > 0
        clock[0] += timedelta(minutes=30)
        DT.STATE["now"] = {"type": "RANGE", "label": "Range"}
        await sig("SCALP-SHORT-3", "SHORT", 71500.0, 71857.5, 70964.0)   # range → Plan A: LONG at ≥ 71500
        await eng.tick(71505)
        t3 = await core.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-3"})
        assert t3["plan"] == "PLAN_A" and t3["bot_dir"] == "LONG" and (t3["stop"], t3["target"]) == (71142.5, 72215.0)
        await eng.close_everything("me")
        await eng.resume("me")
        clock[0] += timedelta(minutes=30)
        DT.STATE["now"] = {"type": "MIXED", "label": "Unclear"}
        await sig("SCALP-SHORT-4", "SHORT", 71500.0, 71857.5, 70964.0)
        await eng.tick(71505)
        assert (await core.db.autotrade_signals.find_one({"_id": "SCALP-SHORT-4"}))["status"] == "SKIPPED"
        await at.save_settings({"direction_mode": 1}, "me")              # follow always, whatever the day type
        clock[0] += timedelta(minutes=30)
        await sig("SCALP-LONG-5", "LONG", 71400.0, 71043.0, 71935.5)
        await eng.tick(71395)
        t5 = await core.db.autotrade_trades.find_one({"_id": "SCALP-LONG-5"})
        assert t5["plan"] == "FOLLOW" and t5["bot_dir"] == "LONG" and t5["stop"] == 71043.0
    run(go())


def test_why_notes_and_final(mods):
    pa = mods["plan_a"]
    t0 = 1_794_000_000
    def bar(i, h, l, c):
        return {"t": t0 + i * 60, "o": c, "h": h, "l": l, "c": c}
    # SHORT at 100, stop gap 10: quick stop at 110, then price falls to 75 (target level 80) -> whipsaw note
    bars = [bar(0, 101, 99, 100), bar(1, 104, 99, 103), bar(2, 111, 103, 110), bar(3, 108, 90, 92), bar(4, 92, 74, 75)]
    w = pa.why("SHORT", 100.0, 10.0, "STOP", t0, t0 + 120, 110.0, bars, 0, 2)
    assert "Went against at once" in w and "2 min" in w and "target level anyway" in w
    w = pa.why("LONG", 100.0, 10.0, "TARGET", t0, t0 + 180, 120.0, [bar(0, 101, 97, 100), bar(1, 105, 96, 104), bar(2, 121, 104, 120)], 0, 2)
    assert w.startswith("Target hit in 3 min") and "0.4x against" in w
    assert pa.why("LONG", 100.0, 10.0, "OPEN", t0, None, None, bars, 0, 2) == "Still open."
    assert pa._final({"fill_status": "NOT FILLED"}, 0)
    row = {"fill_status": "FILLED", "plan_result": "STOP", "follow_result": "TARGET", "model_result": "TARGET", "fill_t": t0}
    assert not pa._final(row, t0 + 3600) and pa._final(row, t0 + 6 * 3600 + 120)
