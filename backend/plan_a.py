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
import os
from datetime import datetime, timezone

from core import db, now_utc, iso, parse_iso, IST
from sources import http_get, DELTA
import daytype

log = logging.getLogger("plan_a")
MIN = 60
VALID_SEC = 600
HOLD_SEC = 6 * 3600
FEES = {"maker": 0.0002, "taker": 0.0005, "gst": 0.18}
STATE = {"computed_at": None, "rows": [], "summary": {}, "error": None, "live_price": None, "books": {}}
SINCE_IST = os.environ.get("JOURNAL_START", "2026-11-13")
JOURNAL_RR = 2.0  # Trade Journal results: target = 2 × stop distance for every signal (admin request, 14 Nov 2026)  # "from today" start of the journal comparison


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


def _seed():
    """Scalp signals from the original workbook (03–12 Nov 2026), so Plan A history is continuous.
    Signals captured by this server are added on top (same ID → the server's copy wins)."""
    import json
    import os
    from datetime import timedelta
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "plan_a_seed.json")) as f:
            fx = json.load(f)
    except Exception:
        return []
    out = []
    for s in fx.get("signals", []):
        if not s["signal_id"].startswith("SCALP"):
            continue
        cap = datetime.fromisoformat(s["captured_ist"]).replace(tzinfo=IST).astimezone(timezone.utc)
        out.append({"_id": s["signal_id"], "strategy": "scalp", "direction": s["direction"], "entry": s["entry"], "stop": s["stop"],
                    "target": s["target"], "qty_btc": s["qty"], "captured_at": iso(cap), "source": "workbook"})
    return out


