"""Flush Short v3: PAPER journal only. It never sends an order to any exchange.

Rules (fixed, admin request 21 Nov 2026; chosen from the 26-month replay, see FLUSH_EVIDENCE):
  take a scalp signal only when ALL hold, measured at the signal candle's close:
    1. the signal is SHORT
    2. RSI 15m < 30                              (price is breaking down)
    3. not between 00:00 and 06:00 IST            (night follows lost the most)
    4. stop >= 0.6 x hourly ATR                   (stop outside normal noise)
    5. 15m, 1h and 4h trends all Down             (the breakdown goes with every timeframe)
    6. BTC is in a DOWN market: down more than 5% over the last 30 days (v3, 21 Nov 2026: in range markets the same
       rules lost; in down markets they passed a luck test on unseen history)
  trade: SHORT at the signal entry with the site's own fill rule (post-only limit, maker fee),
         stop 1R = the signal's stop distance, target 1.25R (maker), close after 6 hours (taker);
         a minute touching both stop and target counts as the stop; $10,000 position (floor(10000/entry, 3) BTC);
         Delta fees + 18% GST. Signals are independent (overlapping trades allowed).
Measures (RSI, ATR, trends) are the Regime journal's frozen features: the site's own indicator code at the signal close.
"""
import asyncio
import logging
import math
import os

from core import db, now_utc, iso
import plan_a
import regime

log = logging.getLogger("flush")
NAME = "Flush Short v3"
LIVE_IST = os.environ.get("FLUSH_LIVE_START", "2026-11-21 17:30")
RSI_MAX, ATR_MIN, RR, HOLD, NOTIONAL = 30.0, 0.6, 1.25, 6 * 3600, 10_000.0
DOWN_MARKET = -0.05                              # BTC 30-day change must be below this
FEAT_V = "flush-f1"
FEES = {"taker": 0.0005, "maker": 0.0002, "gst": 0.18}
STATE = {"computed_at": None, "error": None, "data": None}
FLUSH_EVIDENCE = {
    "period": "21 Oct 2024 – 13 Nov 2026 (26-month replay of the Vantage scalp signals, Delta BTCUSD 5m candles)",
    "all": {"n": 45, "win": 0.71, "usd": 24.14}, "first_half": {"n": 16, "win": 0.56, "usd": 6.96}, "second_half": {"n": 29, "win": 0.79, "usd": 33.63},
    "by_market": {"Down": {"n": 45, "win": 0.71, "usd": 24.14}, "Range": {"n": 18, "win": 0.39, "usd": -12.26}, "Up": {"n": 4, "win": 0.50, "usd": -0.24}},
    "note": ("Down markets only. Luck test: random down-market SHORT trades win 49% and lose $2.15 a trade; the chance of luck matching the "
             "second half (23 of 29) is about 0.035% (about 1.4% after allowing for the 40 variants tried). About 1-2 trades a month, "
             "only while BTC is falling. Not proven until it holds on fresh paper trades."),
}


def market_type(chg30):
    if chg30 is None:
        return "Unknown"
    return "Down" if chg30 < DOWN_MARKET else "Up" if chg30 > -DOWN_MARKET else "Range"


def decide(side, cap_t, f, chg30=None):
    """(take?, reason). All six rules must hold."""
    if not f or f.get("rsi") is None or f.get("stop_atr") is None or f.get("t4h") is None:
        return False, "Market data for this signal is not available"
    if chg30 is None:
        return False, "BTC 30-day change not available for this signal"
    hour = int(regime._ist(cap_t, "%H"))
    why = []
    if side != "SHORT":
        why.append("LONG signal (Flush Short takes SHORT signals only)")
    if f["rsi"] >= RSI_MAX:
        why.append(f"RSI 15m {f['rsi']:.1f} is not below 30")
    if hour < 6:
        why.append(f"{hour:02d}:xx IST is in the 00:00-06:00 night window")
    if f["stop_atr"] < ATR_MIN:
        why.append(f"stop is only {f['stop_atr']:.2f}x the hourly ATR (needs 0.6x)")
    tr = (f.get("t15"), f.get("t1h"), f.get("t4h"))
    if tr != ("Down", "Down", "Down"):
        why.append(f"trends 15m/1h/4h are {'/'.join(x or '-' for x in tr)}, not all Down")
    mt = market_type(chg30)
    if mt != "Down":
        why.append(f"{mt} market (BTC {chg30:+.1%} over 30 days; Flush Short trades only when it is below -5%)")
    if why:
        return False, "; ".join(why)
    return True, (f"SHORT signal, RSI {f['rsi']:.1f} < 30, {hour:02d}:xx IST, stop {f['stop_atr']:.2f}x hourly ATR, 15m/1h/4h all Down, "
                  f"Down market (BTC {chg30:+.1%} over 30 days): breakdown")


