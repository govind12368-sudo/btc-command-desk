"""Delta Top 3 paper journal: SMA 9/21 cross, CCI 50 and RSI 2 signals (Normal / Reverse), reuse of the Candle Runs engine."""
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
def tq(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "runs", "trio"):
        sys.modules.pop(m, None)
    return importlib.import_module("trio")


def bars(closes, sec=3600):
    return [{"t": T0 + sec * i, "o": c, "h": c + 1, "l": c - 1, "c": c, "tc": T0 + sec * (i + 1)} for i, c in enumerate(closes)]


def test_indicators(tq):
    assert tq._sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    r = tq._rsi([10, 11, 10, 10])
    assert r[0] is None and r[1] is None and abs(r[2] - 50.0) < 1e-9            # up 1 then down 1 with Wilder n=2: 0.5 / 0.5
    flat = tq._cci(bars([100.0] * 60))
    assert flat[48] is None and flat[59] is None                                   # no deviation -> no value


def test_sma_cross_normal(tq):
    s = next(x for x in tq.STRATS if x["key"] == "sma")
    closes = [100.0] * 25 + [110.0] * 5
    bs = bars(closes)
    sig = tq.signals(s, bs, [2.0] * len(bs), [0.0] * len(bs))
    assert len(sig) == 1 and sig[0][1] == 1 and sig[0][2] == 4.0 and "crossed above" in sig[0][3]


def test_rsi_reverse_flips_side(tq):
    s = next(x for x in tq.STRATS if x["key"] == "rsi")
    closes = [100, 101, 102, 103, 104, 103, 100, 96, 95.5, 96.5]                    # deep oversold, then RSI(2) climbs back above 10
    bs = bars(closes, 14400)
    x = tq._rsi([b["c"] for b in bs])
    i = next(k for k in range(1, len(x)) if x[k - 1] is not None and x[k] is not None and x[k - 1] < 10 <= x[k])
    sig = tq.signals(s, bs, [5.0] * len(bs), [0.0] * len(bs))
    assert sig[-1][0] == bs[i]["tc"] and sig[-1][1] == -1 and "Reverse rule, so SELL" in sig[-1][3]


def test_build_uses_history_and_split(tq):
    tq.runs.REPLAY_IST = "2027-01-15 05:30"; tq.LIVE_IST = "2027-01-20 05:30"
    rt = tq.runs._ts(tq.runs.REPLAY_IST)
    row = {"signal_t": rt - 86400, "entry_t": rt - 86400, "side": "LONG", "entry": 100.0, "sl": 90.0, "tp": 120.0, "d": 10.0, "size": 100.0, "rr": 2.0,
           "chg30": 0.0, "market": "Range", "reason": "x", "exit_t": rt - 3600, "exit_price": 120.0, "result": "Target", "gross": 2000.0, "fees": 1.0, "net": 1999.0, "r": 2.0, "hold_h": 23.0}
    h = {"rows": [dict(row, strategy="cci"), dict(row, strategy="rsi")]}
    b5 = [{"t": rt - 40 * 86400 + 300 * k, "o": 100.0, "h": 100.5, "l": 99.5, "c": 100.0} for k in range(int(46 * 86400 / 300))]
    out = tq.build(b5, 100.0, hist=h)
    assert out["totals"]["full"]["trades"] == 1 and out["totals"]["full"]["shared"] == 1               # same entry -> one shared position
    p = out["positions"][-1]
    assert p["members"] == ["Row 18", "Row 8"] and [x["share"] for x in p["parts"]] == [0.5, 0.5] and p["period"] == "Backtest"
    assert out["alerts"] is False and {s["key"] for s in out["strategies"]} == {"sma", "cci", "rsi"}
