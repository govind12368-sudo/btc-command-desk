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
