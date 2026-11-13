"""Day type – is BTC in a TREND or a RANGE right now? (Delta Exchange India BTCUSD, 5-minute candles)

A plain, standard desk method that only describes what price has done over the last 12 hours:
  1. Efficiency   net move ÷ total path walked (15-minute steps). High = one-directional, low = back-and-forth.
  2. Move size    net move ÷ average daily range (ADR, last 10 full IST days).
  3. VWAP side    share of 15-minute closes on the same side of the 12-hour VWAP as the latest price.
  4. Range        12-hour high–low ÷ the range normally expected in 12 hours (ADR × √0.5).
  5. Structure    last 4 hours stepping the same way (higher highs+lows, or lower highs+lows).
TREND = 4–5 of these agree (or 3 including both efficiency and move size); RANGE = at most 1 agrees and
price is clearly rotating; anything else = MIXED (unclear).

It is a DESCRIPTION of the market, not a prediction. Its measured track record (11 months of Delta
candles, 1,270 rebuilt scalp signals) is in daytype_evidence.json and is shown on the site next to it.
"""
import asyncio
import json
import logging
import math
import os

from core import db, now_utc, iso

log = logging.getLogger("daytype")
IST_SEC = 19800
WIN = 144  # 12 hours of 5-minute bars
KEEP_DAYS = 26
T_ER, T_MOVE, T_SIDE, T_RNG = 0.30, 0.50, 0.70, 1.10
R_ER, R_MOVE, R_SIDE = 0.15, 0.25, 0.60
STATE = {"bars": [], "now": None, "updated": None, "error": None}
LABEL = {"TREND_UP": "Trend (up)", "TREND_DOWN": "Trend (down)", "RANGE": "Range", "MIXED": "Unclear", "NA": "No data"}


def ist_day(ts):
    return (ts + IST_SEC) // 86400


def daily_ranges(b5):
    d = {}
    for b in b5:
        x = d.setdefault(ist_day(b["t"]), [b["h"], b["l"], 0, b["o"], b["c"]])
        x[0] = max(x[0], b["h"]); x[1] = min(x[1], b["l"]); x[2] += 1; x[4] = b["c"]
    return d


def adr(dr, day, n=10, min_bars=280):
    v = [dr[day - k][0] - dr[day - k][1] for k in range(1, n + 4) if day - k in dr and dr[day - k][2] >= min_bars][:n]
    return sum(v) / len(v) if len(v) >= 5 else None