def fee(price, q, kind):
    return price * q * FEES[kind] * (1 + FEES["gst"])


def simulate(e, d, fill_t, bars, live):
    """SHORT trade on the site's touch rules (plan_a._first_exit): stop wins a same-minute tie; fill minute: stop only."""
    stop, tgt = e + d, e - RR * d
    q = math.floor(NOTIONAL / e * 1000) / 1000
    i0 = plan_a._bar_index(bars, fill_t)
    res, bar = plan_a._first_exit("SHORT", stop, tgt, bars, fill_t, HOLD, i0)
    out = {"side": "SHORT", "sl": stop, "tp": tgt, "size": q, "fill_t": fill_t, "fill_ist": regime._ist(fill_t)}
    end = bar["t"] if bar else (bars[-1]["t"] if bars else fill_t)
    best = worst = 0.0
    for c in bars[i0:]:
        if c["t"] > end:
            break
        worst = max(worst, (c["h"] - e) / d)
        if c["t"] != fill_t:
            best = max(best, (e - c["l"]) / d)
    out.update(mfe=round(best, 2), mae=round(worst, 2))
    if res is None:
        px = live or e
        gross = (e - px) * q
        fees = fee(e, q, "maker") + fee(px, q, "taker")
        out.update(status="OPEN", result="Open", mark=px, gross=gross, fees=fees, net=gross - fees, r=(gross - fees) / (d * q))
        return out
    xp = tgt if res == "TARGET" else stop if res == "STOP" else bar["c"]
    exit_t = bar["t"] + 60 if res == "TIME" else bar["t"]
    gross = (e - xp) * q
    fees = fee(e, q, "maker") + fee(xp, q, "maker" if res == "TARGET" else "taker")
    out.update(status="CLOSED", result={"TARGET": "Target", "STOP": "Stop", "TIME": "Time exit"}[res], exit_t=exit_t, exit_ist=regime._ist(exit_t),
               exit_price=xp, gross=gross, fees=fees, net=gross - fees, r=(gross - fees) / (d * q))
    return out


async def _close_at(t):
    """Close of the 5m candle that opens at t (or the nearest one just before), from Delta."""
    import daytype
    have = [b for b in (daytype.STATE.get("bars") or []) if t - 900 <= b["t"] <= t]
    if not have:
        try:
            have = [b for b in await daytype._fetch(t - 1800, t + 300) if b["t"] <= t]
        except Exception as e:
            log.warning(f"30-day price fetch failed: {e}")
            return None
    return have[-1]["c"] if have else None


async def chg30_for(sid, t0, close):
    """BTC change over the 30 days before the signal candle; frozen once known."""
    doc = await db.flush_feat.find_one({"_id": sid})
    if doc and doc.get("v") == FEAT_V:
        return doc.get("chg30")
    if not close:
        return None
    c30 = await _close_at(t0 - 30 * 86400)
    if not c30:
        return None
    chg = round(close / c30 - 1, 5)
    await db.flush_feat.update_one({"_id": sid}, {"$set": {"chg30": chg, "close30": c30, "v": FEAT_V}}, upsert=True)
    return chg


async def market_now(live):
    now = int(now_utc().timestamp()); now -= now % 300
    c30 = await _close_at(now - 30 * 86400)
    if not live or not c30:
        return None
    chg = live / c30 - 1
    return {"chg30": round(chg, 5), "type": market_type(chg), "active": market_type(chg) == "Down"}


