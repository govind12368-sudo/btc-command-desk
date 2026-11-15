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
    for m in ("core", "intel", "intel2"):
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
    assert {s["status"] for s in intel.health()} == {"WAITING"} and len(intel.health()) == 17


def test_okx_liquidation_side(intel):
    assert intel._okx_side({"side": "sell", "posSide": "long"}) == "long" and intel._okx_side({"side": "buy", "posSide": "short"}) == "short"
    assert intel._okx_side({"side": "sell", "posSide": "net"}) == "long" and intel._okx_side({"posSide": "short"}) == "short"


def test_forecast_probabilities_and_scoring(intel):
    cal = intel.calibration()
    assert set(cal["horizons"]) == {"5m", "15m", "30m", "1h", "4h", "8h", "24h", "7d"}
    for h in cal["horizons"].values():
        assert len(h["table"]) == 5 and all(abs(x["up"] + x["neutral"] + x["down"] - 1) < 0.001 for x in h["table"]) and h["range"]["q16"] < 0 < h["range"]["q84"]
    assert [intel.fc_bucket(x) for x in (-7, -5, -4.5, -2, -1.5, 1, 1.5, 4, 4.5)] == [0, 0, 1, 1, 2, 2, 3, 3, 4]

    async def go():
        intel.register()
        assert all(r["status"] == "INSUFFICIENT DATA" for r in intel.build_forecast()["rows"])     # never a forecast from missing data
        now = int(intel.now_utc().timestamp()); slot = now - now % 900
        for tf in ("5m", "15m", "30m", "1h", "4h", "1d"):
            intel.STATE["score"][tf] = (5, 300.0, now)
            intel.STATE["tf"][tf] = {"support": [{"price": 69000.0}], "resistance": [{"price": 71000.0}], "votes": [{"name": "Trend", "vote": 1, "text": "up"}]}
        intel.STATE["overview"]["delta"] = {"price": 70000.0}
        f = intel.build_forecast()
        assert [r["horizon"] for r in f["rows"]] == ["5m", "15m", "30m", "1h", "4h", "8h", "24h", "7d"]
        for r in f["rows"]:
            assert r["status"] == "OK" and r["up"] + r["neutral"] + r["down"] == 100 and r["range68"][0] < 70000 < r["range68"][1] and r["range90"][0] < r["range68"][0]
            assert r["confidence"] == "LOW" and r["support"] == 69000.0 and r["state"] == "strongly bullish"
        # an old forecast is checked against the price that followed
        t0 = slot - 3600
        await intel.db.intel_fc.insert_one({"_id": t0, "price": 70000.0, "f": {"5m": {"up": 40, "neutral": 30, "down": 30, "band": 0.001, "lean": "UP"}, "7d": {"up": 30, "neutral": 40, "down": 30, "band": 0.01, "lean": "NONE"}}, "res": {}})
        intel.STATE["candles"]["5m"] = [{"t": t0 + k * 300, "o": 70000, "h": 70300, "l": 69900, "c": 70200.0, "v": 1} for k in range(-2, 12)]
        await intel.forecast_tick()
        d = await intel.db.intel_fc.find_one({"_id": t0})
        assert d["res"] == {"5m": {"y": "up", "top_hit": True, "lean_hit": True}} and intel.STATE["fc_card"]["5m"] == {"checked": 1, "top_hit": 1, "lean_n": 1, "lean_hit": 1}
        assert await intel.db.intel_fc.find_one({"_id": slot})
    run(go())


# ---------------- phase 2: macro, ETF flows, on-chain, sentiment ----------------
def test_macro_rows_and_summary(intel):
    i2 = intel.intel2
    m = {x[0]: x for x in i2.MACRO}
    cpi = [("2025-%02d-01" % k, 300.0) for k in range(1, 13)] + [("2026-01-01", 309.0), ("2026-02-01", 312.0)]
    r = i2.macro_row(*m["cpi"], cpi)
    assert abs(r["value"] - 4.0) < 1e-9 and abs(r["previous"] - 3.0) < 1e-9 and r["date"] == "2026-02-01"      # year-on-year, not the raw index
    nfp = i2.macro_row(*m["nfp"], [("2026-01-01", 159000.0), ("2026-02-01", 159150.0), ("2026-03-01", 159100.0)])
    assert nfp["value"] == -50.0 and nfp["previous"] == 150.0                                                    # monthly change in thousand jobs
    with pytest.raises(RuntimeError):
        i2.macro_row(*m["fed_funds"], [("2026-01-01", 4.0)])
    assert i2.parse_fred_json({"observations": [{"date": "2026-01-01", "value": "."}, {"date": "2026-01-02", "value": "4.1"}]}) == [("2026-01-02", 4.1)]
    assert i2.parse_fred_csv("observation_date,DGS10\n2026-01-01,\n2026-01-02,4.25\n") == [("2026-01-02", 4.25)]
    nl = i2.net_liquidity({"fed_assets": [("2026-01-07", 7_000_000.0), ("2026-01-14", 7_000_000.0)], "tga": [("2026-01-07", 800_000.0), ("2026-01-14", 700_000.0)],
                           "rrp": [("2026-01-07", 100.0), ("2026-01-13", 50.0), ("2026-01-14", 90.0)]})
    assert nl == [("2026-01-07", 6_100_000.0), ("2026-01-14", 6_210_000.0)]                                      # RRP is in billions, the others in millions
    hist = lambda a, b: [{"d": str(k), "v": a} for k in range(25)] + [{"d": "x", "v": b}]
    rows = [{"key": "y10", "status": "OK", "value": 4.6, "change": 0.01, "history": hist(4.2, 4.6)}, {"key": "usd", "status": "OK", "value": 125.0, "change": 0.1, "history": hist(123.0, 125.0)},
            {"key": "fed_funds", "status": "OK", "value": 4.0, "change": -0.25, "history": []}, {"key": "cpi", "status": "UNAVAILABLE"}]
    s = i2.macro_summary(rows)
    assert s["headwinds"] == 2 and s["tailwinds"] == 1 and "against" in s["label"]
    assert i2.macro_summary([])["label"] == "No clear macro lean"


