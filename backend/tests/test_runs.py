"""Candle Runs paper journal: candle building, run and big-bar signals, market filter, exits, fees, one trade at a time."""
import importlib
import os
import sys

import pytest

pytest.importorskip("mongomock_motor")
import motor.motor_asyncio as mm  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_db")

T0 = 1_800_000_000 - 1_800_000_000 % 14400


@pytest.fixture()
def rn(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "runs"):
        sys.modules.pop(m, None)
    return importlib.import_module("runs")


def c5(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


def test_bars_and_atr(rn):
    b5 = [c5(T0 + 300 * k, 100 + k, 101 + k, 99 + k, 100.5 + k) for k in range(96)]       # two full 4h candles
    bs = rn.bars(b5, "4h")
    assert [b["t"] for b in bs] == [T0, T0 + 14400] and bs[0]["o"] == 100 and bs[0]["c"] == 147.5 and bs[0]["h"] == 148 and bs[0]["l"] == 99
    assert bs[0]["tc"] == T0 + 14400
    assert rn.bars(b5[:90], "4h")[-1]["t"] == T0                                            # the unfinished candle is left out
    at = rn.atr([{"h": 10, "l": 0, "c": 5}, {"h": 12, "l": 8, "c": 10}])
    assert at[0] == 10 and abs(at[1] - (10 + (7 - 10) / 14)) < 1e-12                          # Wilder: true range 7 (12 - prev close 5)


def _bars4(colours, base=100.0):
    out, p = [], base
    for i, col in enumerate(colours):
        o = p; c = p + (1 if col == "g" else -1 if col == "r" else 0)
        out.append({"t": T0 + 14400 * i, "o": o, "h": max(o, c) + 0.5, "l": min(o, c) - 0.5, "c": c, "tc": T0 + 14400 * (i + 1)}); p = c
    return out


def test_run_signals_and_market_filter(rn):
    s4 = next(s for s in rn.STRATS if s["key"] == "pl5")
    bs = _bars4("rggggrrrr")
    at = [2.0] * len(bs)
    sig = rn.signals(s4, bs, at, [0.0] * len(bs))
    assert [(t, side) for t, side, *_ in sig] == [(bs[4]["tc"], 1), (bs[8]["tc"], -1)]
    assert sig[0][2] == 4.0 and "4 green" in sig[0][3]
    up = {**s4, "key": "up", "market": "Up"}
    assert rn.signals(up, bs, at, [0.03] * len(bs)) == []                                    # Range market: stands aside
    assert len(rn.signals(up, bs, at, [0.06] * len(bs))) == 2
    s5 = next(s for s in rn.STRATS if s["key"] == "win1")
    assert rn.signals(s5, _bars4("ggggrgggg"), at, [0.0] * 9) == []                           # never 5 in a row
    assert [rn.market_type(x) for x in (0.051, 0.05, -0.05, -0.051, None)] == ["Up", "Range", "Range", "Down", "Unknown"]


def test_big_bar(rn):
    bb = {"key": "bb", "kind": "bigbar", "n": None, "market": "Up", "rr": 1.0, "hold_h": 72}
    bs = [{"t": T0 + 3600 * i, "o": 100, "h": 101, "l": 99, "c": 100.5, "tc": T0 + 3600 * (i + 1)} for i in range(3)]
    bs[2] = {**bs[2], "o": 100, "h": 108, "l": 99, "c": 107}                                 # range 9 > 3 x ATR 2
    sig = rn.signals(bb, bs, [2.0, 2.0, 2.5], [0.08] * 3)
    assert len(sig) == 1 and sig[0][1] == 1 and sig[0][2] == 5.0
    assert rn.signals(bb, bs, [2.0, 2.0, 2.5], [0.0] * 3) == []                               # not an Up market


def test_only_three_strategies(rn):
    assert [s["wb"] for s in rn.STRATS] == ["P&L #1", "P&L #5", "Win #1"]


def test_simulate_exits_fees_and_one_at_a_time(rn):
    s = {**next(x for x in rn.STRATS if x["key"] == "pl5"), "hold_h": 1}
    e, d = 80000.0, 400.0
    b5 = [c5(T0, e, e + 10, e - 10, e), c5(T0 + 300, e, e + 820, e - 10, e + 700)] + [c5(T0 + 300 * k, e, e + 10, e - 10, e) for k in range(2, 40)]
    rows = rn.simulate(s, [(T0, 1, d, "x", 0.0), (T0 + 300, 1, d, "busy", 0.0), (T0 + 600, -1, d, "next", 0.0)], b5, 0, None)
    a = rows[0]
    assert a["result"] == "Target" and a["exit_price"] == e + 800 and a["size"] == 0.125 and a["sl"] == e - 400 and a["tp"] == e + 800
    fee = (e + e + 800) * 0.125 * 0.0005 * 1.18
    assert abs(a["fees"] - fee) < 1e-9 and abs(a["gross"] - 100.0) < 1e-9 and abs(a["net"] - (100 - fee)) < 1e-9
    assert len(rows) == 2 and rows[1]["reason"] == "next"                                    # signal during the open trade is skipped
    b = rows[1]
    assert b["result"] == "Time exit" and b["exit_price"] == e and b["exit_t"] == b["entry_t"] + 3600
    both = [c5(T0, e, e + 900, e - 500, e)]                                                   # touches stop and target: stop counts
    assert rn.simulate(s, [(T0, 1, d, "x", 0.0)], both + [c5(T0 + 300, e, e, e, e)], 0, None)[0]["result"] == "Stop"
    flat = [c5(T0 + 300 * k, e, e + 10, e - 10, e) for k in range(5)]
    op = rn.simulate({**s, "hold_h": 168}, [(T0, 1, d, "x", 0.0), (T0 + 900, 1, d, "y", 0.0)], flat, 0, e + 100)
    assert len(op) == 1 and op[0]["status"] == "OPEN" and op[0]["mark"] == e + 100
    sm = rn.summary(rows + op)
    assert sm["trades"] == 3 and sm["closed"] == 2 and sm["open"] == 1 and sm["wins"] == 1 and sm["losses"] == 1
    assert abs(sm["gross"] - 100.0) < 0.01 and abs(sm["cumulative"] - (sm["net"] + sm["open_pnl"])) < 0.01


def test_history_join(rn):
    """3-year backtest rows come first; the replay starts only after the last backtest trade has closed."""
    rn.REPLAY_IST = "2027-01-15 05:30"; rn.LIVE_IST = "2027-01-20 05:30"
    rt = rn._ts(rn.REPLAY_IST)
    h = {"rows": [{"strategy": "pl5", "signal_t": rt - 86400, "entry_t": rt - 86400, "side": "LONG", "entry": 100.0, "sl": 90.0, "tp": 120.0, "d": 10.0, "size": 1.0,
                   "rr": 2.0, "chg30": 0.0, "market": "Range", "reason": "x", "exit_t": rt + 3 * 86400, "exit_price": 120.0, "result": "Target",
                   "gross": 20.0, "fees": 1.0, "net": 19.0, "r": 1.9, "hold_h": 96.0}]}
    b5 = [c5(rt - 40 * 86400 + 300 * k, 100.0, 100.5, 99.5, 100.0) for k in range(int(46 * 86400 / 300))]
    out = rn.build(b5, 100.0, hist=h)
    s5 = next(s for s in out["strategies"] if s["key"] == "pl5")
    assert s5["full"]["trades"] == 1 and abs(s5["full"]["net"] - (2000 - (100 + 120) * 100 * 0.0005 * 1.18)) < 0.01 and s5["all"]["trades"] == 0
    assert out["totals"]["full"]["trades"] == 1 and out["backtest"]["trades"] == 1
    assert out["positions"][-1]["period"] == "Backtest" and out["positions"][-1]["exit_ist"]


def test_split_positions(rn):
    """Two strategies entering together share ONE $10,000 position; each part keeps its own target."""
    base = {"entry_t": T0, "side": "SHORT", "entry": 80000.0, "sl": 80400.0, "d": 400.0, "period": "Replay", "market": "Down", "chg30": -0.1,
            "signal_ist": "s", "entry_ist": "e", "time_exit_ist": "t", "reason": "run", "status": "CLOSED", "exit_ist": "x"}
    a = dict(base, strategy="pl5", wb="P&L #5", rr=2.0, tp=79200.0, result="Target", exit_price=79200.0, net=1.0)
    b = dict(base, strategy="pl1", wb="P&L #1", rr=3.0, tp=78800.0, result="Stop", exit_price=80400.0, net=1.0)
    c = dict(base, strategy="win1", wb="Win #1", rr=1.0, tp=79600.0, result="Target", exit_price=79600.0, net=1.0, entry_t=T0 + 14400)
    ps = rn.split_positions([a, b, c])
    assert len(ps) == 2 and ps[0]["members"] == ["P&L #5", "P&L #1"] and ps[1]["members"] == ["Win #1"]
    p0 = ps[0]
    assert [x["size"] for x in p0["parts"]] == [0.062, 0.062] and [x["share"] for x in p0["parts"]] == [0.5, 0.5]
    assert abs(p0["parts"][0]["gross"] - 800 * 0.062) < 1e-9 and abs(p0["parts"][1]["gross"] + 400 * 0.062) < 1e-9
    assert abs(p0["net"] - (p0["parts"][0]["net"] + p0["parts"][1]["net"])) < 1e-9 and p0["result"] == "Target / Stop"
    assert ps[1]["parts"][0]["size"] == 0.125
    sm = rn.summary_pos(ps)
    assert sm["trades"] == 2 and sm["shared"] == 1 and sm["closed"] == 2 and sm["targets"] == 2 and sm["stops"] == 1
