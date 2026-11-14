"""Market intelligence: technical engine, derivatives positioning, liquidation parsing and correlations."""
import asyncio
import importlib
import math
import os
import sys

import pytest

pytest.importorskip("mongomock_motor")
import motor.motor_asyncio as mm  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_db")
import ta  # noqa: E402


def bars(fn, n=300, sec=3600, t0=1_794_000_000 - 1_794_000_000 % 86400, vol=lambda i: 10.0):
    out, prev = [], fn(0)
    for i in range(n):
        c = fn(i + 1)
        out.append({"t": t0 + i * sec, "o": prev, "h": max(prev, c) + 20, "l": min(prev, c) - 20, "c": c, "v": vol(i)})
        prev = c
    return out


def test_indicators_match_known_values():
    c = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64]
    r = ta.rsi(c)
    assert abs(r[14] - 70.46) < 0.1 and abs(r[15] - 66.25) < 0.1          # Wilder's textbook RSI example
    assert ta.sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    e = ta.ema([1, 2, 3, 4, 5, 6], 3)
    assert e[2] == 2.0 and e[3] == 3.0 and e[5] == 5.0
    mid, up, lo = ta.bollinger([10.0] * 25)
    assert mid[-1] == up[-1] == lo[-1] == 10.0
    assert ta.rsi([1, 2, 3])[-1] is None


def test_uptrend_downtrend_and_insufficient():
    up = ta.analyze(bars(lambda i: 60000 + i * 40 + 600 * math.sin(i / 4)), 3600)
    assert up["status"] == "OK" and up["bias"] == "BULLISH" and up["structure"]["trend"] == "UP" and up["ema"]["20"] > up["ema"]["50"] > up["ema"]["200"]
    assert up["adx"] > 25 and up["trend_strength"] == "TRENDING" and len(up["votes"]) >= 6 and up["support"] and up["support"][0]["price"] < up["price"]
    dn = ta.analyze(bars(lambda i: 90000 - i * 40 + 600 * math.sin(i / 4)), 3600)
    assert dn["bias"] == "BEARISH" and dn["structure"]["label"] == "Lower High, Lower Low" and dn["resistance"][0]["price"] > dn["price"]
    flat = ta.analyze(bars(lambda i: 70000 + 200 * math.sin(i / 5)), 3600)
    assert flat["status"] == "OK" and flat["bias"] in ("NEUTRAL", "BULLISH", "BEARISH") and abs(flat["score"]) <= len(flat["votes"])
    assert ta.analyze(bars(lambda i: 70000, n=30), 3600)["status"] == "INSUFFICIENT DATA"
    vp = up["volume_profile"]
    assert vp["value_area_low"] <= vp["poc"] <= vp["value_area_high"] and abs(sum(x["share"] for x in vp["levels"]) - 1) < 1e-9
    f = up["fib"]
    assert f["direction"] == "UP" and f["swing_low"] < f["levels"]["0.618"] < f["levels"]["0.382"] < f["swing_high"]


def test_breakout_needs_volume_and_events():
    b = bars(lambda i: 70000 + 100 * math.sin(i / 5) + (900 if i == 300 else 0), vol=lambda i: 60.0 if i == 299 else 10.0)
    a = ta.analyze(b, 3600)
    ev = {e["kind"]: e for e in a["events"]}
    assert "breakout" in ev and "confirmed" in ev["breakout"]["text"]
    b2 = bars(lambda i: 70000 + 100 * math.sin(i / 5) + (900 if i == 300 else 0))
    assert "not confirming" in {e["kind"]: e for e in ta.analyze(b2, 3600)["events"]}["breakout"]["text"]
    o = ta.overlays(b)
    assert len(o["ema20"]) == len(b) and o["ema200"][-1] is not None


@pytest.fixture()
def intel(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "intel"):
        sys.modules.pop(m, None)
    return importlib.import_module("intel")


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


