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
    for m in ("core", "intel", "intel2", "intel3"):
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
    assert abs(r["value"] - 4.0) < 1e-9 and abs(r["previous"] - 3.0) < 1e-9 and r["date"] == "2026-02-01" and r["freq"] == "M"      # year-on-year, not the raw index
    nfp = i2.macro_row(*m["nfp"], [("2026-01-01", 159000.0), ("2026-02-01", 159150.0), ("2026-03-01", 159100.0)])
    assert nfp["value"] == -50.0 and nfp["previous"] == 150.0                                                    # monthly change in thousand jobs
    with pytest.raises(RuntimeError):
        i2.macro_row(*m["fed_funds"], [("2026-01-01", 4.0)])
    assert i2.parse_fred_json({"observations": [{"date": "2026-01-01", "value": "."}, {"date": "2026-01-02", "value": "4.1"}]}) == [("2026-01-02", 4.1)]
    assert i2.parse_fred_csv("observation_date,DGS10\n2026-01-01,\n2026-01-02,4.25\n") == [("2026-01-02", 4.25)]
    nl = i2.net_liquidity({"fed_assets": [("2026-01-07", 7_000_000.0), ("2026-01-14", 7_000_000.0)], "tga": [("2026-01-07", 800_000.0), ("2026-01-14", 700_000.0)],
                           "rrp": [("2026-01-07", 100.0), ("2026-01-13", 90.0)]})
    assert nl == [("2026-01-07", 6_100_000.0), ("2026-01-14", 6_210_000.0)]                                      # RRP is in billions; a holiday uses the last value
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
    html = "<table><tr><th></th><th>IBIT</th><th>FBTC</th><th>Total</th></tr><tr><td>Fee</td><td>0.25%</td><td>0.25%</td><td></td></tr>" \
           "<tr><td>12 Nov 2026</td><td>(50.0)</td><td>(30.5)</td><td>(80.5)</td></tr><tr><td>11 Nov 2026</td><td>1,200.0</td><td>100.0</td><td>1,300.0</td></tr>" \
           "<tr><td>10 Nov 2026</td><td>0.0</td><td>10.0</td><td>10.0</td></tr><tr><td>Total</td><td>9</td><td>9</td><td>99</td></tr><tr><td>13 Nov 2026</td><td>-</td><td>-</td><td>-</td></tr></table>"
    tot, funds = i2.parse_farside(html)
    assert tot == {"2026-11-12": -80.5, "2026-11-11": 1300.0, "2026-11-10": 10.0}                                 # brackets are outflows; unpublished days are skipped
    assert funds["2026-11-12"] == {"IBIT": -50.0, "FBTC": -30.5} and funds["2026-11-10"] == {"FBTC": 10.0}
    with pytest.raises(RuntimeError):
        i2.parse_farside("<html>Just a moment...</html>")
    c = i2.etf_compare(tot, {"2026-11-12": -80.0, "2026-11-11": 24.9, "2026-11-01": 5.0})
    assert c["days_compared"] == 2 and [m["date"] for m in c["mismatches"]] == ["2026-11-11"] and "differs" in c["text"]
    assert "agrees" in i2.etf_compare(tot, {"2026-11-12": -80.0})["text"]


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


# ---------------- phase 3: risk scores, backtest, estimated liquidation zones ----------------
def fake_history(n=1500, seed=7, start=736000):
    """A random-walk market with every side series, daily from ordinal `start`."""
    import random
    from datetime import date
    rnd, px, K, iso_d = random.Random(seed), 20000.0, [], lambda o: date.fromordinal(o).isoformat()
    for k in range(n):
        o = px
        px = max(1000.0, px * (1 + rnd.gauss(0.0005, 0.035)))
        qv = rnd.uniform(1e9, 3e9)
        K.append({"d": start + k, "o": o, "h": max(o, px) * 1.01, "l": min(o, px) * 0.99, "c": px, "qv": qv, "tb": qv * rnd.uniform(0.44, 0.56), "partial": k == n - 1})
    days = range(start - 420, start + n)
    daily = lambda lo, hi: [(iso_d(d), rnd.uniform(lo, hi)) for d in days]
    walk = lambda v, step: [(iso_d(d), (v := v + rnd.gauss(0, step))) for d in days]
    return {"klines": K, "fng": [(d, rnd.randint(5, 95)) for d in days], "etf": {iso_d(d): rnd.gauss(0, 300) for d in days if d % 7 < 5},
            "core": [(f"{y}-{m:02d}-01", 300 + (y - 2010) * 9 + m * rnd.uniform(0.6, 0.9)) for y in range(2014, 2022) for m in range(1, 13)],
            "y2": walk(4.0, 0.05), "usd": walk(120.0, 0.3), "liq": [(iso_d(d), 6e6 + rnd.gauss(0, 5e4)) for d in days if d % 7 == 3],
            "flow_in": daily(15000, 30000), "flow_out": daily(15000, 30000), "mvrv": daily(0.8, 3.2), "stables": walk(2e11, 5e8), "funding": [(d, rnd.gauss(0.0001, 0.0002)) for d in days]}


