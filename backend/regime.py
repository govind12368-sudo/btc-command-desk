"""Regime Strategy journal: PAPER only, it never sends an order to any exchange.

Rules (fixed, admin spec of 19 Nov 2026):
1. Market type at the signal candle's close
   Down trend = day type Trend down, or Unclear with the 4h trend DOWN.
   Range      = day type Range, or Unclear with the 4h trend MIXED.
   Up trend   = day type Trend up, or Unclear with the 4h trend UP.
2. First match wins
   Down:  Breakdown short  RSI15 < 35 and stop >= 0.6 x hourly ATR            -> SHORT
          Dip-bounce long  RSI15 >= 35, price below 4h ago, day type != Trend down -> LONG
   Range: Trend follow     stop >= 0.6 x ATR, volume >= 1.2 x avg, a 15m/1h/4h trend agrees with the signal -> signal side
          Trend fade       same stop/volume checks, a trend agrees with the opposite side -> opposite side
          Dip-bounce long  RSI15 >= 35 and price below 4h ago                    -> LONG
   Up:    Breakout long    RSI15 > 65 and stop >= 0.6 x ATR                     -> LONG
          Pullback long    RSI15 <= 50 and price below 4h ago                    -> LONG   (no shorts in an up trend)
3. Orders: entry at the signal entry (the site's own fill rule), SL 1R = signal stop distance, TP 1.5R, close after 6 h,
   $1,000 margin x 10x = $10,000 (size floor(10000/entry, 3 dp) BTC), max 2 trades per IST day.
Reverse book: every Regime trade taken the other way round (same entry, fill, 1R stop, 1.5R target, 6 h exit).
Measures use the site's own indicator code (indicators.py) on Delta BTCUSD 5-minute candles completed by the signal
candle's close; RSI 15m includes the signal candle's close (the 15-minute candle still forming at that moment).
"""
import asyncio
import bisect
import logging
import math
import os
from datetime import datetime, timezone

from core import db, now_utc, iso, IST
import daytype
import indicators as I
import plan_a

log = logging.getLogger("regime")
NAME = "Regime Strategy"
LIVE_IST = os.environ.get("REGIME_LIVE_START", "2026-11-19 17:30")
RR, HOLD, MAX_PER_DAY, ATR_MIN, VOL_MIN = 1.5, 6 * 3600, 2, 0.6, 1.2
NOTIONAL, MARGIN, LEVERAGE = 10000.0, 1000.0, 10
FEES = {"taker": 0.0005, "maker": 0.0002, "gst": 0.18}
DAY = {"RANGE": "Range", "TREND_UP": "Trend up", "TREND_DOWN": "Trend down", "MIXED": "Unclear", "NA": "Unclear"}
FEAT_V = "regime-f2"
STATE = {"computed_at": None, "error": None, "data": None}


def _ts(ist):
    return int(datetime.strptime(ist, "%Y-%m-%d %H:%M").replace(tzinfo=IST).timestamp())


def _ist(ts, fmt="%Y-%m-%d %H:%M"):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(IST).strftime(fmt) if ts else ""


def signal_candle(row):
    """Open time of the 5-minute signal candle (the signal ID carries it; else the candle before capture)."""
    try:
        t = int(str(row["signal_id"]).split("-")[-1])
        if t % 300 == 0 and abs(t - row["cap_t"]) < 1800:
            return t
    except Exception:
        pass
    c = row["cap_t"] - row["cap_t"] % 300
    return c - 300


def _trend(bars, res):
    m = I.frame_metrics(bars, res)
    t = I.trend_dir(m)
    return {"UP": "Up", "DOWN": "Down", "MIXED": "Mixed"}.get(t)


