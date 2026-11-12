"""Plan A – "4C reverse 1:2 + 6-hour exit", scalps only, one trade at a time.

Re-creates the rules of the BTC_Signal_Scenario_PnL workbook (Method sheet) automatically, 24/7,
from Delta Exchange India BTCUSD 1-minute candles:

Original fill (POST-ONLY limit at the model entry):
  * fills on the first 1-minute candle at/after the capture time whose low (LONG) / high (SHORT)
    reaches the entry; in the capture minute a touch only at the minute's opening price is ignored;
  * the limit is valid for 10 minutes (or the signal's own valid_until) — otherwise NOT FILLED.
Original result: first later minute touching stop or target; both in one minute → STOP; in the fill
  minute only the stop counts.
Plan A (taken only when the previous Plan A trade has already exited):
  * opposite direction, same entry price and fill minute, same quantity;
  * stop = entry ∓ model risk distance, target = entry ± 2 × risk (1 : 2);
  * same touch rules; if neither is hit within 6 hours, exit at the close of the 1-minute candle
    360 minutes after the fill (TIME, taker fee).
Fees (display only; the Google Sheet uses its own Inputs sheet): entry taker, exit maker on TARGET
else taker, ×(1+GST).
"""
import asyncio
import logging
from datetime import datetime, timezone

from core import db, now_utc, iso, parse_iso, IST
from sources import http_get, DELTA

log = logging.getLogger("plan_a")
MIN = 60
VALID_SEC = 600
HOLD_SEC = 6 * 3600
FEES = {"maker": 0.0002, "taker": 0.0005, "gst": 0.18}
STATE = {"computed_at": None, "rows": [], "summary": {}, "error": None, "live_price": None}


async def _fetch_1m(start, end):
    """Fetch completed 1m candles [start, end) from Delta (≤ 2000 per request)."""
    out = []
    s = start
    while s < end:
        e = min(end, s + 1900 * MIN)
        r = (await http_get(f"{DELTA}/v2/history/candles", params={"symbol": "BTCUSD", "resolution": "1m", "start": s, "end": e})).json()
        for c in r.get("result") or []:
            out.append({"t": int(c["time"]), "o": float(c["open"]), "h": float(c["high"]), "l": float(c["low"]), "c": float(c["close"])})
        s = e
    return out


async def candles(start, end):
    """Cached 1m candles; only completed minutes are stored."""
    start -= start % MIN
    now = int(now_utc().timestamp())
    end = min(end, now - now % MIN)  # exclude the running minute
    have = {c["_id"]: c async for c in db.candles_1m.find({"_id": {"$gte": start, "$lt": end}})}
    need = [t for t in range(start, end, MIN) if t not in have]
    if need:
        got = await _fetch_1m(need[0], need[-1] + MIN)
        for c in got:
            if c["t"] + MIN <= now and c["t"] not in have:
                have[c["t"]] = {"_id": c["t"], **{k: c[k] for k in "ohlc"}}
                await db.candles_1m.update_one({"_id": c["t"]}, {"$set": {k: c[k] for k in "ohlc"}}, upsert=True)
    return [{"t": t, **{k: have[t][k] for k in "ohlc"}} for t in sorted(have) if start <= t < end]


def _touch_entry(direction, c, entry, capture_minute):
    if direction == "LONG":
        hit = c["l"] <= entry
        return hit and (c["t"] != capture_minute or c["o"] > entry or c["l"] < c["o"])
    hit = c["h"] >= entry
    return hit and (c["t"] != capture_minute or c["o"] < entry or c["h"] > c["o"])


def _first_exit(direction, stop, target, bars, fill_t, time_exit=None):
    """Returns (result, exit_bar). result in TARGET/STOP/TIME or None (still open)."""
    for c in bars:
        if c["t"] < fill_t:
            continue
        if time_exit is not None and c["t"] >= fill_t + time_exit:
            return "TIME", c
        hs = c["l"] <= stop if direction == "LONG" else c["h"] >= stop
        ht = c["h"] >= target if direction == "LONG" else c["l"] <= target
        if c["t"] == fill_t:
            if hs:
                return "STOP", c
            continue
        if hs:
            return "STOP", c
        if ht:
            return "TARGET", c
    return None, None


def _fee(price, qty, kind):
    return price * qty * FEES[kind] * (1 + FEES["gst"])


def _ist(ts):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(IST).strftime("%Y-%m-%d %H:%M") if ts else ""