def test_risk_rows_have_no_lookahead(intel):
    i3 = intel.intel3
    H = fake_history()
    rows = i3.build_days(H)
    k = 900
    day = rows[k]["d"]
    from datetime import date
    iso_d = lambda o: date.fromordinal(o).isoformat()
    G = dict(H)                                                         # the same world, but everything after day k (and anything not yet published on day k) is different
    G["klines"] = [dict(b, c=b["c"] * 3, h=b["h"] * 3, l=b["l"] * 3, o=b["o"] * 3, qv=1.0, tb=1.0) if b["d"] > day else b for b in H["klines"]]
    G["fng"] = [(d, 1) if d > day else (d, v) for d, v in H["fng"]]
    G["funding"] = [(d, 9.0) if d > day else (d, v) for d, v in H["funding"]]
    G["etf"] = {d: (9e9 if d >= iso_d(day) else v) for d, v in H["etf"].items()}                       # the flow of day k is published the next day
    for key in ("y2", "flow_in", "flow_out", "mvrv", "stables"):
        G[key] = [(d, 9e9) if d >= iso_d(day) else (d, v) for d, v in H[key]]
    G["usd"] = [(d, 9e9) if d > iso_d(day - 7) else (d, v) for d, v in H["usd"]]                        # weekly release
    G["liq"] = [(d, 9e9) if d > iso_d(day - 2) else (d, v) for d, v in H["liq"]]
    G["core"] = [(d, 9e9) if d > iso_d(day - 45) else (d, v) for d, v in H["core"]]                     # a month's CPI is published about 6 weeks after the month starts
    assert i3.build_days(G)[k] == rows[k]
    assert rows[k]["taker7"] is not None and rows[k]["netflow7"] is not None and rows[k]["etf5"] is not None and rows[k]["fund3"] is not None


