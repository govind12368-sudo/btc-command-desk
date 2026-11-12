"""Orchestration: builds context from adapters, runs engines, swing lifecycle, auto-capture, direction logs."""
import asyncio
import logging
from datetime import timedelta
from core import db, now_utc, iso, parse_iso, age_sec, ist_date, get_settings, finite, tick_ceil, tick_floor
from sources import REGISTRY as R, WS
from indicators import frame_metrics, trend_dir
import strategy as ST
import models_calc as MC
import direction_state as DS
import journal as JN
import validation as VA
import sources

log = logging.getLogger("engine")
STATE = {"computed_at": None}
_FRAMES = {"key": None, "frames": {}}
_COMPUTE_LOCK = asyncio.Lock()
_REVIEW_TASK = None
_REVIEW_NEXT_REFRESH = 0.0
_REVIEW_REFRESH_SECONDS = 10
_REVIEW_TIMEOUT_SECONDS = 5


def frames():
    c = R["delta_candles"]
    if not c.data:
        return {}
    if _FRAMES["key"] != c.last_success:
        _FRAMES["frames"] = {tf: frame_metrics(v["bars"], {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}[tf]) for tf, v in c.data.items()}
        _FRAMES["key"] = c.last_success
    return _FRAMES["frames"]


_SCALP_HIST = {"key": None, "value": None}