async def compute():
    rows = sorted([r for r in (plan_a.STATE.get("rows") or []) if r.get("cap_t")], key=lambda x: x["cap_t"])
    if not rows:
        return STATE
    now = int(now_utc().timestamp())
    feats = {d["_id"]: d async for d in db.regime_feat.find({"v": regime.FEAT_V})}
    fills = [r.get("fill_t") for r in rows if r.get("fill_t")]
    b1 = await plan_a.candles(min(fills) - 60, now) if fills else []
    live = plan_a.STATE.get("live_price") or (b1[-1]["c"] if b1 else None)
    live_ts = regime._ts(LIVE_IST)
    out = []
    for r in rows:
        f = feats.get(r["signal_id"])
        if f and f.get("missing"):
            f = None
        e = float(r["entry"]); d = abs(e - float(r["model_stop"]))
        chg30 = await chg30_for(r["signal_id"], regime.signal_candle(r), (f or {}).get("close")) if f else None
        take, why = decide(r["model_dir"], r["cap_t"], f, chg30)
        x = {"signal_id": r["signal_id"], "time_ist": r["captured_ist"], "cap_t": r["cap_t"], "period": "Live" if r["cap_t"] >= live_ts else "Before live",
             "signal_side": r["model_dir"], "day_type": regime.DAY.get(r.get("day_type") or "NA", "Unclear"), "entry": e, "d": d,
             "chg30": chg30, "market": market_type(chg30),
             **{k: (f or {}).get(k) for k in ("rsi", "stop_atr", "t15", "t1h", "t4h", "vr", "chg4h")}, "reason": why,
             "rule": "Flush short" if take else "No trade"}
        if take:
            fs = r.get("fill_status")
            if fs == "NOT FILLED":
                x.update(rule="Not filled", rule_note="Flush short", reason=why + "; but the entry was never filled")
            else:
                x["trade_side"] = "SHORT"
                if fs == "FILLED":
                    x.update(simulate(e, d, r["fill_t"], b1, live))
                else:
                    x.update(status="WAITING", result="Waiting for fill", sl=e + d, tp=e - RR * d)
        out.append(x)
    regime.running(out)
    trades = [x for x in out if x.get("status") in ("OPEN", "CLOSED")]
    daily = {}
    for x in out:
        v = daily.setdefault(x["time_ist"][:10], {"date": x["time_ist"][:10], "signals": 0, "trades": 0, "targets": 0, "stops": 0, "time_exits": 0, "open": 0, "net": 0.0})
        v["signals"] += 1
        if x.get("status") in ("OPEN", "CLOSED"):
            v["trades"] += 1
            if x["status"] == "OPEN":
                v["open"] += 1
            else:
                v[{"Target": "targets", "Stop": "stops", "Time exit": "time_exits"}[x["result"]]] += 1; v["net"] += x["net"]
    cum = 0.0
    days = sorted(daily.values(), key=lambda v: v["date"])
    for v in days:
        c = v["targets"] + v["stops"] + v["time_exits"]
        v["win_rate"] = v["targets"] / c if c else None
        v["net"] = round(v["net"], 2); cum += v["net"]; v["cumulative"] = round(cum, 2)
    lv = [t for t in trades if t["period"] == "Live"]
    try:
        mnow = await market_now(live)
    except Exception:
        mnow = None
    data = {"name": NAME, "paper_only": True, "live_start_ist": LIVE_IST, "computed_at": iso(now_utc()), "live_price": live, "market_now": mnow,
            "rows": out[::-1], "all": regime.summary(trades), "live": regime.summary(lv), "daily": [d for d in days if d["trades"]][::-1],
            "signals_checked": len(out), "evidence": FLUSH_EVIDENCE,
            "settings": {"rsi_max": RSI_MAX, "atr_min": ATR_MIN, "target_r": RR, "stop_r": 1, "time_exit_h": 6, "night_skip": "00:00-06:00 IST",
                         "trends": "15m, 1h, 4h all Down", "market": "BTC down more than 5% over 30 days", "notional": NOTIONAL, "fees": FEES, "entry": "post-only limit at the signal entry (maker)"}}
    STATE.update(computed_at=data["computed_at"], error=None, data=data)
    try:
        await notify(out, now)
    except Exception as e:
        log.warning(f"flush alerts failed: {e}")
    return STATE


# ---------------------------------------------------------------- email alerts
ENTRY_WINDOW, EXIT_WINDOW = 1800, 3 * 3600


