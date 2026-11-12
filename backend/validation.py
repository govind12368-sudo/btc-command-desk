"""Forward validation: 30-day completed 5m history, immutable snapshots, first-touch outcomes, and rule backtest evidence."""
import math
from core import db, now_utc, iso, tick_ceil, tick_floor
from indicators import ema_series, aggregate
from journal import first_touch, limit_fill_outcome
from strategy import cost_model, sided_ok, scalp_direction, ext_atr, exec_legs
import sources

HIST = {"status": "never", "error": None, "bars": 0, "gaps": [], "first": None, "last": None, "synced_at": None, "pages": 0}
BACKTEST = {"computed_at": None}


async def sync_history(days=30):
    res = 300
    now = int(now_utc().timestamp())
    floor = now - days * 86400
    floor -= floor % res
    last = await db.history_5m.find_one(sort=[("_id", -1)])
    start = max(floor, last["_id"] - 3 * res) if last else floor
    pages = 0
    try:
        while start < now:
            end = min(start + 1999 * res, now)
            rows = await sources.delta_candles_tf("5m", start=start, end=end)
            pages += 1
            bars, _ = sources._complete(rows, res)
            for b in bars:
                await db.history_5m.update_one({"_id": b["t"]}, {"$set": {"o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]}}, upsert=True)
            start = end + res
        HIST.update(status="ok", error=None)
    except Exception as e:
        HIST.update(status="error", error=f"{type(e).__name__}: {str(e)[:200]}")
    await db.history_5m.delete_many({"_id": {"$lt": floor - 7 * 86400}})
    ts = [d["_id"] async for d in db.history_5m.find({"_id": {"$gte": floor}}, {"_id": 1}).sort("_id", 1)]
    gaps = [{"after": iso(a + res), "before": iso(b), "missing_bars": (b - a) // res - 1} for a, b in zip(ts, ts[1:]) if b - a != res]
    HIST.update(bars=len(ts), gaps=gaps[:50], gap_count=len(gaps), first=iso(ts[0]) if ts else None,
                last=iso(ts[-1] + res) if ts else None, synced_at=iso(now_utc()), pages=pages, expected=(now - floor) // res)


async def load_bars(since=None):
    q = {"_id": {"$gte": since}} if since else {}
    return [{"t": d["_id"], "o": d["o"], "h": d["h"], "l": d["l"], "c": d["c"], "v": d["v"]} async for d in db.history_5m.find(q).sort("_id", 1)]


async def evaluate_snapshots():
    first = await db.history_5m.find_one(sort=[("_id", 1)])
    bars = await load_bars()
    async for sn in db.validation_snapshots.find({"frozen": {"$ne": True}}):
        cap = int(sn["capture_ts"])
        if not sided_ok(sn["direction"], sn["entry"], sn["stop"], sn["target"]):
            out = {"outcome": "INVALID SNAPSHOT", "at": None, "frozen": False}
        elif HIST["status"] == "error" and not bars:
            out = {"outcome": "SOURCE ERROR", "at": None, "frozen": False, "note": HIST["error"]}
        elif not first or cap < first["_id"]:
            out = {"outcome": "HISTORY GAP", "at": None, "frozen": False, "note": "capture precedes stored history"}
        else:
            out = first_touch(sn["direction"], sn["stop"], sn["target"], bars, cap)
        await db.validation_snapshots.update_one({"_id": sn["_id"], "version": sn["version"]}, {"$set": {
            "outcome": out["outcome"], "outcome_at": out.get("at"), "frozen": out.get("frozen", False),
            "outcome_note": out.get("note"), "levels_evaluated": [sn["entry"], sn["stop"], sn["target"]], "evaluated_at": iso(now_utc())}})


def _pre(bars):
    c = [b["c"] for b in bars]
    e20, e50 = ema_series(c, 20), ema_series(c, 50)
    n = len(bars)
    atr = [None] * n
    trs = [bars[0]["h"] - bars[0]["l"]] + [max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"])) for p, b in zip(bars, bars[1:])]
    if n > 15:
        a = sum(trs[1:15]) / 14
        atr[14] = a
        for i in range(15, n):
            a = (a * 13 + trs[i]) / 14
            atr[i] = a
    return {"c": c, "e20": e20, "e50": e50, "atr": atr}


def _metric(bars, P, i):
    if i < 55 or P["e50"][i] is None or P["atr"][i] is None:
        return None
    w = bars[i - 19:i + 1]
    pr = bars[i - 20:i]
    vs = sum(b["v"] for b in w)
    vp = sum(b["v"] for b in pr) / 20
    return {"close": bars[i]["c"], "high": bars[i]["h"], "low": bars[i]["l"], "ema20": P["e20"][i], "ema50": P["e50"][i],
            "ema20_prev": P["e20"][i - 1], "atr14": P["atr"][i], "vwap20": sum((b["h"] + b["l"] + b["c"]) / 3 * b["v"] for b in w) / vs if vs else None,
            "prior20_high": max(b["h"] for b in pr), "prior20_low": min(b["l"] for b in pr), "volume_ratio": bars[i]["v"] / vp if vp else None}


def _trend(m):
    c, e20, e50, p = m["close"], m["ema20"], m["ema50"], m["ema20_prev"]
    return "UP" if c > e20 > e50 and e20 > p else "DOWN" if c < e20 < e50 and e20 < p else "MIXED"


def _stats(trades, missed=0):
    win = [t for t in trades if t["outcome"] == "TARGET FIRST"]
    loss = [t for t in trades if t["outcome"] == "STOP FIRST"]
    dec = len(win) + len(loss)
    eq = pk = dd = 0.0
    for t in trades:
        if t["outcome"] in ("TARGET FIRST", "STOP FIRST"):
            eq += t["net_r"] if t["outcome"] == "TARGET FIRST" else -1
            pk = max(pk, eq); dd = min(dd, eq - pk)
    return {"signals": len(trades), "missed_fills": missed, "target_first": len(win), "stop_first": len(loss),
            "ambiguous": sum(t["outcome"] in ("SAME CANDLE REVIEW", "CAPTURE CANDLE REVIEW") for t in trades),
            "pending_or_gap": sum(t["outcome"] in ("PENDING", "HISTORY GAP") for t in trades),
            "hit_rate": len(win) / dec if dec else None, "expectancy_net_r": (eq / dec) if dec else None,
            "total_net_r": eq if dec else None, "max_drawdown_r": dd if dec else None,
            "breakeven_hit_rate": (1 / (1 + (sum(t["net_r"] for t in trades) / len(trades)))) if trades else None,
            "long": sum(t["direction"] == "LONG" for t in trades), "short": sum(t["direction"] == "SHORT" for t in trades),
            "recent": trades[-15:][::-1]}


def _run_strategy(strat, b5, lo, lres, hi, hres, s, tick, entry_mode=None):
    PL, PH = _pre(lo), _pre(hi)
    hts = [b["t"] for b in hi]
    trades, missed, busy_until, j = [], 0, 0, 0
    setups = {}  # bar index -> base scalp setup direction (for the confirmation rule)
    need, look_bars = int(s["scalp_confirm_signals"]), int(s["scalp_confirm_lookback_min"]) // 5
    legs = exec_legs(s, strat, entry_mode) if strat == "scalp" else None
    limit = bool(legs) and legs["entry_type"] == "POST-ONLY LIMIT"
    wait = int(s["scalp_limit_wait_bars"])
    for i in range(len(lo)):
        close_t = lo[i]["t"] + lres
        while j + 1 < len(hts) and hts[j + 1] + hres <= close_t:
            j += 1
        if not hts or hts[j] + hres > close_t:
            continue
        m, mh = _metric(lo, PL, i), _metric(hi, PH, j)
        if not m or not mh or not m["vwap20"] or not m["volume_ratio"]:
            continue
        ht = _trend(mh)
        c, e20, e50, p = m["close"], m["ema20"], m["ema50"], m["ema20_prev"]
        d = None
        if strat == "scalp":
            d = scalp_direction(m, ht, s)
            if d:
                setups[i] = d
            if close_t <= busy_until or not d:
                continue
            if need > 0 and sum(1 for k in range(i - look_bars, i) if setups.get(k) == d) < need:
                continue
            x = ext_atr(d, m)
            if x is None or x > s["scalp_max_ext_atr"]:
                continue
            sf, tr = s["scalp_stop"], s["scalp_target_r"]
            if limit:
                off = s["scalp_limit_offset_atr"] * m["atr14"]
                entry = tick_floor(c - off, tick) if d == "LONG" else tick_ceil(c + off, tick)
            else:
                entry = tick_ceil(c + tick, tick) if d == "LONG" else tick_floor(c - tick, tick)
        else:
            if close_t <= busy_until:
                continue
            if c > e20 > e50 and e20 > p and ht == "UP" and c > m["prior20_high"] and c > m["vwap20"] and m["volume_ratio"] >= s["swing_volume_ratio"]:
                d = "LONG"
            elif c < e20 < e50 and e20 < p and ht == "DOWN" and c < m["prior20_low"] and c < m["vwap20"] and m["volume_ratio"] >= s["swing_volume_ratio"]:
                d = "SHORT"
            sf, tr = s["swing_stop"], s["swing_target_r"]
            entry = (tick_ceil(max(c, m["prior20_high"] + tick), tick) if d == "LONG" else tick_floor(min(c, m["prior20_low"] - tick), tick)) if d else None
        if not d or abs(entry - c) > m["atr14"]:
            continue
        if d == "LONG":
            stop = tick_floor(entry * (1 - sf), tick); target = tick_floor(entry + (entry - stop) * tr, tick)
        else:
            stop = tick_ceil(entry * (1 + sf), tick); target = tick_ceil(entry - (stop - entry) * tr, tick)
        cm = cost_model(d, entry, stop, target, s, legs=legs)
        if not cm["net_r"] or cm["net_r"] <= s[f"{strat}_min_net_r"]:
            continue
        after = [x for x in b5 if x["t"] >= close_t][:4000]
        if limit:
            o = limit_fill_outcome(d, entry, stop, target, tick, after, lo[i]["t"], wait, lres)
            if o["fill_status"] == "MISSED":
                missed += 1
                busy_until = int(datetime_ts(o["at"]))
                continue
        else:
            o = first_touch(d, stop, target, after, close_t, capture_review=False)
        end_t = int(now_utc().timestamp()) if o["outcome"] in ("PENDING", "HISTORY GAP") or not o.get("at") else int(datetime_ts(o["at"]))
        busy_until = end_t
        trades.append({"time": iso(close_t), "direction": d, "entry": entry, "stop": stop, "target": target, "net_r": cm["net_r"],
                       "outcome": o["outcome"], "entry_type": legs["entry_type"] if legs else "MARKET"})
    return _stats(trades, missed)


def run_backtest(b5, s, tick):
    """Rule backtest on stored completed 5m bars. Scalp is run twice on the same bars:
    'market' (market entry and exits) and 'limit' (post-only limit entry, limit take-profit, stop-market stop).
    The active scenario (scalp_entry_mode) is also copied to the top level for older UI code."""
    out = {}
    specs = {"scalp": (b5, 300, aggregate(b5, 300, 900), 900), "swing": (aggregate(b5, 300, 3600), 3600, aggregate(b5, 300, 14400), 14400)}
    for strat, (lo, lres, hi, hres) in specs.items():
        if strat == "scalp":
            market = _run_strategy(strat, b5, lo, lres, hi, hres, s, tick, entry_mode=0)
            lim = _run_strategy(strat, b5, lo, lres, hi, hres, s, tick, entry_mode=1)
            active = "limit" if s["scalp_entry_mode"] >= 0.5 else "market"
            out[strat] = {**(lim if active == "limit" else market), "mode": active, "market": market, "limit": lim,
                          "note": "Limit fills only when a later completed 5m bar trades through the limit by 1 tick within "
                                  f"{int(s['scalp_limit_wait_bars'])} bar(s); stop exits are stop-market (taker + slippage)."}
        else:
            out[strat] = _run_strategy(strat, b5, lo, lres, hi, hres, s, tick)
    return out


def datetime_ts(s):
    from core import parse_iso
    return parse_iso(s).timestamp()


def mtf_alignment(frames):
    rows = []
    for tf, m in frames.items():
        if m.get("valid"):
            rows.append({"tf": tf, "trend": _trend(m), "close": m["close"], "ema20": m["ema20"], "ema50": m["ema50"],
                         "above_vwap": m["close"] > m["vwap20"], "volume_ratio": m["volume_ratio"], "bar_close": iso(m["bar_close"])})
        else:
            rows.append({"tf": tf, "trend": "UNAVAILABLE", "issues": m.get("issues")})
    ups = sum(r["trend"] == "UP" for r in rows); dns = sum(r["trend"] == "DOWN" for r in rows)
    return {"rows": rows, "up": ups, "down": dns,
            "summary": f"{ups} of {len(rows)} timeframes UP, {dns} DOWN (Delta BTCUSD completed candles)"}