def test_fred_key_validation(intel):
    i2 = intel.intel2
    with pytest.raises(ValueError):
        run(i2.save_fred_key("not-a-key"))
    assert run(i2.fred_status())["set"] is False

    async def ok(sid, key):
        return [("2026-01-01", 4.0)]
    i2.fred_series = ok
    assert run(i2.save_fred_key("  " + "AB12" * 8 + " "))["set"] is True
    st = run(i2.fred_status())
    assert st["set"] is True and "key" not in st                       # the key itself is never returned
    assert run(i2.save_fred_key(""))["set"] is False and run(i2.fred_key()) is None

    async def bad(sid, key):
        raise RuntimeError("FRED rejected the API key")
    i2.fred_series = bad
    with pytest.raises(RuntimeError):
        run(i2.save_fred_key("ab12" * 8))
    assert run(i2.fred_key()) is None                                   # a key FRED refuses is not stored


def test_etf_summary_and_farside(intel):
    i2 = intel.intel2
    rows = [{"date": "2026-11-13", "total_musd": -120.0}, {"date": "2026-11-12", "total_musd": -80.5}, {"date": "2026-11-11", "total_musd": 300.0},
            {"date": "2026-11-10", "total_musd": 10.0}, {"date": "2026-11-09", "total_musd": -400.0}]
    s = i2.etf_summary(rows)
    assert s["streak"] == {"days": 2, "side": "OUTFLOW"} and abs(s["sum_5d"] + 290.5) < 1e-9 and s["sum_10d"] is None and "selling" in s["label"]
    assert i2.etf_summary([]) is None
    html = "<table><tr><th>Date</th><th>IBIT</th><th>Total</th></tr><tr><td>12 Nov 2026</td><td>(50.0)</td><td>(80.5)</td></tr><tr><td>11 Nov 2026</td><td>1,200.0</td><td>1,300.0</td></tr>" \
           "<tr><td>10 Nov 2026</td><td>-</td><td>10.0</td></tr><tr><td>Total</td><td>9</td><td>99</td></tr><tr><td>13 Nov 2026</td><td>-</td><td>-</td></tr></table>"
    assert i2.parse_farside(html) == {"2026-11-12": -80.5, "2026-11-11": 1300.0, "2026-11-10": 10.0}             # brackets are outflows; unpublished days are skipped
    with pytest.raises(RuntimeError):
        i2.parse_farside("<html>Just a moment...</html>")


def test_onchain_and_sentiment(intel):
    i2 = intel.intel2
    vals = [("2026-10-%02d" % d, 100.0) for d in range(1, 24)] + [("2026-10-%02d" % d, 130.0) for d in range(24, 31)]
    r = i2.series_row("x", vals, "u", "s", "w")
    assert r["avg_7d"] == 130.0 and abs(r["avg_30d"] - 107.0) < 1e-9 and abs(r["week_vs_month_pct"] - (130 / 107 - 1) * 100) < 1e-9 and r["date"] == "2026-10-30"
    with pytest.raises(RuntimeError):
        i2.series_row("x", vals[:5], "u", "s", "w")
    v = i2.valuation(2.0, 100000.0)
    assert v["nupl"] == 0.5 and v["realized_price"] == 50000.0 and v["zone"] == "Normal range"
    assert i2.valuation(0.8, 40000.0)["zone"].startswith("Deep value") and i2.valuation(None, 1.0) is None
    obs = [{"value": 20 + k, "label": "Extreme Fear", "date": "2026-11-%02dT00:00:00+00:00" % (15 - k)} for k in range(10)]
    f = i2.fng_summary(obs)
    assert f["value"] == 20 and f["yesterday"] == 21 and f["week_ago"] == 27 and f["avg_7d"] == 23.0 and f["avg_30d"] is None and f["read"].startswith("Extreme fear")
    assert f["history"][-1]["v"] == 20 and i2.fng_summary([]) is None
    items = [{"title": "t%d" % k, "impact": "HIGH" if k < 2 else "LOW", "keyword_direction": "BEARISH KEYWORDS" if k < 6 else "NEUTRAL", "categories": ["Macro"]} for k in range(10)]
    n = i2.news_summary(items)
    assert n["total"] == 10 and n["high_impact"] == 2 and n["bearish_words"] == 6 and n["tone"].startswith("More negative") and len(n["top"]) == 2 and n["categories"][0] == ("Macro", 10)
    assert set(intel.public()) >= {"macro", "etf", "onchain", "sentiment"}