def _email(kind, x):
    from html import escape
    from channel_config import CFG
    from core import APP_NAME
    name = CFG.get("EMAIL_FROM_NAME") or APP_NAME
    app = CFG["FRONTEND_URL"].split(",")[0].rstrip("/")
    f1 = lambda v: f"{v:,.1f}" if isinstance(v, (int, float)) else str(v)
    if kind == "entry":
        subject = f"{name}: Flush Short — SHORT BTCUSD at {f1(x['entry'])} ({x.get('market', '?')} market)"
        rows = [("Trade", "SHORT BTCUSD (Flush Short v3)"), ("Market type", f"{x.get('market', '?')} (BTC {x['chg30']:+.1%} over 30 days)" if x.get("chg30") is not None else "?"), ("Entry (USD)", f1(x["entry"])), ("Stop loss (USD)", f1(x["entry"] + x["d"])),
                ("Take profit 1.25R (USD)", f1(x["entry"] - RR * x["d"])), ("Time exit", "6 hours after the fill"), ("Size", "$10,000 position"),
                ("Status", "Filled at entry" if x.get("status") == "OPEN" else "Waiting for the entry price to fill"),
                ("Why", x["reason"]), ("Scalp signal", x["signal_id"]), ("Signal time (IST)", x["time_ist"])]
    elif kind == "exit":
        subject = f"{name}: Flush Short closed — {x['result']} {('+' if x['net'] >= 0 else '−')}${abs(x['net']):,.2f}"
        rows = [("Trade", "SHORT BTCUSD (Flush Short v3)"), ("Market type at entry", x.get("market", "?")), ("Result", x["result"]), ("Entry (USD)", f1(x["entry"])), ("Exit (USD)", f1(x["exit_price"])),
                ("Net P&L after fees", f"{('+' if x['net'] >= 0 else '−')}${abs(x['net']):,.2f} ({x['r']:+.2f}R)"), ("Filled (IST)", x.get("fill_ist", "")),
                ("Closed (IST)", x.get("exit_ist", "")), ("Running total (Flush Short journal)", f"${x.get('running_total', 0):+,.2f}")]
    else:
        subject = f"{name}: Flush Short not filled — no trade"
        rows = [("Entry (USD)", f1(x["entry"])), ("Result", "The entry price was never reached, so no trade"), ("Signal time (IST)", x["time_ist"])]
    tr = "".join(f"<tr><td style='padding:4px 12px 4px 0;color:#555'>{escape(k)}</td><td style='padding:4px 0'><b>{escape(str(v))}</b></td></tr>" for k, v in rows)
    link = f"{app}/flush-short"
    html = (f"<table role='presentation' width='100%'><tr><td style='padding:24px;font-family:Arial,sans-serif;color:#111'>"
            f"<h2 style='margin:0 0 12px'>{escape(name)} · Flush Short journal</h2><table role='presentation'>{tr}</table>"
            f"<p>Open the journal: <a href='{escape(link)}'>{escape(link)}</a></p>"
            f"<p style='font-size:12px;color:#888'>Paper journal alert: no order was placed. Verify on the exchange before trading.</p></td></tr></table>")
    return subject, html


async def notify(rows, now):
    """Email each fresh Flush Short event once (new trade, close, not filled). Follows the site's alert switches."""
    import alert_mode
    import alerts
    from channel_config import CFG
    live_ts = regime._ts(LIVE_IST)
    todo = []
    for x in rows:
        if x["cap_t"] < live_ts:
            continue
        sid = x["signal_id"]
        if x.get("trade_side") and now - x["cap_t"] < ENTRY_WINDOW:
            todo.append(("entry", f"flush-entry-{sid}", x))
        if x.get("status") == "CLOSED" and now - x.get("exit_t", 0) < EXIT_WINDOW:
            todo.append(("exit", f"flush-exit-{sid}", x))
        if x.get("rule") == "Not filled" and now - x["cap_t"] < EXIT_WINDOW:
            todo.append(("not_filled", f"flush-nofill-{sid}", x))
    if not todo:
        return []
    mode = await alert_mode.get_mode()
    sent = []
    for kind, key, x in todo:
        if await db.alerts_log.find_one({"_id": key}, {"_id": 1}):
            continue
        entry = {"_id": key, "at": iso(now_utc()), "signal_id": x["signal_id"], "channel": "email", "kind": f"flush-{kind}", "test": False}
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
                log.warning(f"flush email failed: {e}")
                entry["result"] = f"failed: {str(e)[:160]}"
        try:
            await db.alerts_log.insert_one(entry)
        except Exception:
            continue
        sent.append(entry)
    return sent


async def loop():
    await asyncio.sleep(75)                     # after the Regime journal has frozen the features
    while True:
        try:
            await compute()
        except Exception as e:
            STATE["error"] = str(e)[:200]
            log.exception("flush compute failed")
        await asyncio.sleep(120)