async def compute():
    sigs = [s async for s in db.signals.find({"strategy": "scalp"}).sort("captured_at", 1)]
    now = int(now_utc().timestamp())
    rows = []
    if sigs:
        first = int(parse_iso(sigs[0]["captured_at"]).timestamp())
        bars = await candles(first - MIN, now)
    else:
        bars = []
    live = bars[-1]["c"] if bars else None
    busy_until = None  # exit time of the last Plan A trade taken; None = free; -1 = still open
    for s in sigs:
        cap = int(parse_iso(s["captured_at"]).timestamp())
        cap_min = cap - cap % MIN
        valid_end = int(parse_iso(s["valid_until"]).timestamp()) if s.get("valid_until") else cap + VALID_SEC
        d0, e, st, tg, q = s["direction"], float(s["entry"]), float(s["stop"]), float(s["target"]), float(s.get("qty_btc") or 0)
        row = {"signal_id": s["_id"], "captured_ist": _ist(cap), "model_dir": d0, "entry": e, "model_stop": st, "model_target": tg, "qty": q}
        fill = next((c for c in bars if cap_min <= c["t"] < valid_end and _touch_entry(d0, c, e, cap_min)), None)
        if not fill:
            row["fill_status"] = "WAITING" if now < valid_end else "NOT FILLED"
            rows.append(row)
            continue
        ft = fill["t"]
        row["fill_status"] = "FILLED"
        row["fill_ist"] = _ist(ft)
        o_res, o_bar = _first_exit(d0, st, tg, bars, ft)
        row["model_result"] = o_res or "OPEN"
        row["model_exit_ist"] = _ist(o_bar["t"]) if o_bar else ""
        # Plan A
        d = "SHORT" if d0 == "LONG" else "LONG"
        risk = abs(e - st)
        pa_stop = e - risk if d == "LONG" else e + risk
        pa_tgt = e + 2 * risk if d == "LONG" else e - 2 * risk
        res, bar = _first_exit(d, pa_stop, pa_tgt, bars, ft, HOLD_SEC)
        exit_px = None
        if res == "TARGET":
            exit_px = pa_tgt
        elif res == "STOP":
            exit_px = pa_stop
        elif res == "TIME":
            exit_px = bar["c"]
        exit_t = bar["t"] if bar else None
        taken = busy_until is None or (busy_until != -1 and busy_until <= ft)
        gross = ((exit_px or live or e) - e) * q * (1 if d == "LONG" else -1)
        fees = _fee(e, q, "taker") + _fee(exit_px or live or e, q, "maker" if res == "TARGET" else "taker")
        row.update(plan_dir=d, plan_stop=pa_stop, plan_target=pa_tgt, plan_result=res or "OPEN", exit_ist=_ist(exit_t),
                   exit_price=exit_px, time_exit_close=bar["c"] if res == "TIME" else None,
                   trigger_high=bar["h"] if bar else None, trigger_low=bar["l"] if bar else None,
                   taken="Yes" if taken else "Skip", gross=gross, fees=fees, net=gross - fees,
                   note=("Closed 6 h after fill at the 1-minute close (taker fee)" if res == "TIME" else
                         "Stop hit in the fill minute" if bar and bar["t"] == ft else ""))
        if taken:
            busy_until = -1 if res is None else exit_t
        rows.append(row)
    taken = [r for r in rows if r.get("taken") == "Yes"]
    closed = [r for r in taken if r["plan_result"] != "OPEN"]
    eq, peak, dd = 0.0, 0.0, 0.0
    for r in taken:
        eq += r["net"]; peak = max(peak, eq); dd = min(dd, eq - peak)
    wins = [r for r in closed if r["net"] > 0]
    STATE.update(computed_at=iso(now_utc()), rows=rows, live_price=live, error=None, summary={
        "signals": len(rows), "filled": sum(1 for r in rows if r.get("fill_status") == "FILLED"),
        "taken": len(taken), "skipped": sum(1 for r in rows if r.get("taken") == "Skip"),
        "open": sum(1 for r in taken if r["plan_result"] == "OPEN"),
        "target": sum(1 for r in closed if r["plan_result"] == "TARGET"), "stop": sum(1 for r in closed if r["plan_result"] == "STOP"),
        "time": sum(1 for r in closed if r["plan_result"] == "TIME"),
        "win_rate": len(wins) / len(closed) if closed else None, "net": sum(r["net"] for r in taken), "max_drawdown": dd})
    return STATE


async def loop():
    while True:
        try:
            await compute()
        except Exception as e:
            STATE["error"] = str(e)[:200]
            log.exception("plan A compute failed")
        await asyncio.sleep(120)


FEED_COLS = ["signal_id", "plan_dir", "fill_ist", "exit_ist", "entry", "plan_stop", "plan_target", "plan_result", "qty",
             "time_exit_close", "trigger_high", "trigger_low", "note"]


def feed_csv():
    """CSV for the Google Sheet (IMPORTDATA). Line 1: status. Line 3+: one row per FILLED scalp signal."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["updated_ist", _ist(int(parse_iso(STATE["computed_at"]).timestamp())) if STATE["computed_at"] else "", "live_price", STATE.get("live_price") or ""])
    w.writerow([])
    w.writerow(FEED_COLS)
    for r in STATE["rows"]:
        if r.get("fill_status") != "FILLED":
            continue
        w.writerow(["" if r.get(k) is None else r.get(k) for k in FEED_COLS])
    return buf.getvalue()