def classify(win, adr_v):
    """win: the last 144 completed 5-minute bars (t,o,h,l,c,v), oldest first. adr_v: average daily range in USD."""
    if len(win) < WIN or not adr_v or adr_v <= 0:
        return {"type": "NA", "label": LABEL["NA"], "dir": None, "score": 0, "measures": [], "at": None}
    win = win[-WIN:]
    pts = [win[0]["o"]] + [win[i]["c"] for i in range(2, WIN, 3)]
    last = win[-1]["c"]
    net = last - pts[0]
    path = sum(abs(a - b) for a, b in zip(pts, pts[1:]))
    er = abs(net) / path if path else 0.0
    move = abs(net) / adr_v
    vs = sum(b["v"] for b in win)
    vwap = sum((b["h"] + b["l"] + b["c"]) / 3 * b["v"] for b in win) / vs if vs > 0 else sum(pts) / len(pts)
    sgn = 1 if last >= vwap else -1
    side = sum(1 for p in pts[1:] if (p - vwap) * sgn > 0) / (len(pts) - 1)
    hi, lo = max(b["h"] for b in win), min(b["l"] for b in win)
    rng = (hi - lo) / (adr_v * math.sqrt(0.5))
    blocks = [(max(b["h"] for b in win[k:k + 12]), min(b["l"] for b in win[k:k + 12])) for k in range(WIN - 48, WIN, 12)]
    up = sum(1 for a, b in zip(blocks, blocks[1:]) if b[0] > a[0] and b[1] > a[1])
    dn = sum(1 for a, b in zip(blocks, blocks[1:]) if b[0] < a[0] and b[1] < a[1])
    struct = up if net > 0 else dn
    votes = [er >= T_ER, move >= T_MOVE, side >= T_SIDE, rng >= T_RNG, struct >= 2]
    score = sum(votes)
    rvotes = sum([er <= R_ER, move <= R_MOVE, side <= R_SIDE])
    if score >= 4 or (score == 3 and votes[0] and votes[1]):
        typ = "TREND_UP" if net > 0 else "TREND_DOWN"
    elif score <= 1 and rvotes >= 2:
        typ = "RANGE"
    else:
        typ = "MIXED"
    way = "up" if net > 0 else "down"
    ms = [
        {"key": "er", "label": "Efficiency (net move ÷ path)", "value": round(er, 2), "trend_at": T_ER, "range_at": R_ER, "vote": bool(votes[0]),
         "text": f"price kept {er * 100:.0f}% of the distance it travelled"},
        {"key": "move", "label": "Net move vs average daily range", "value": round(move, 2), "trend_at": T_MOVE, "range_at": R_MOVE, "vote": bool(votes[1]),
         "text": f"{net:+,.0f} USD in 12 hours; a normal full day is {adr_v:,.0f} USD"},
        {"key": "side", "label": "Time on one side of VWAP", "value": round(side, 2), "trend_at": T_SIDE, "range_at": R_SIDE, "vote": bool(votes[2]),
         "text": f"{side * 100:.0f}% of the last 12 hours {'above' if sgn > 0 else 'below'} the 12-hour VWAP {vwap:,.0f}"},
        {"key": "rng", "label": "12-hour range vs normal", "value": round(rng, 2), "trend_at": T_RNG, "range_at": None, "vote": bool(votes[3]),
         "text": f"range {hi - lo:,.0f} USD ({lo:,.0f}–{hi:,.0f}); normal for 12 hours is {adr_v * math.sqrt(0.5):,.0f}"},
        {"key": "struct", "label": "Hourly structure (last 4 hours)", "value": struct, "trend_at": 2, "range_at": None, "vote": bool(votes[4]),
         "text": f"{struct} of 3 hourly steps moved {way} with both high and low"},
    ]
    return {"type": typ, "label": LABEL[typ], "dir": ("UP" if net > 0 else "DOWN") if typ.startswith("TREND") else None, "score": score, "range_votes": rvotes,
            "measures": ms, "high": hi, "low": lo, "vwap": round(vwap, 1), "adr": round(adr_v, 1), "price": last, "at": iso_ts(win[-1]["t"] + 300)}


def iso_ts(ts):
    from datetime import datetime, timezone
    return iso(datetime.fromtimestamp(ts, timezone.utc))


def playbook(day, signal_dir):
    """Which way a scalp signal is traded under 'Auto by day type'. FOLLOW / PLAN_A / STAND_ASIDE."""
    t = (day or {}).get("type")
    if t == "RANGE":
        return "PLAN_A"
    if t in ("TREND_UP", "TREND_DOWN"):
        return "FOLLOW" if (t == "TREND_UP") == (signal_dir == "LONG") else "STAND_ASIDE"
    return "STAND_ASIDE"


def eod_label(day_bars, adr_v):
    """Hindsight label for one finished IST day (used in the audit table only)."""
    if len(day_bars) < 200 or not adr_v:
        return "NA"
    pts = [day_bars[0]["o"]] + [day_bars[i]["c"] for i in range(11, len(day_bars), 12)]
    path = sum(abs(a - b) for a, b in zip(pts, pts[1:]))
    o, c = day_bars[0]["o"], day_bars[-1]["c"]
    hi, lo = max(b["h"] for b in day_bars), min(b["l"] for b in day_bars)
    er = abs(c - o) / path if path else 0
    body = abs(c - o) / (hi - lo) if hi > lo else 0
    if er >= 0.40 or (body >= 0.6 and (hi - lo) >= adr_v):
        return "TREND_UP" if c > o else "TREND_DOWN"
    if er <= 0.20 and body <= 0.35:
        return "RANGE"
    return "MIXED"