async def compute():
    live_sigs = {s["_id"]: s async for s in db.signals.find({"strategy": "scalp"})}
    sigs = sorted([s for s in _seed() if s["_id"] not in live_sigs] + list(live_sigs.values()), key=lambda s: s["captured_at"])
    now = int(now_utc().timestamp())
    rows = []
    if sigs:
        first = int(parse_iso(sigs[0]["captured_at"]).timestamp())
        bars = await candles(first - MIN, now)
    else:
        bars = []
    live = bars[-1]["c"] if bars else None
    idx = {c["t"]: i for i, c in enumerate(bars)}
    busy_until = None  # exit time of the last Plan A trade taken; None = free; -1 = still open
    for s in sigs:
        cap = int(parse_iso(s["captured_at"]).timestamp())
        cap_min = cap - cap % MIN
        valid_end = int(parse_iso(s["valid_until"]).timestamp()) if s.get("valid_until") else cap + VALID_SEC
        d0, e, st, tg, q = s["direction"], float(s["entry"]), float(s["stop"]), float(s["target"]), float(s.get("qty_btc") or 0)
        row = {"signal_id": s["_id"], "captured_ist": _ist(cap), "cap_t": cap, "model_dir": d0, "entry": e, "model_stop": st, "model_target": tg, "qty": q}
        tag = await daytype.tag_signal(s["_id"], cap, d0)
        row.update(day_type=tag.get("type"), day_label=tag.get("label"), playbook=tag.get("playbook"))
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
        # Follow the signal (Trade Journal way): the signal's own direction, stop and target, same 6-hour exit
        # Trade Journal book: the signal's own direction, entry and stop, target at JOURNAL_RR × stop distance
        # for every signal (older signals were captured with a 1.5R target; scored here at 1:2 like the rest)
        f_tg = round((e + (1 if d0 == "LONG" else -1) * JOURNAL_RR * risk) * 2) / 2
        f_res, f_bar = _first_exit(d0, st, f_tg, bars, ft, HOLD_SEC)
        f_px = f_tg if f_res == "TARGET" else st if f_res == "STOP" else f_bar["c"] if f_res == "TIME" else None
        f_gross = ((f_px or live or e) - e) * q * (1 if d0 == "LONG" else -1)
        f_fees = _fee(e, q, "taker") + _fee(f_px or live or e, q, "maker" if f_res == "TARGET" else "taker")
        i0 = idx.get(ft)
        seg = bars[i0:i0 + HOLD_SEC // MIN + 1] if i0 is not None else []
        if seg and risk > 0:
            hh, ll = max(c["h"] for c in seg), min(c["l"] for c in seg)
            row.update(mfe=((hh - e) if d0 == "LONG" else (e - ll)) / risk, mae=((e - ll) if d0 == "LONG" else (hh - e)) / risk)
        row.update(fill_t=ft, plan_exit_t=exit_t, follow_result=f_res or "OPEN", follow_exit_t=f_bar["t"] if f_bar else None,
                   follow_exit_ist=_ist(f_bar["t"]) if f_bar else "", follow_exit_price=f_px, follow_net=f_gross - f_fees, follow_target=f_tg)
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
    since = int(datetime.fromisoformat(SINCE_IST).replace(tzinfo=IST).timestamp())
    STATE["books"] = {"since_date": SINCE_IST, "all": {m: book(rows, m) for m in BOOKS}, "since": {m: book(rows, m, since) for m in BOOKS}}
    return STATE


BOOKS = ("follow", "plan_a", "switch")


def book(rows, mode, since=None):
    """One trade at a time, same fill rule, same 6-hour exit, same fees.
    follow = the signal's own direction / stop / target; plan_a = reverse 1:2;
    switch = day type at capture decides: RANGE → plan_a, TREND with the signal → follow, otherwise no trade."""
    busy, eq, peak, dd = None, 0.0, 0.0, 0.0
    c = {"signals": 0, "filled": 0, "taken": 0, "skipped": 0, "stood_aside": 0, "open": 0, "target": 0, "stop": 0, "time": 0, "wins": 0, "net": 0.0}
    curve, picks = [], {}
    for r in rows:
        if since and r["cap_t"] < since:
            continue
        c["signals"] += 1
        if r.get("fill_status") != "FILLED":
            continue
        c["filled"] += 1
        pick = mode if mode != "switch" else {"FOLLOW": "follow", "PLAN_A": "plan_a"}.get(r.get("playbook"))
        if not pick:
            c["stood_aside"] += 1
            picks[r["signal_id"]] = "aside"
            continue
        res, xt, xi, net = (r["follow_result"], r["follow_exit_t"], r["follow_exit_ist"], r["follow_net"]) if pick == "follow" else (r["plan_result"], r["plan_exit_t"], r["exit_ist"], r["net"])
        if busy is not None and (busy == -1 or busy > r["fill_t"]):
            c["skipped"] += 1
            picks[r["signal_id"]] = "busy"
            continue
        busy = -1 if res == "OPEN" else xt
        picks[r["signal_id"]] = pick
        c["taken"] += 1
        c["net"] += net
        eq += net
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
        if res == "OPEN":
            c["open"] += 1
            continue
        c[{"TARGET": "target", "STOP": "stop", "TIME": "time"}[res]] += 1
        c["wins"] += net > 0
        curve.append({"t": xi, "v": round(eq, 2)})
    closed = c["target"] + c["stop"] + c["time"]
    return {**c, "win_rate": c["wins"] / closed if closed else None, "max_drawdown": dd, "curve": curve, "picks": picks}


def audit():
    """Strategy audit: follow vs Plan A vs day-type switch on the same signals, day by day, with the reasons."""
    rows = [r for r in STATE["rows"] if r.get("fill_status") == "FILLED"]
    days = {}
    for r in rows:
        d = days.setdefault(r["captured_ist"][:10], {"date": r["captured_ist"][:10], "n": 0, "follow": 0.0, "plan_a": 0.0, "switch": 0.0, "switch_n": 0, "tags": {}})
        d["n"] += 1
        d["follow"] += r["follow_net"]
        d["plan_a"] += r["net"]
        pb = r.get("playbook")
        if pb in ("FOLLOW", "PLAN_A"):
            d["switch"] += r["follow_net"] if pb == "FOLLOW" else r["net"]
            d["switch_n"] += 1
        d["tags"][r.get("day_type") or "NA"] = d["tags"].get(r.get("day_type") or "NA", 0) + 1
    bars = daytype.STATE["bars"]
    dr = daytype.daily_ranges(bars) if bars else {}
    for d in days.values():
        t0 = int(datetime.fromisoformat(d["date"]).replace(tzinfo=IST).timestamp())
        seg = [b for b in bars if t0 <= b["t"] < t0 + 86400]
        d["eod"] = daytype.eod_label(seg, daytype.adr(dr, daytype.ist_day(t0))) if seg and seg[-1]["t"] >= t0 + 86400 - 600 else "TODAY" if seg else "NA"
        if seg:
            d["range_pct"] = (max(b["h"] for b in seg) - min(b["l"] for b in seg)) / seg[0]["o"] * 100
            d["change_pct"] = (seg[-1]["c"] / seg[0]["o"] - 1) * 100
    def bucket(lo, hi):
        g = [r for r in rows if r.get("mfe") is not None and lo <= r["mfe"] < hi]
        return {"n": len(g), "follow_wins": sum(r["follow_net"] > 0 for r in g), "plan_a_wins": sum(r["net"] > 0 for r in g)}
    fw = [r["follow_net"] for r in rows if r["follow_result"] == "TARGET"]
    fl = [-r["follow_net"] for r in rows if r["follow_result"] == "STOP"]
    pw = [r["net"] for r in rows if r["plan_result"] == "TARGET"]
    pl = [-r["net"] for r in rows if r["plan_result"] == "STOP"]
    be = lambda w, l: (sum(l) / len(l)) / (sum(l) / len(l) + sum(w) / len(w)) if w and l else None
    avg_fee = sum(r["fees"] for r in rows) / len(rows) if rows else None
    avg_risk = sum(abs(r["entry"] - r["model_stop"]) * r["qty"] for r in rows) / len(rows) if rows else None
    return {"computed_at": STATE["computed_at"], "books": {k: {m: {x: y for x, y in b.items() if x != "picks"} for m, b in v.items()} if isinstance(v, dict) else v for k, v in STATE["books"].items()},
            "days": sorted(days.values(), key=lambda d: d["date"]),
            "why": {"ran_2": bucket(2.0, 1e9), "ran_under_1": bucket(-1e9, 1.0), "between": bucket(1.0, 2.0),
                    "both_lost": sum(1 for r in rows if r["follow_net"] < 0 and r["net"] < 0 and r["follow_result"] != "OPEN" and r["plan_result"] != "OPEN"), "n": len(rows),
                    "avg_fee": avg_fee, "avg_risk": avg_risk, "breakeven_follow": be(fw, fl), "breakeven_plan_a": be(pw, pl),
                    "long": {"n": sum(r["model_dir"] == "LONG" for r in rows), "follow_wins": sum(r["model_dir"] == "LONG" and r["follow_net"] > 0 for r in rows)},
                    "short": {"n": sum(r["model_dir"] == "SHORT" for r in rows), "follow_wins": sum(r["model_dir"] == "SHORT" and r["follow_net"] > 0 for r in rows)}},
            "evidence": daytype.evidence(), "day_type_now": daytype.public()}


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