def test_risk_score_rules(intel):
    i3 = intel.intel3
    assert [i3.band(x) for x in (0, 20, 21, 40, 41, 60, 61, 80, 81, 100)] == ["LOW", "LOW", "MODERATE", "MODERATE", "ELEVATED", "ELEVATED", "HIGH", "HIGH", "EXTREME", "EXTREME"]
    on = lambda mx: [i3.I("x", mx, True, "t")]
    off = lambda mx: [i3.I("x", mx, False, "t")]
    t = i3.total({"technical": on(20), "spot": off(10), "etf": off(15), "macro": off(15), "onchain": off(15), "sentiment": off(10), "funding": off(5)})
    assert t["score"] == 22 and t["confirming"] == 1                    # price alone: 20 of 90 points, never a high score
    t = i3.total({"technical": on(20), "spot": on(10), "etf": on(15), "macro": off(15)})
    assert t["raw"] == 75 and t["score"] == 60 and t["capped"] and t["confirming"] == 3      # three groups agree: held at 60
    t = i3.total({"technical": on(20), "spot": on(10), "etf": on(15), "macro": on(15), "onchain": off(15)})
    assert t["raw"] == 80 and t["score"] == 80 and t["confirming"] == 4
    t = i3.total({"technical": on(20), "spot": on(10), "etf": on(15), "macro": on(15), "onchain": on(15), "sentiment": off(1)})
    assert t["score"] == 99 and t["confirming"] == 5
    t = i3.total({"technical": on(20), "live": on(10), "etf": [i3.I("x", 15, None, None)]})
    assert t["available"] == 30 and t["groups"][2]["status"] == "UNAVAILABLE" and i3.total({"technical": on(20), "live": on(10)}, True)["available"] == 20
    assert i3.total({})["score"] is None
    f = {"c": 60000.0, "low20": 63000.0, "ema50": 66000.0, "ema200": 71000.0, "ema50_prev5": 66500.0, "brk": {"level": 63000.0, "vol": 1.8, "ago": 0}, "low30": 59000.0, "low30_held": 1, "div": False,
         "taker7": 47.0, "upvol10": 35.0, "low10": 59000.0, "broke_out": False, "fng": 10, "fng_3": 13, "fng_7": 28, "etf_streak": -7, "etf5": -1065.0, "etf20": -2248.0, "core": 1,
         "y2_chg20": 0.3, "usd_chg20": 1.2, "liq_chg4w": -74000.0, "netflow7": -2852.0, "mvrv": 1.19, "stable_chg30": -2.4, "fund3": -0.00001}
    thr = {"taker7": [48.5, 51.0], "y2_chg20": [-0.2, 0.2], "usd_chg20": [-1.0, 1.0], "liq_chg4w": [-50000.0, 50000.0], "netflow7": [-9000.0, 4000.0], "stable_chg30": [0.2, 3.0], "fund3": [0.00002, 0.0002]}
    live = {"long_share": 0.73, "ch24": None, "ch4": None, "liq": {"1h": {"long_usd": 8e5, "short_usd": 6e5}, "24h": {"long_usd": 1e8, "short_usd": 4e7}}, "cascade": None, "feeds_ok": True}
    c = i3.total(i3.crash_items(f, thr, live))
    st = {g["key"]: g["status"] for g in c["groups"]}
    assert st["technical"] == st["spot"] == st["etf"] == st["macro"] == st["sentiment"] == "CONFIRMING" and st["funding"] == "QUIET" and c["confirming"] >= 5 and c["score"] >= 61
    assert c["available"] == 97                                          # the 24-hour open-interest item has no data yet, so its 3 points are left out
    r = i3.total(i3.recovery_items(f, thr, live))
    assert r["score"] <= 20 and {g["key"]: g["status"] for g in r["groups"]}["funding"] == "CONFIRMING"
    assert i3.stage(f)["key"] == "DOWN" and i3.stage({**f, "c": 62500.0})["key"] == "RELIEF"
    assert i3.stage({**f, "c": 70000.0, "ema50_prev5": 65000.0, "broke_out": True})["key"] == "CONFIRMED" and i3.stage({"c": 1.0})["key"] == "UNKNOWN"
    assert i3.total(i3.crash_items({"c": 60000.0}, {}))["score"] is None                     # no data at all: no score, nothing invented