def at(ts, bars=None):
    """Day type using only candles completed before `ts` (epoch seconds)."""
    bars = STATE["bars"] if bars is None else bars
    if not bars:
        return classify([], None)
    import bisect
    T = [b["t"] for b in bars]
    j = bisect.bisect_right(T, ts - 300)
    win = bars[max(0, j - WIN):j]
    if len(win) < WIN or win[-1]["t"] - win[0]["t"] != (WIN - 1) * 300:
        return classify([], None)
    return classify(win, adr(daily_ranges(bars[:j]), ist_day(ts)))


async def _fetch(start, end):
    from sources import http_get, DELTA
    out, s = [], start
    while s < end:
        e = min(end, s + 1900 * 300)
        r = (await http_get(f"{DELTA}/v2/history/candles", params={"symbol": "BTCUSD", "resolution": "5m", "start": s, "end": e})).json()
        out += [{"t": int(c["time"]), "o": float(c["open"]), "h": float(c["high"]), "l": float(c["low"]), "c": float(c["close"]), "v": float(c.get("volume") or 0)} for c in r.get("result") or []]
        s = e
    return out


async def refresh():
    now = int(now_utc().timestamp())
    now -= now % 300
    have = {b["t"]: b for b in STATE["bars"]}
    start = max(have) - 600 if have else now - KEEP_DAYS * 86400
    for b in await _fetch(start, now):
        if b["t"] + 300 <= now:
            have[b["t"]] = b
    floor = now - KEEP_DAYS * 86400
    STATE["bars"] = [have[t] for t in sorted(have) if t >= floor]
    STATE["now"] = at(now)
    STATE["updated"] = iso(now_utc())
    STATE["error"] = None
    cur = STATE["now"]
    if cur["type"] != "NA":  # hourly log = the scanner's score card for this call
        hour = now - now % 3600
        await db.daytype_log.update_one({"_id": hour}, {"$setOnInsert": {"type": cur["type"], "score": cur["score"], "price": cur["price"], "high": cur["high"], "low": cur["low"], "adr": cur["adr"]}}, upsert=True)


async def tag_signal(sid, captured_ts, direction):
    """Day type frozen at the moment a signal was captured (computed once, then stored)."""
    doc = await db.signal_tags.find_one({"_id": sid})
    if doc:
        return doc
    d = at(captured_ts)
    if d["type"] == "NA":
        return {"_id": sid, "type": "NA", "label": LABEL["NA"], "playbook": "STAND_ASIDE", "score": 0}
    doc = {"_id": sid, "type": d["type"], "label": d["label"], "score": d["score"], "playbook": playbook(d, direction),
           "er": d["measures"][0]["value"], "move": d["measures"][1]["value"], "at": iso_ts(captured_ts)}
    await db.signal_tags.update_one({"_id": sid}, {"$setOnInsert": doc}, upsert=True)
    return doc


def evidence():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "daytype_evidence.json")) as f:
            return json.load(f)
    except Exception:
        return None


def public():
    cur = STATE["now"] or classify([], None)
    return {**cur, "updated": STATE["updated"], "error": STATE["error"],
            "playbook": {"LONG": playbook(cur, "LONG"), "SHORT": playbook(cur, "SHORT")},
            "method": "Last 12 hours of Delta BTCUSD 5-minute candles: efficiency, move size, VWAP side, range and hourly structure."}


async def loop():
    fails = 0
    while True:
        try:
            await refresh()
            fails = 0
        except Exception as e:
            fails += 1
            STATE["error"] = str(e)[:200]
            log.warning("day type refresh failed: %s", e)
        await asyncio.sleep(60 if fails < 3 else 300)