def features(t0, d, bars):
    """bars: 5m candles (t,o,h,l,c,v) ascending. Only candles up to the signal candle (open t0) are used."""
    T = [b["t"] for b in bars]
    j = bisect.bisect_left(T, t0)
    if j >= len(bars) or T[j] != t0 or j < 300:
        return None
    upto = bars[max(0, j - 7000):j + 1]                       # up to ~24 days: enough for 4h EMA50
    close_t = t0 + 300
    f15 = [b for b in I.aggregate(upto, 300, 900) if b["t"] + 900 <= close_t]
    f1h = [b for b in I.aggregate(upto, 300, 3600) if b["t"] + 3600 <= close_t]
    f4h = [b for b in I.aggregate(upto, 300, 14400) if b["t"] + 14400 <= close_t]
    k15 = t0 - t0 % 900
    rsi_closes = [b["c"] for b in f15 if b["t"] < k15][-150:] + [bars[j]["c"]]
    rsi = I.rsi_wilder(rsi_closes)
    atr = I.atr_series(f1h[-60:], 14) if len(f1h) >= 20 else None
    m5 = I.frame_metrics(bars[max(0, j - 80):j + 1], 300)
    j4 = bisect.bisect_left(T, t0 - 4 * 3600)
    c4 = bars[j4]["c"] if j4 < len(bars) and T[j4] == t0 - 4 * 3600 else None
    c = bars[j]["c"]
    lo_i = bisect.bisect_left(T, t0 - 86400)
    hi24, lo24 = max(b["h"] for b in bars[lo_i:j]), min(b["l"] for b in bars[lo_i:j])
    return {"hi24": hi24, "lo24": lo24, "pos24": round((c - lo24) / (hi24 - lo24), 4) if hi24 > lo24 else None,
            "hour_ist": int(((t0 + 300 + 19800) % 86400) // 3600),"rsi": round(rsi, 2) if rsi is not None else None, "atr1h": round(atr, 2) if atr else None,
            "stop_atr": round(d / atr, 3) if atr else None, "vr": round(m5.get("volume_ratio"), 3) if m5.get("volume_ratio") is not None else None,
            "t15": _trend(f15, 900), "t1h": _trend(f1h, 3600), "t4h": _trend(f4h, 14400),
            "close": c, "close_4h_ago": c4, "chg4h": round(c / c4 - 1, 5) if c4 else None, "candle_t": t0, "v": FEAT_V}


def market_type(day, t4h):
    if day == "Trend down" or (day == "Unclear" and t4h == "Down"):
        return "Down trend"
    if day == "Range" or (day == "Unclear" and t4h == "Mixed"):
        return "Range"
    if day == "Trend up" or (day == "Unclear" and t4h == "Up"):
        return "Up trend"
    return "Unknown"


def decide(side, day, f):
    """(market, rule, trade side or None, reason). First match wins."""
    if not f or f.get("rsi") is None or f.get("t4h") is None:
        return "Unknown", "No trade", None, "Market data (RSI / 4h trend) not available for this signal"
    mk = market_type(day, f["t4h"])
    rsi, sa, vr = f["rsi"], f.get("stop_atr"), f.get("vr")
    lower = f.get("chg4h") is not None and f["chg4h"] < 0
    stop_ok = sa is not None and sa >= ATR_MIN
    vol_ok = vr is not None and vr >= VOL_MIN
    opp = "SHORT" if side == "LONG" else "LONG"
    want = {"LONG": "Up", "SHORT": "Down"}
    trends = [f.get("t15"), f.get("t1h"), f.get("t4h")]
    if mk == "Down trend":
        if rsi < 35 and stop_ok:
            return mk, "Breakdown short", "SHORT", f"Down trend; RSI {rsi:.1f} < 35 and stop {sa:.2f}x hourly ATR >= 0.6"
        if rsi >= 35 and lower and day != "Trend down":
            return mk, "Dip-bounce long", "LONG", f"Down trend (Unclear day, 4h down); RSI {rsi:.1f} >= 35 and price {f['chg4h']:+.2%} vs 4h ago"
        return mk, "No trade", None, f"Down trend but no setup: RSI {rsi:.1f}, stop {sa if sa is not None else 0:.2f}x ATR, 4h change {(f.get('chg4h') or 0):+.2%}, day {day}"
    if mk == "Range":
        if stop_ok and vol_ok and want[side] in trends:
            return mk, "Trend follow", side, f"Range; stop {sa:.2f}x ATR, volume {vr:.2f}x, a trend agrees with the {side} signal"
        if stop_ok and vol_ok and want[opp] in trends:
            return mk, "Trend fade", opp, f"Range; stop {sa:.2f}x ATR, volume {vr:.2f}x, trends agree with {opp}: fade the signal"
        if rsi >= 35 and lower:
            return mk, "Dip-bounce long", "LONG", f"Range; RSI {rsi:.1f} >= 35 and price {f['chg4h']:+.2%} vs 4h ago"
        return mk, "No trade", None, (f"Range but no setup: stop {sa if sa is not None else 0:.2f}x ATR, volume {vr if vr is not None else 0:.2f}x, "
                                      f"RSI {rsi:.1f}, 4h change {(f.get('chg4h') or 0):+.2%}")
    if mk == "Up trend":
        if rsi > 65 and stop_ok:
            return mk, "Breakout long", "LONG", f"Up trend; RSI {rsi:.1f} > 65 and stop {sa:.2f}x ATR >= 0.6"
        if rsi <= 50 and lower:
            return mk, "Pullback long", "LONG", f"Up trend; RSI {rsi:.1f} <= 50 and price {f['chg4h']:+.2%} vs 4h ago"
        return mk, "No trade", None, f"Up trend but no setup (no shorts in an up trend): RSI {rsi:.1f}, 4h change {(f.get('chg4h') or 0):+.2%}"
    return mk, "No trade", None, "Market type not clear"


def fee(price, q, kind):
    return price * q * FEES[kind] * (1 + FEES["gst"])


def simulate(side, e, d, fill_t, bars, live):
    """Site touch rules (plan_a._first_exit): stop wins a same-minute tie; in the fill minute only the stop counts."""
    stop = e + d if side == "SHORT" else e - d
    tgt = e - RR * d if side == "SHORT" else e + RR * d
    q = math.floor(NOTIONAL / e * 1000) / 1000
    i0 = plan_a._bar_index(bars, fill_t)
    res, bar = plan_a._first_exit(side, stop, tgt, bars, fill_t, HOLD, i0)
    sg = 1 if side == "LONG" else -1
    out = {"side": side, "sl": stop, "tp": tgt, "size": q, "fill_t": fill_t, "fill_ist": _ist(fill_t)}
    end = bar["t"] if bar else (bars[-1]["t"] if bars else fill_t)
    best = worst = 0.0
    for c in bars[i0:]:
        if c["t"] > end:
            break
        worst = max(worst, ((e - c["l"]) if side == "LONG" else (c["h"] - e)) / d)
        if c["t"] != fill_t:
            best = max(best, ((c["h"] - e) if side == "LONG" else (e - c["l"])) / d)
    out.update(mfe=round(best, 2), mae=round(worst, 2))
    if res is None:
        px = live or e
        gross = sg * (px - e) * q
        fees = fee(e, q, "taker") + fee(px, q, "taker")
        out.update(status="OPEN", result="Open", mark=px, gross=gross, fees=fees, net=gross - fees, r=(gross - fees) / (d * q))
        return out
    xp = tgt if res == "TARGET" else stop if res == "STOP" else bar["c"]
    exit_t = bar["t"] + 60 if res == "TIME" else bar["t"]
    gross = sg * (xp - e) * q
    fees = fee(e, q, "taker") + fee(xp, q, "maker" if res == "TARGET" else "taker")
    out.update(status="CLOSED", result={"TARGET": "Target", "STOP": "Stop", "TIME": "Time exit"}[res], exit_t=exit_t, exit_ist=_ist(exit_t),
               exit_price=xp, gross=gross, fees=fees, net=gross - fees, r=(gross - fees) / (d * q))
    return out


async def bars_5m(start, end):
    """5m candles with volume: daytype keeps the last 26 days; anything older is fetched from Delta."""
    have = {b["t"]: b for b in daytype.STATE.get("bars") or []}
    if not have or min(have) > start:
        try:
            for b in await daytype._fetch(start, (min(have) if have else end) + 300):
                have.setdefault(b["t"], b)
        except Exception as e:
            log.warning(f"5m fetch failed: {e}")
    return [have[t] for t in sorted(have) if start <= t <= end]


def summary(trades):
    closed = [t for t in trades if t["status"] == "CLOSED"]
    wins = [t for t in closed if t["net"] > 0]
    gw = sum(t["net"] for t in wins); gl = -sum(t["net"] for t in closed if t["net"] < 0)
    eq = peak = dd = 0.0
    for t in sorted(closed, key=lambda x: x["exit_t"]):
        eq += t["net"]; peak = max(peak, eq); dd = min(dd, eq - peak)
    return {"trades": len(trades), "closed": len(closed), "open": len(trades) - len(closed),
            "targets": sum(t["result"] == "Target" for t in closed), "stops": sum(t["result"] == "Stop" for t in closed),
            "time_exits": sum(t["result"] == "Time exit" for t in closed), "wins": len(wins),
            "win_rate": len(wins) / len(closed) if closed else None, "net": round(sum(t["net"] for t in closed), 2),
            "open_pnl": round(sum(t["net"] for t in trades if t["status"] == "OPEN"), 2),
            "cumulative": round(sum(t["net"] for t in trades), 2),
            "avg_r": round(sum(t["r"] for t in closed) / len(closed), 3) if closed else None,
            "profit_factor": round(gw / gl, 2) if gl > 0 else None, "max_drawdown": round(dd, 2)}


def by_rule(trades):
    out = {}
    for t in trades:
        out.setdefault(t["rule"], []).append(t)
    return [{"rule": k, **summary(v)} for k, v in sorted(out.items())]


def running(rows):
    """Running total of closed trades, oldest first (rows are oldest first)."""
    tot = pk = 0.0
    for x in rows:
        if x.get("status") == "CLOSED":
            tot += x["net"]; pk = max(pk, tot)
            x["running_total"] = round(tot, 2); x["running_dd"] = round(tot - pk, 2)
    return rows


async def compute():
    rows = sorted([r for r in (plan_a.STATE.get("rows") or []) if r.get("cap_t")], key=lambda x: x["cap_t"])
    if not rows:
        return STATE
    now = int(now_utc().timestamp())
    feats = {d["_id"]: d async for d in db.regime_feat.find({"v": FEAT_V})}
    need = [r for r in rows if r["signal_id"] not in feats]
    if need:
        b5 = await bars_5m(min(signal_candle(r) for r in need) - 25 * 86400, now)
        for r in need:
            t0 = signal_candle(r)
            if not b5 or b5[-1]["t"] < t0:
                continue                                  # candle not in yet: try again next round
            f = features(t0, abs(float(r["entry"]) - float(r["model_stop"])), b5)
            if f is None and now - t0 < 3 * 3600:
                continue
            f = f or {"v": FEAT_V, "missing": True}
            feats[r["signal_id"]] = f
            await db.regime_feat.update_one({"_id": r["signal_id"]}, {"$set": f}, upsert=True)
            await asyncio.sleep(0)
    fills = [r.get("fill_t") for r in rows if r.get("fill_t")]
    b1 = await plan_a.candles(min(fills) - 60, now) if fills else []
    live = plan_a.STATE.get("live_price") or (b1[-1]["c"] if b1 else None)
    live_ts = _ts(LIVE_IST)
    per_day, out, rev = {}, [], []
    for r in rows:
        f = feats.get(r["signal_id"])
        if f and f.get("missing"):
            f = None
        side = r["model_dir"]; e = float(r["entry"]); d = abs(e - float(r["model_stop"]))
        day = DAY.get(r.get("day_type") or "NA", "Unclear")
        mk, rule, tside, why = decide(side, day, f)
        x = {"signal_id": r["signal_id"], "time_ist": r["captured_ist"], "cap_t": r["cap_t"], "period": "Live" if r["cap_t"] >= live_ts else "Before live",
             "signal_side": side, "day_type": day, "market": mk, "entry": e, "signal_stop": float(r["model_stop"]), "d": d,
             **{k: (f or {}).get(k) for k in ("rsi", "atr1h", "stop_atr", "vr", "t15", "t1h", "t4h", "chg4h")}, "rule": rule, "reason": why}
        fs = r.get("fill_status")
        if tside:
            dk = r["captured_ist"][:10]
            if fs == "NOT FILLED":
                x.update(rule_note=rule, rule="Not filled", reason=f"{rule} ({tside}) but the entry was never filled")
            elif per_day.get(dk, 0) >= MAX_PER_DAY:
                x.update(rule_note=rule, rule="No trade", reason=f"{rule} ({tside}) but 2 trades already taken on {dk} (max 2 a day)")
            else:
                per_day[dk] = per_day.get(dk, 0) + 1
                x["trade_side"] = tside
                if fs == "FILLED":
                    sim = simulate(tside, e, d, r["fill_t"], b1, live)
                    x.update(sim)
                    rx = {**{k: x[k] for k in ("signal_id", "time_ist", "cap_t", "period", "signal_side", "day_type", "market", "entry", "d", "rule")},
                          **simulate("LONG" if tside == "SHORT" else "SHORT", e, d, r["fill_t"], b1, live)}
                    rx["rule"] = f"Reverse of {rule}"; rx["base_rule"] = rule
                    rev.append(rx)
                else:
                    x.update(status="WAITING", result="Waiting for fill")
        out.append(x)
    trades = [x for x in out if x.get("status") in ("OPEN", "CLOSED")]
    running(out); running(rev)

    def book(tr, rows_):
        lv = [t for t in tr if t["period"] == "Live"]
        return {"all": summary(tr), "live": summary(lv), "by_rule": by_rule(tr), "by_rule_live": by_rule(lv),
                "by_market": [{"market": m, **summary([t for t in tr if t["market"] == m])} for m in ("Down trend", "Range", "Up trend")]}
    daily = {}
    for x in out:
        v = daily.setdefault(x["time_ist"][:10], {"date": x["time_ist"][:10], "signals": 0, "trades": 0, "targets": 0, "stops": 0, "time_exits": 0, "open": 0,
                                                    "net": 0.0, "rev_net": 0.0})
        v["signals"] += 1
        if x.get("status") in ("OPEN", "CLOSED"):
            v["trades"] += 1
            if x["status"] == "OPEN":
                v["open"] += 1
            else:
                v[{"Target": "targets", "Stop": "stops", "Time exit": "time_exits"}[x["result"]]] += 1; v["net"] += x["net"]
    for t in rev:
        if t["status"] == "CLOSED":
            daily[t["time_ist"][:10]]["rev_net"] += t["net"]
    cum = rcum = 0.0
    days = sorted(daily.values(), key=lambda v: v["date"])
    for v in days:
        c = v["targets"] + v["stops"] + v["time_exits"]
        v["win_rate"] = v["targets"] / c if c else None
        cum += v["net"]; rcum += v["rev_net"]
        v["net"], v["rev_net"], v["cumulative"], v["rev_cumulative"] = round(v["net"], 2), round(v["rev_net"], 2), round(cum, 2), round(rcum, 2)
    data = {"name": NAME, "paper_only": True, "live_start_ist": LIVE_IST, "computed_at": iso(now_utc()), "live_price": live,
            "regime": {"rows": out[::-1], **book(trades, out)}, "reverse": {"rows": rev[::-1], **book(rev, rev)}, "daily": days[::-1],
            "settings": {"target_r": RR, "stop_r": 1, "time_exit_h": 6, "max_per_day": MAX_PER_DAY, "atr_min": ATR_MIN, "vol_min": VOL_MIN,
                         "margin": MARGIN, "leverage": LEVERAGE, "notional": NOTIONAL, "fees": FEES}}
    STATE.update(computed_at=data["computed_at"], error=None, data=data)
    try:
        import alert_mode
        if alert_mode.source_on("regime"):            # off since 21 Nov 2026: only Flush Short and Candle Runs alert
            await notify(out, now)
    except Exception as e:
        log.warning(f"regime alerts failed: {e}")
    return STATE


# ---------------------------------------------------------------- email alerts (admin request, 20 Nov 2026)
ENTRY_WINDOW, EXIT_WINDOW = 1800, 3 * 3600        # only fresh events: no flood of old trades after a restart


def _email(kind, x):
    """(subject, html) for a Regime trade: kind = entry / exit / not_filled. Paper journal: no order is placed."""
    from html import escape
    from channel_config import CFG
    from core import APP_NAME
    name = CFG.get("EMAIL_FROM_NAME") or APP_NAME
    app = CFG["FRONTEND_URL"].split(",")[0].rstrip("/")
    f1 = lambda v: f"{v:,.1f}" if isinstance(v, (int, float)) else str(v)
    side = x.get("trade_side") or x.get("side") or ""
    if kind == "entry":
        subject = f"{name}: Regime {side} — {x['rule']} ({x['market']})"
        rows = [("Trade", f"{side} BTCUSD"), ("Setup", x["rule"]), ("Market type", f"{x['market']} (day type {x['day_type']})"),
                ("Entry (USD)", f1(x["entry"])), ("Stop loss (USD)", f1(x.get("sl") or (x["entry"] + x["d"] if side == "SHORT" else x["entry"] - x["d"]))),
                ("Take profit 1.5R (USD)", f1(x.get("tp") or (x["entry"] - RR * x["d"] if side == "SHORT" else x["entry"] + RR * x["d"]))),
                ("Time exit", "6 hours after the fill"), ("Size", "$1,000 margin x 10x = $10,000"),
                ("Status", "Filled at entry" if x.get("status") == "OPEN" else "Waiting for the entry price to fill"),
                ("Why", x["reason"]), ("Scalp signal it came from", f"{x['signal_side']} · {x['signal_id']}"), ("Signal time (IST)", x["time_ist"])]
    elif kind == "exit":
        subject = f"{name}: Regime trade closed — {x['result']} {('+' if x['net'] >= 0 else '−')}${abs(x['net']):,.2f}"
        rows = [("Trade", f"{side} BTCUSD · {x['rule']}"), ("Result", x["result"]), ("Entry (USD)", f1(x["entry"])), ("Exit (USD)", f1(x["exit_price"])),
                ("Net P&L after fees", f"{('+' if x['net'] >= 0 else '−')}${abs(x['net']):,.2f} ({x['r']:+.2f}R)"), ("Filled (IST)", x.get("fill_ist", "")),
                ("Closed (IST)", x.get("exit_ist", "")), ("Running total (Regime journal)", f"${x.get('running_total', 0):+,.2f}")]
    else:
        subject = f"{name}: Regime {x.get('rule_note', '')} not filled — no trade"
        rows = [("Setup", x.get("rule_note", "")), ("Entry (USD)", f1(x["entry"])), ("Result", "The entry price was never reached, so no trade"), ("Signal time (IST)", x["time_ist"])]
    tr = "".join(f"<tr><td style='padding:4px 12px 4px 0;color:#555'>{escape(k)}</td><td style='padding:4px 0'><b>{escape(str(v))}</b></td></tr>" for k, v in rows)
    link = f"{app}/regime"
    html = (f"<table role='presentation' width='100%'><tr><td style='padding:24px;font-family:Arial,sans-serif;color:#111'>"
            f"<h2 style='margin:0 0 12px'>{escape(name)} · Regime Journal</h2><table role='presentation'>{tr}</table>"
            f"<p>Open the journal: <a href='{escape(link)}'>{escape(link)}</a></p>"
            f"<p style='font-size:12px;color:#888'>Paper journal alert: no order was placed. Verify on the exchange before trading.</p></td></tr></table>")
    return subject, html


async def notify(rows, now):
    """Email each fresh Regime event once: a new trade, its close, or a trade that never filled. Follows the site's alert switches."""
    import alert_mode
    import alerts
    from channel_config import CFG
    live_ts = _ts(LIVE_IST)
    todo = []
    for x in rows:
        if x["cap_t"] < live_ts:
            continue
        sid = x["signal_id"]
        if x.get("trade_side") and now - x["cap_t"] < ENTRY_WINDOW:
            todo.append(("entry", f"regime-entry-{sid}", x))
        if x.get("status") == "CLOSED" and now - x.get("exit_t", 0) < EXIT_WINDOW:
            todo.append(("exit", f"regime-exit-{sid}", x))
        if x.get("rule") == "Not filled" and x.get("rule_note") and now - x["cap_t"] < EXIT_WINDOW:
            todo.append(("not_filled", f"regime-nofill-{sid}", x))
    if not todo:
        return []
    mode = await alert_mode.get_mode()
    sent = []
    for kind, key, x in todo:
        if await db.alerts_log.find_one({"_id": key}, {"_id": 1}):
            continue
        entry = {"_id": key, "at": iso(now_utc()), "signal_id": x["signal_id"], "channel": "email", "kind": f"regime-{kind}", "test": False}
        to = CFG.get("ALERT_EMAIL")
        if not mode["effective_armed"]:
            entry["result"] = "not sent (" + ("kill switch on" if mode["kill_switch"] else "alert mode is OFF") + ")"
        elif not mode["channels"].get("email"):
            entry["result"] = "not sent (email channel switched off)"
        elif not to:
            entry["result"] = "not sent (ALERT_EMAIL not configured)"
        else:
            try:
                subject, html = _email(kind, x)
                entry["provider_id"] = await alerts.send_email(to=to, subject=subject, html=html)
                entry["result"] = "sent"; entry["to"] = to
            except Exception as e:
                log.warning(f"regime email failed: {e}")
                entry["result"] = f"failed: {str(e)[:160]}"
        try:
            await db.alerts_log.insert_one(entry)
        except Exception:
            continue                                       # another worker logged it first
        sent.append(entry)
    return sent


async def loop():
    await asyncio.sleep(45)
    while True:
        try:
            await compute()
        except Exception as e:
            STATE["error"] = str(e)[:200]
            log.exception("regime compute failed")
        await asyncio.sleep(120)