def test_backtest_report(intel):
    i3 = intel.intel3
    H = fake_history()
    rep, thr = i3.run_backtest(H)
    assert set(thr) == set(i3.QUANT) and all(lo <= hi for lo, hi in thr.values())
    sp = rep["splits"]
    assert sp["train"]["to"] < sp["validation"]["from"] and sp["validation"]["to"] < sp["test"]["from"]              # in time order, never mixed
    from datetime import date
    assert (date.fromisoformat(sp["validation"]["from"]) - date.fromisoformat(sp["train"]["to"])).days > i3.EMBARGO   # gap so outcomes cannot leak across
    t = rep["crash"]["7"]["test"]
    w = t["warn41"]
    assert t["days"] > 100 and w["warnings"] == w["hits"] + w["false_alarms"] and sum(b["days"] for b in t["bands"]) == t["days"] and sum(b["events"] for b in t["bands"]) == t["events"]
    assert w["hits"] + w["missed"] == t["events"] and w["episodes_hit"] <= w["episodes"] <= max(w["warnings"], 0)
    assert set(rep["crash"]) == {"3", "7", "14", "verdict"} and rep["crash"]["14"]["test"]["events"] >= t["events"] >= rep["crash"]["3"]["test"]["events"]
    assert all(0 <= s["crash"] <= 100 and 0 <= s["recovery"] <= 100 for s in rep["series"]) and rep["missing"] == []
    m = i3._metrics([(50, True, -0.2), (50, False, 0.0), (10, True, -0.2), (10, False, 0.0), (10, False, 0.0), (70, True, -0.3)], 41, 0.5)
    assert (m["warnings"], m["hits"], m["false_alarms"], m["missed"]) == (3, 2, 1, 1) and abs(m["precision"] - 2 / 3) < 1e-9 and abs(m["recall"] - 2 / 3) < 1e-9 and m["episodes"] == 1
    assert abs(m["fpr"] - 1 / 3) < 1e-9 and abs(m["lift"] - 4 / 3) < 1e-9 and not m["proven"]                  # one warning period proves nothing
    many = [(50, k % 10 < 8, -0.2) if k % 6 == 0 else (10, False, 0.0) for k in range(600)]                      # 100 separate warnings, 80% followed by the fall
    assert i3._metrics(many, 41, 0.13)["proven"] and i3._metrics(many, 41, 0.13)["episodes"] == 100
    assert "unproven" in i3._verdict("crash", {"days": 100, "base_rate": 0.2, "warn41": {**m, "warnings": 30, "episodes": 9}})
    with pytest.raises(RuntimeError):
        i3.run_backtest({**H, "klines": H["klines"][:500]})
    rows = i3.build_days(H)
    v = i3.risk_view(rows, thr, rep, None, {"crash": {"score": 10, "label": "24 hours ago"}, "recovery": {"score": 10, "label": "24 hours ago"}})
    c = v["crash"]
    assert c["score"] == c["tested_score"] and c["band"] == i3.band(c["score"]) and c["change"] == c["score"] - 10 and c["confidence"] in ("LOW", "MEDIUM", "HIGH")
    assert c["history_rate"]["band"] == c["tested_band"] and v["recovery"]["stage"]["key"] in ("DOWN", "RELIEF", "UPTREND", "CONFIRMED") and not v["day_complete"]
    import json
    json.dumps(rep), json.dumps(v)


def test_estimated_liquidation_zones(intel):
    i3 = intel.intel3
    bars = {t * 3600: (100.2, 99.8, 100.0) for t in range(6)}
    oi = [(0, 1000.0), (3600, 1100.0), (7200, 1100.0)]                   # 100 BTC of new positions in hour 0 at about 100
    m = i3.liq_map(bars, oi, 100.0)
    tot = lambda key: sum(b[key] for b in m["bins"])
    assert abs(tot("long_usd") - 100 * 100 * 0.75) < 1 and abs(tot("short_usd") - 100 * 100 * 0.75) < 1      # 5x sits 19.5% away: outside the +/-15% map
    b = {x["from_pct"]: x for x in m["bins"]}
    assert abs(b[-1]["long_usd"] - 500) < 1 and abs(b[-2]["long_usd"] - 1500) < 1 and abs(b[-4]["long_usd"] - 2500) < 1 and abs(b[-10]["long_usd"] - 3000) < 1
    assert abs(b[0]["short_usd"] - 500) < 1 and abs(b[9]["short_usd"] - 3000) < 1 and m["lean"] == "BALANCED"
    assert m["within"][1]["long_usd"] == b[-1]["long_usd"] + b[-2]["long_usd"] + b[-4]["long_usd"]
    bars[3 * 3600] = (100.2, 97.5, 98.0)                                  # price later trades down to 97.5: the 100x and 50x longs are gone
    m = i3.liq_map(bars, oi, 98.0)
    assert abs(sum(x["long_usd"] for x in m["bins"]) - 100 * 100 * 0.55) < 1 and abs(sum(x["short_usd"] for x in m["bins"]) - 7500) < 1
    assert i3.liq_map({**bars, 3 * 3600: (100.2, 99.9, 100.0), 4 * 3600: (103.0, 99.9, 100.0)}, oi, 100.0)["lean"] == "LONGS"      # shorts within 3% were taken out
    m = i3.liq_map({t * 3600: (100.2, 99.8, 100.0) for t in range(6)}, [(0, 1000.0), (3600, 1100.0), (7200, 550.0)], 100.0)
    assert abs(sum(x["long_usd"] for x in m["bins"]) - 100 * 100 * 0.75 * 0.5) < 1                           # open interest halved: positions scaled down
    assert i3.liq_map(bars, [], 100.0)["clusters"] == {"long": [], "short": []}