def scalp_history(s):
    """Count completed 5m bars in the confirmation window (before the latest bar) that met the base scalp setup."""
    c = R["delta_candles"]
    if not c.data or "5m" not in c.data or "15m" not in c.data:
        return None
    b5, b15 = c.data["5m"]["bars"], c.data["15m"]["bars"]
    look = int(s["scalp_confirm_lookback_min"])
    k = max(0, look // 5)
    if not b5:
        return None
    key = (b5[-1]["t"], len(b5), b15[-1]["t"] if b15 else None, look, s["close_extreme_atr"], s["min_scalp_volume_ratio"])
    if _SCALP_HIST["key"] == key:
        return _SCALP_HIST["value"]
    counts, checked = {"LONG": 0, "SHORT": 0}, 0
    for idx in range(max(0, len(b5) - 1 - k), len(b5) - 1):
        sub = b5[:idx + 1]
        m = frame_metrics(sub, 300)
        if not m.get("valid"):
            continue
        close_t = sub[-1]["t"] + 300
        mh = frame_metrics([b for b in b15 if b["t"] + 900 <= close_t], 900)
        ht = trend_dir(mh)
        if ht is None:
            continue
        checked += 1
        d = ST.scalp_direction(m, ht, s)
        if d:
            counts[d] += 1
    value = {**counts, "lookback_min": look, "bars_checked": checked, "bars_expected": k} if checked else None
    _SCALP_HIST.update(key=key, value=value)
    return value


def quote():
    q = WS.get("quote")
    if q and q.get("bid") and age_sec(q["received"]) is not None and age_sec(q["received"]) < 10 and WS["status"] == "connected":
        return {**q, "source": "Delta WebSocket v2/ticker"}
    t = R["delta_ticker"].data
    if t:
        return {"bid": t["bid"], "ask": t["ask"], "last": t["last"], "mark": t["mark"], "event_time": t["event_time"],
                "received": iso(R["delta_ticker"].last_success), "source": "Delta REST ticker (backup)"}
    return {}


async def review_status(s, cal_ok):
    today = ist_date()
    rv = await db.reviews.find_one({"_id": today}) or {}
    issues, status, blocked = [], "REVIEW", False
    for k, label in (("risk", "risk"), ("news", "news"), ("venue", "venue"), ("journal", "journal")):
        if not (rv.get(k) or {}).get("ok"):
            issues.append(f"{label} confirmation for {today} IST pending")
    now = now_utc()
    news_ok = bool((rv.get("news") or {}).get("ok"))
    crit_from = iso(now - timedelta(minutes=s["critical_news_window_min"]))
    high_from = iso(now - timedelta(minutes=s["high_news_window_min"]))
    crit = await db.news_items.find_one({"impact": "CRITICAL", "published_at": {"$gte": crit_from, "$lte": iso(now)}, "review_status": {"$ne": "IGNORED"}}, sort=[("published_at", -1)])
    if crit:
        blocked = True
        issues.append(f"CRITICAL headline within {s['critical_news_window_min']} min: “{crit['title'][:90]}” ({crit['publisher']})")
    if news_ok:
        at = rv["news"]["at"]
        newer = await db.news_items.find_one({"impact": {"$in": ["CRITICAL", "HIGH"]}, "published_at": {"$gt": at, "$gte": high_from, "$lte": iso(now)}, "review_status": {"$ne": "IGNORED"}}, sort=[("published_at", -1)])
        if newer:
            news_ok = False
            issues.append(f"new {newer['impact']} headline after your news review: “{newer['title'][:80]}” — re-review needed")
    ev_block = []
    async for ev in db.manual_events.find({"impact": {"$in": ["HIGH", "CRITICAL"]}}):
        t = parse_iso(ev["scheduled_utc"])
        if t - timedelta(minutes=s["event_before_min"]) <= now <= t + timedelta(minutes=s["event_after_min"]):
            ev_block.append(ev["title"])
    import re
    for ev in (R["bls"].data or {}).get("events", []):
        if re.search(r"\b(Consumer Price Index|Employment Situation|Producer Price Index|Job Openings|Employment Cost Index|FOMC|GDP|Gross Domestic Product|Personal Income and Outlays)\b", ev["title"], re.I):
            t = parse_iso(ev["time"])
            if t - timedelta(minutes=s["event_before_min"]) <= now <= t + timedelta(minutes=s["event_after_min"]):
                ev_block.append(f"{ev['title']} ({ev.get('publisher') or 'official calendar'})")
    if ev_block:
        blocked = True
        issues.append("economic event window active: " + ", ".join(ev_block))
    ed, fd = rv.get("event_decision"), rv.get("flow_decision")
    if not cal_ok:
        issues.append("official calendar coverage is partial, stale or unavailable; event risk remains unknown")
    if ed != "PASS":
        issues.append(f"event decision is {ed or 'not set'}" + ("" if cal_ok else " (calendar source unavailable — an empty or failed calendar is not 'no events')"))
    if fd != "PASS":
        issues.append(f"large-flow decision is {fd or 'not set'}")
    if "BLOCKED" in (ed, fd):
        blocked = True
    fills = [f async for f in db.fills.find({"exit_price": None})]
    inc = [f["_id"] for f in fills if not JN.complete_open(f)]
    if inc:
        issues.append(f"{len(inc)} open actual record(s) incomplete")
    ok = not issues
    return {"ok": ok, "status": "PASS" if ok else "BLOCKED" if blocked else "REVIEW", "detail": "; ".join(issues) or "today's risk, news, venue, journal confirmations complete; event and flow PASS",
            "advisory": True, "affects_decision": False, "affects_direction": False,
            "news_ok": news_ok, "doc": {k: v for k, v in rv.items()}}


async def _refresh_advisory_review(settings):
    """Keep informational review warnings outside the market computation path."""
    try:
        calendar = R.get("bls")
        cal_ok = calendar is not None and calendar.health() == "ok"
        result = await asyncio.wait_for(review_status(settings, cal_ok), _REVIEW_TIMEOUT_SECONDS)
        result.update(advisory=True, affects_decision=False, affects_direction=False)
        STATE["review"] = result
    except Exception:
        log.warning("Informational news/event review unavailable; market calculations continue", exc_info=True)
        STATE["review"] = {"ok": False, "status": "UNAVAILABLE", "advisory": True,
                           "affects_decision": False, "affects_direction": False,
                           "detail": "Informational news/event review unavailable. Trade decisions and BTC direction do not depend on this review.",
                           "news_ok": False, "doc": {}}


def _schedule_advisory_review(settings):
    global _REVIEW_TASK, _REVIEW_NEXT_REFRESH
    STATE.setdefault("review", {"ok": False, "status": "UNAVAILABLE", "advisory": True,
                                "affects_decision": False, "affects_direction": False,
                                "detail": "Informational review loading; no effect on trade decisions or BTC direction.",
                                "news_ok": False, "doc": {}})
    loop = asyncio.get_running_loop()
    if (_REVIEW_TASK is None or _REVIEW_TASK.done()) and loop.time() >= _REVIEW_NEXT_REFRESH:
        _REVIEW_NEXT_REFRESH = loop.time() + _REVIEW_REFRESH_SECONDS
        _REVIEW_TASK = asyncio.create_task(_refresh_advisory_review(dict(settings)))


def _plan_for(direction_bar_metrics, ev, s, prod):
    lo = direction_bar_metrics
    lv = ev["levels"]
    d = lv["direction"]
    close_ts = lo["bar_close"]
    return {"_id": f"SW-{d}-{lo['bar_open']}", "plan_id": f"SW-{d}-{lo['bar_open']}", "venue": "Delta Exchange India", "instrument": "BTCUSD perpetual",
            "direction": d, "confirm_bar_open": lo["bar_open"], "confirm_bar_close": close_ts, "confirm_close": lo["close"],
            "entry": lv["entry"], "stop": lv["stop"], "target": lv["target"], "qty_btc": lv["qty_btc"], "contracts": lv["contracts"],
            "atr": lo["atr14"], "breakout_ref": lo["prior20_high"] if d == "LONG" else lo["prior20_low"],
            "tick_size": prod["tick_size"], "contract_value": prod["contract_value"],
            "costs": {"fee_per_side": s["fee_per_side"], "slippage_bps": s["slippage_bps"], "funding_bps": s["funding_bps"]},
            "net_r": lv["net_r"], "capture_checks": ev["checks"], "captured_at": iso(now_utc()),
            "expires_at": iso((close_ts // 14400 + 1) * 14400), "status": "ACTIVE", "status_reason": None}


async def archive_plan(p, status, reason):
    p = dict(p)
    p.update(status=status, status_reason=reason, archived_at=iso(now_utc()))
    await db.plan_archive.update_one({"_id": p["_id"]}, {"$setOnInsert": p}, upsert=True)
    await db.swing_plans.delete_one({"_id": p["_id"]})


async def monitor_plans(fr):
    b5 = R["delta_candles"].data["5m"]["bars"] if R["delta_candles"].data else []
    b1h = R["delta_candles"].data["1h"]["bars"] if R["delta_candles"].data else []
    async for p in db.swing_plans.find():
        if now_utc() >= parse_iso(p["expires_at"]):
            await archive_plan(p, "EXPIRED", f"reached 4h boundary {p['expires_at']}")
            continue
        cap = parse_iso(p["captured_at"]).timestamp()
        post = [b for b in b5 if b["t"] >= cap - cap % 300 + 300]
        issue = None
        if post:
            ts = [b["t"] for b in post]
            if len(set(ts)) != len(ts):
                issue = "duplicate post-capture 5m bars"
            elif any(b - a != 300 for a, b in zip(ts, ts[1:])):
                issue = "missing post-capture 5m bar(s)"
            elif now_utc().timestamp() - (ts[-1] + 300) > 300 + 120:
                issue = "post-capture 5m bars not current"
        elif now_utc().timestamp() - cap > 300 + 300 + 120:
            issue = "no post-capture 5m bars received"
        touched = None
        for b in post:
            if p["direction"] == "LONG" and (b["l"] <= p["stop"] or b["h"] >= p["target"]) or p["direction"] == "SHORT" and (b["h"] >= p["stop"] or b["l"] <= p["target"]):
                touched = b; break
        if touched:
            await archive_plan(p, "INVALIDATED", f"stop or target touched by completed 5m bar {iso(touched['t'])}")
            continue
        lost = [b for b in b1h if b["t"] > p["confirm_bar_open"] and ((p["direction"] == "LONG" and b["c"] <= p["breakout_ref"]) or (p["direction"] == "SHORT" and b["c"] >= p["breakout_ref"]))]
        if lost:
            await archive_plan(p, "INVALIDATED", f"completed 1h candle {iso(lost[0]['t'])} closed back through breakout {p['breakout_ref']}")
            continue
        new_status = "PAUSED" if issue else "ACTIVE"
        if new_status != p["status"] or (issue or None) != p.get("status_reason"):
            await db.swing_plans.update_one({"_id": p["_id"]}, {"$set": {"status": new_status, "status_reason": issue, "status_changed": iso(now_utc())}})


async def maybe_capture_plan(ev, fr, s, prod):
    if ev["plan_id"] or not ev["mandatory_pass"] or not ev["levels"]:
        return
    lo = fr["1h"]
    d = ev["direction"]
    newer = await db.plan_archive.find_one({"direction": d, "confirm_bar_open": {"$gte": lo["bar_open"]}})
    if newer or await db.swing_plans.find_one({}):
        return
    p = _plan_for(lo, ev, s, prod)
    await db.swing_plans.update_one({"_id": p["_id"]}, {"$setOnInsert": p}, upsert=True)


OPEN_SIGNAL_WINDOW_H = 24  # a model signal still PENDING after this long no longer blocks new captures


async def maybe_capture_signal(ev, q, plan, s=None):
    if ev["status"] not in ("BUY", "SELL"):
        return
    lv = ev["levels"]
    lower = ST.CFG[ev["strategy"]]["lower"]
    bar = plan["confirm_bar_open"] if plan else ev["frames"][lower]["bar_open"]
    sid = f"{ev['strategy'].upper()}-{lv['direction']}-{bar}"
    cap = int((s or {}).get("max_open_model_signals", 0) or 0)
    if cap > 0:
        # Record one setup at a time per strategy (as it would really be traded), instead of a new
        # signal on every 5m bar while the same move continues.
        since = iso(now_utc() - timedelta(hours=OPEN_SIGNAL_WINDOW_H))
        open_n = await db.signals.count_documents({"strategy": ev["strategy"], "frozen": False, "_id": {"$ne": sid}, "captured_at": {"$gte": since}})
        if open_n >= cap:
            return
    doc = {"signal_id": sid, "strategy": ev["strategy"], "direction": lv["direction"], "confirm_bar_open": iso(bar),
           "entry": lv["entry"] if not plan else lv["entry_live"], "stop": lv["stop"], "target": lv["target"], "qty_btc": lv["qty_btc"],
           "net_r": lv["net_r"], "captured_at": iso(now_utc()), "quote_time": q.get("event_time"), "quote_source": q.get("source"),
           "candle_retrieved": iso(R["delta_candles"].last_success), "checks": ev["checks"], "plan_id": ev["plan_id"],
           "observation": "PENDING", "observed_at": None, "frozen": False,
           "entry_type": lv.get("entry_type", "MARKET"), "target_type": lv.get("target_type"), "valid_until": iso(lv["valid_until"]) if lv.get("valid_until") else None,
           "limit_wait_bars": lv.get("limit_wait_bars"), "tick": lv.get("tick"),
           "fill_status": "WAITING FILL" if lv.get("entry_type") == "POST-ONLY LIMIT" else "FILLED"}
    res = await db.signals.update_one({"_id": sid}, {"$setOnInsert": doc}, upsert=True)
    if res.upserted_id:
        import alerts
        asyncio.create_task(alerts.notify(doc))


async def update_signal_observations():
    bars = await VA.load_bars(int(now_utc().timestamp()) - 3 * 86400)
    live = R["delta_candles"].data["5m"]["bars"] if R["delta_candles"].data else []
    m = {b["t"]: b for b in bars}
    m.update({b["t"]: b for b in live})
    allb = [m[k] for k in sorted(m)]
    async for sg in db.signals.find({"frozen": False}):
        if sg.get("entry_type") == "POST-ONLY LIMIT" and sg.get("tick") and sg.get("limit_wait_bars"):
            o = JN.limit_fill_outcome(sg["direction"], sg["entry"], sg["stop"], sg["target"], sg["tick"], allb,
                                      int(parse_iso(sg["confirm_bar_open"]).timestamp()), sg["limit_wait_bars"])
            await db.signals.update_one({"_id": sg["_id"]}, {"$set": {"observation": o["outcome"], "observed_at": o.get("at"), "frozen": o["frozen"],
                                                                     "fill_status": o["fill_status"], "filled_at": o.get("filled_at")}})
            continue
        o = JN.first_touch(sg["direction"], sg["stop"], sg["target"], allb, parse_iso(sg["captured_at"]).timestamp())
        await db.signals.update_one({"_id": sg["_id"]}, {"$set": {"observation": o["outcome"], "observed_at": o.get("at"), "frozen": o["frozen"]}})


async def log_direction(model, direction, score, q, s, prod):
    if direction not in ("LONG", "SHORT") or not q.get("bid") or not prod:
        return
    # A score hovering near zero flips sign on every tick of trade/book data. Only record a
    # direction with a real tilt, and at most one snapshot per model per 5-minute candle.
    if not finite(score) or abs(score) < s["snapshot_min_score"]:
        return
    last = await db.validation_snapshots.find_one({"model": model, "auto": True}, sort=[("capture_ts", -1)])
    if last and last["direction"] == direction:
        return
    nowts = int(now_utc().timestamp())
    if last and last["capture_ts"] // 300 == nowts // 300:
        return
    tick = prod["tick_size"]
    entry = round(((q["bid"] + q["ask"]) / 2) / tick) * tick
    sf, tr = s["swing_stop"], s["swing_target_r"]
    stop = tick_floor(entry * (1 - sf), tick) if direction == "LONG" else tick_ceil(entry * (1 + sf), tick)
    target = tick_floor(entry + (entry - stop) * tr, tick) if direction == "LONG" else tick_ceil(entry - (stop - entry) * tr, tick)
    ts = now_utc()
    sid = f"{model}-{int(ts.timestamp())}"
    await db.validation_snapshots.insert_one({"_id": sid, "snapshot_id": sid, "model": model, "auto": True, "capture_time": iso(ts),
                                              "capture_ts": int(ts.timestamp()), "quote_time": q.get("event_time"), "direction": direction,
                                              "score": score, "entry": entry, "stop": stop, "target": target, "venue": "Delta Exchange India",
                                              "symbol": "BTCUSD", "stop_fraction": sf, "target_ratio": tr, "outcome": "PENDING",
                                              "frozen": False, "version": 1})


async def _compute_once():
    s, sv = await get_settings()
    fr = frames()
    q = quote()
    prod = R["delta_product"].data
    fills = [f async for f in db.fills.find()]
    limits = JN.today_limits(fills, s)
    _schedule_advisory_review(s)
    plan = await db.swing_plans.find_one({"status": "ACTIVE"})
    paused = await db.swing_plans.find_one({"status": "PAUSED"})
    open_by = {st: any(not finite(f.get("exit_price")) and f.get("strategy") == st for f in fills) for st in ("scalp", "swing")}
    base = {"frames": fr, "quote": q, "product": prod, "candles_retrieved": R["delta_candles"].last_success,
            "product_retrieved": R["delta_product"].last_success, "limits": limits}
    scalp = ST.evaluate("scalp", {**base, "open_position": open_by["scalp"], "scalp_history": scalp_history(s)}, s)
    swing = ST.evaluate("swing", {**base, "open_position": open_by["swing"]}, s, plan)
    if paused and not plan:
        swing["paused_plan"] = {k: paused[k] for k in ("plan_id", "direction", "entry", "stop", "target", "status_reason", "expires_at")}
        if swing["status"] in ("BUY", "SELL", "READY TO CAPTURE"):
            swing["status"] = "WAIT"
    if prod and fr.get("1h", {}).get("valid") and not paused:
        await maybe_capture_plan(swing, fr, s, prod)
    for ev in (scalp, swing):
        if not ev["status"] == "MONITOR":
            await maybe_capture_signal(ev, q, plan if ev["strategy"] == "swing" else None, s)
    macro = MC.macro_block(R, s)
    pil = MC.five_pillars(R, s, macro)
    live = await DS.stable_direction(db, pil, MC.live_direction(pil), R["okx"], s, sv, now_utc())
    four = MC.four_factor(R, macro)
    six = MC.six_factor(pil, four)
    flow = MC.flow_observation(WS, prod)
    serr = ST.validate_settings(s)
    ready = MC.readiness(scalp, swing, live["coverage"], not limits["issues"], not serr)
    if live["direction"] in ("LONG", "SHORT"):
        await log_direction("FIVE_PILLAR_CONFIRMED_V2", live["direction"], live["score"], q, s, prod)
    if six["complete"]:
        await log_direction("ORIGINAL_SIX_FACTOR", six["direction"], six["score"], q, s, prod)
    statuses = [scalp["status"], swing["status"]]
    exe = [e for e in (scalp, swing) if e["status"] in ("BUY", "SELL")]
    if exe:
        decision, why = exe[0]["status"], f"{exe[0]['strategy'].title()}: all {len(exe[0]['checks'])} active checks pass (F31 excluded)"
    elif "MONITOR" in statuses:
        decision, why = "MONITOR", "an actual position is open — monitoring"
    elif "READY TO CAPTURE" in statuses:
        decision, why = "READY TO CAPTURE", "mandatory gates pass; remaining checks: " + ", ".join(sorted(set(scalp["failing"] if scalp["status"] == "READY TO CAPTURE" else swing["failing"])))
    else:
        decision, why = "WAIT", "no strategy has a qualified, fully checked signal"
    plan_doc = plan or paused
    STATE.update({
        "computed_at": iso(now_utc()), "settings_version": sv, "quote": q,
        "price": q.get("last") or (R["delta_ticker"].data or {}).get("last"),
        "ticker": R["delta_ticker"].data and {k: v for k, v in R["delta_ticker"].data.items() if k != "book"},
        "product": prod, "ws": {k: v for k, v in WS.items() if k not in ("trades", "book", "quote") and not k.startswith("_")} | {"trades_buffered": len(WS["trades"])},
        "decision": {"decision": decision, "reason": why, "note": "A directional bias never becomes an executable trade by itself."},
        "scalp": scalp, "swing": swing, "plan": plan_doc and {k: v for k, v in plan_doc.items() if k not in ("_id", "capture_checks")},
        "pillars": pil, "live_direction": live, "four_factor": four, "six_factor": six, "macro": macro,
        "flow": flow, "readiness": ready, "limits": limits,
        "mtf": VA.mtf_alignment(fr), "settings_errors": serr,
    })


async def compute():
    # The audit recovery endpoint and background loop share the same writer.
    async with _COMPUTE_LOCK:
        await _compute_once()
        STATE.pop("compute_error", None)


async def compute_loop():
    while True:
        try:
            await compute()
        except Exception as e:
            log.exception("compute failed")
            STATE["compute_error"] = f"{type(e).__name__}: {e}"
        await asyncio.sleep(2)


async def slow_loop():
    n = 0
    while True:
        try:
            if R["delta_candles"].data:
                await monitor_plans(frames())
                await update_signal_observations()
            if n % 20 == 0:
                await VA.sync_history()
                await VA.evaluate_snapshots()
                s, _ = await get_settings()
                prod = R["delta_product"].data
                if prod:
                    bars = await VA.load_bars(int(now_utc().timestamp()) - 30 * 86400)
                    VA.BACKTEST.update(VA.run_backtest(bars, s, prod["tick_size"]))
                    VA.BACKTEST["computed_at"] = iso(now_utc())
                    VA.BACKTEST["bars"] = len(bars)
                await oi_snapshot()
        except Exception:
            log.exception("slow loop failed")
        n += 1
        await asyncio.sleep(15)


async def oi_snapshot():
    o, d = R["okx"].data, R["delta_ticker"].data
    ts = now_utc()
    if o:
        await db.oi_history.insert_one({"t": iso(ts), "venue": "OKX", "instrument": "BTC-USDT-SWAP", "oi_btc": o["oi_btc"], "oi_usd": o["oi_usd"], "funding_fraction": o["funding_fraction"], "observed": o["oi_time"]})
    if d:
        await db.oi_history.insert_one({"t": iso(ts), "venue": "Delta", "instrument": "BTCUSD", "oi_btc": d["oi_btc"], "oi_usd": d["oi_usd"], "funding_fraction": d["funding_rate_pct_8h"] / 100, "observed": d["event_time"]})
    ch = {}
    for hrs in (1, 24):
        old = await db.oi_history.find_one({"venue": "OKX", "t": {"$lte": iso(ts - timedelta(hours=hrs))}}, sort=[("t", -1)])
        if old and o and age_sec(old["t"]) <= hrs * 3600 + 900:
            ch[f"{hrs}h_pct"] = (o["oi_btc"] / old["oi_btc"] - 1) * 100
        else:
            ch[f"{hrs}h_pct"] = None
    ch["note"] = "OI change unavailable until the app has collected enough OI history" if None in ch.values() else "from stored OKX OI snapshots"
    sources.EXTRA["oi_change"] = ch