def test_derivatives_positioning(intel):
    c = intel.classify_derivs
    crowded = c({"funding_8h": 0.0009, "premium_pct": 0.05, "long_share": 0.72, "change": {"4h": {"oi_pct": 6.0, "price_pct": 1.0}}})
    assert {f["kind"] for f in crowded["flags"]} == {"crowded_longs", "leverage_build"} and crowded["label"] == "NEUTRAL"
    assert c({"funding_8h": 0.0001, "premium_pct": 0.02, "long_share": 0.5, "change": {"4h": {"oi_pct": 2.0, "price_pct": 1.0}}})["label"] == "BULLISH"
    bear = c({"funding_8h": -0.0002, "premium_pct": -0.08, "long_share": 0.7, "change": {"4h": {"oi_pct": 3.0, "price_pct": -2.0}}})
    assert bear["label"] == "BEARISH"
    unwind = c({"funding_8h": 0.0001, "premium_pct": 0.0, "long_share": 0.5, "change": {"4h": {"oi_pct": -4.0, "price_pct": -2.0}}})
    assert [f["kind"] for f in unwind["flags"]] == ["long_unwind"]
    assert c({"funding_8h": 0.0001})["label"] == "INSUFFICIENT DATA"


def test_liquidation_parsing_and_summary(intel):
    by = intel.parse_bybit({"topic": "allLiquidation.BTCUSDT", "data": [{"T": 1794600000000, "s": "BTCUSDT", "S": "Buy", "v": "2", "p": "70000"}]})
    assert by == [("Bybit", "1794600000000-Buy-70000-2", 1794600000, "long", 140000.0, 70000.0)]
    bn = intel.parse_binance({"e": "forceOrder", "o": {"s": "BTCUSDT", "S": "BUY", "q": "1.5", "p": "70500", "ap": "70400", "z": "1.5", "T": 1794600001000}})
    assert bn[0][3] == "short" and bn[0][4] == 70400 * 1.5
    assert intel.parse_binance({"result": None}) == [] and intel.parse_bybit({"op": "pong"}) == []

    async def go():
        intel.register()
        now = int(intel.now_utc().timestamp())
        await intel.add_liq("Bybit", "a", now - 60, "long", 400000, 70000)
        await intel.add_liq("Bybit", "a", now - 60, "long", 400000, 70000)      # duplicate ignored
        await intel.add_liq("OKX", "b", now - 7200, "short", 50000, 70100)
        await intel.add_liq("OKX", "old", now - 90000, "short", 999999, 70100)  # older than 24h: pruned
        s = intel.liq_summary()
        assert s["windows"]["5m"] == {"long_usd": 400000.0, "short_usd": 0.0, "count": 1} and s["windows"]["4h"]["short_usd"] == 50000.0 and s["events_24h"] == 2
        assert [e["usd"] for e in s["large"]] == [400000.0] and len(s["hourly"]) == 24 and s["cascade"] is None
    run(go())


def test_correlation(intel):
    days = [f"2026-10-{d:02d}" for d in range(1, 31)]
    a = {d: 100 * 1.01 ** i * (1.02 if i % 2 else 0.99) for i, d in enumerate(days)}
    c, n, last = intel.rolling_corr(a, {d: v * 3 for d, v in a.items()})
    assert n == 30 and last == "2026-10-30" and abs(c["7"] - 1) < 1e-9 and c["90"] is None
    inv = {d: 1e6 / v for d, v in a.items()}
    assert abs(intel.rolling_corr(a, inv)[0]["7"] + 1) < 1e-9
    assert intel.corr([1, 2, 3], [1, 2, 3]) is None   # fewer than 5 points
    h = intel.health() if intel.SRC else None
    intel.register()
    assert {s["status"] for s in intel.health()} == {"WAITING"} and len(intel.health()) == 16


def test_okx_liquidation_side(intel):
    assert intel._okx_side({"side": "sell", "posSide": "long"}) == "long" and intel._okx_side({"side": "buy", "posSide": "short"}) == "short"
    assert intel._okx_side({"side": "sell", "posSide": "net"}) == "long" and intel._okx_side({"posSide": "short"}) == "short"
