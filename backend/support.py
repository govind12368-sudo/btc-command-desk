"""Support data for the Day-type switch: five yes/no checks, saved once at the moment a signal fires.

Information only. Nothing here is read by the day-type rule, by Auto-Trade or by the paper books, so it cannot change
which trades are taken or how they are scored. A snapshot is written only while the signal is fresh (never rebuilt with
hindsight); older trades simply have no snapshot.
"""
import logging

from core import db, now_utc, iso

log = logging.getLogger("support")
FRESH_SEC = 600
NEED = 30                                                 # recorded trades wanted before the split means anything
LEVELS = (("STRONG", "Strong (4 or 5 checks)"), ("MEDIUM", "Medium (3 checks)"), ("WEAK", "Weak (0 to 2 checks)"), ("NONE", "Not recorded (earlier trades)"))


def trade_dir(plan, signal_dir):
    if plan == "FOLLOW":
        return signal_dir
    if plan == "PLAN_A":
        return "SHORT" if signal_dir == "LONG" else "LONG"
    return None


def _c(key, name, ok, text):
    return {"key": key, "name": name, "ok": ok, "text": text if ok is not None else "No data"}


def checks(plan, tdir, day_score, I, read_score, soon_event, cascade):
    """Five independent checks for one Day-type switch trade. ok is True, False, or None when the data is missing."""
    I, side = I or {}, 1 if tdir == "LONG" else -1
    tf = I.get("tf") or {}
    out = []
    if plan == "FOLLOW":
        out.append(_c("day_clear", "Day type is clear", None if day_score is None else day_score >= 4, f"{day_score} of 5 trend measures agree (clear trend at 4 or more)"))
        s1, s4 = (tf.get("1h") or {}).get("score"), (tf.get("4h") or {}).get("score")
        out.append(_c("higher_tf", "Higher timeframes fit", None if s1 is None or s4 is None else s1 * side > 0 and s4 * side > 0,
                      f"1h score {s1 or 0:+d}, 4h score {s4 or 0:+d}; both must point {'up' if side > 0 else 'down'} for a {tdir} trade"))
    else:
        out.append(_c("day_clear", "Day type is clear", None if day_score is None else day_score == 0, f"{day_score} of 5 trend measures are on (clear range at 0)"))
        adx = (tf.get("1h") or {}).get("adx")
        out.append(_c("higher_tf", "Higher timeframes fit", None if adx is None else adx < 25, f"1h ADX {adx or 0:.0f} (a range trade wants no strong trend: below 25)"))
    out.append(_c("market_read", "Market read is not against it", None if read_score is None else read_score * side > -20, f"Intraday market read {read_score or 0:+d}; against a {tdir} trade at {'-20 or lower' if side > 0 else '+20 or higher'}"))
    press = (((I.get("pressure") or {}).get("windows") or {}).get("1h") or {}).get("label")
    pos = ((I.get("deriv") or {}).get("positioning") or {}).get("label")
    press, pos = (press if press in ("BUYERS", "SELLERS", "BALANCED") else None), (pos if pos in ("BULLISH", "BEARISH", "NEUTRAL") else None)
    against = (press == ("SELLERS" if side > 0 else "BUYERS")) or (pos == ("BEARISH" if side > 0 else "BULLISH"))
    out.append(_c("order_flow", "Order flow is not against it", None if press is None and pos is None else not against,
                  f"Spot 1h: {(press or 'no data').lower()}; futures positioning: {(pos or 'no data').lower()}"))
    hit = cascade and cascade.get("side") == ("long" if side > 0 else "short")
    out.append(_c("no_shock", "No shock nearby", not (soon_event or hit),
                  f"High-impact release within 2 hours: {soon_event['title']}" if soon_event else f"{'Long' if side > 0 else 'Short'} liquidation cascade under way" if hit else "No high-impact release within 2 hours and no liquidation cascade against the trade"))
    return out


def grade(cs):
    n = sum(1 for c in cs if c["ok"] is True)
    return {"passed": n, "of": len(cs), "level": "STRONG" if n >= 4 else "MEDIUM" if n == 3 else "WEAK"}


def now_view(day, I, read_score, soon_event, cascade):
    """What the support would be if a LONG or a SHORT scalp signal fired right now."""
    import daytype
    out = []
    for sig in ("LONG", "SHORT"):
        plan = daytype.playbook(day, sig)
        td = trade_dir(plan, sig)
        if not td:
            out.append({"signal": sig, "plan": "STAND_ASIDE", "trade_dir": None, "checks": [], "passed": None, "of": None, "level": None})
            continue
        cs = checks(plan, td, (day or {}).get("score"), I, read_score, soon_event, cascade)
        out.append({"signal": sig, "plan": plan, "trade_dir": td, "checks": cs, **grade(cs)})
    return out


async def context():
    """Live inputs for the checks: market-intelligence state, intraday market read, a near high-impact event, a liquidation cascade."""
    import desk
    import engine
    import intel
    import scanner
    from sources import REGISTRY
    ev = list((REGISTRY["bls"].data or {}).get("events", [])) if "bls" in REGISTRY else []
    ev += [x async for x in db.manual_events.find({}, {"_id": 0, "title": 1, "scheduled_utc": 1, "impact": 1})]
    soon = next((e for e in desk.upcoming(ev, now_utc(), hours=2, n=20) if e["impact"] in ("HIGH", "CRITICAL")), None)
    scan = scanner.STATE["result"] or scanner.compute(engine.STATE)
    read = desk.compose("intraday", {"scanner": desk.r_scanner(scan, "intraday"), "pillars": desk.r_pillars(engine.STATE, "intraday"),
                                     "intel": desk.r_intel(intel.STATE, "intraday"), "news": desk.r_news(intel.STATE)})["score"]
    return intel.STATE, read, soon, intel.liq_summary()["cascade"]


async def snapshot(sid, cap_ts, signal_dir, tag):
    """Save the five checks for a fresh Day-type switch signal, once. Never raises into the caller."""
    try:
        plan = (tag or {}).get("playbook")
        td = trade_dir(plan, signal_dir)
        if not td or now_utc().timestamp() - cap_ts > FRESH_SEC or await db.switch_support.find_one({"_id": sid}, {"_id": 1}):
            return None
        I, read, soon, cascade = await context()
        cs = checks(plan, td, tag.get("score"), I, read, soon, cascade)
        doc = {"_id": sid, "at": iso(now_utc()), "plan": plan, "signal_dir": signal_dir, "trade_dir": td, "checks": cs, **grade(cs)}
        await db.switch_support.update_one({"_id": sid}, {"$setOnInsert": doc}, upsert=True)
        return doc
    except Exception as e:                                # support data must never disturb the journal or the strategy
        log.warning("support snapshot %s failed: %s", sid, e)
        return None


def split(trades, snaps):
    """Results of the Day-type switch trades by the support level recorded when each signal fired."""
    rows = {k: {"level": k, "name": name, "trades": 0, "closed": 0, "wins": 0, "net": 0.0} for k, name in LEVELS}
    for t in trades or []:
        r = rows[((snaps or {}).get(t["signal_id"]) or {}).get("level") or "NONE"]
        r["trades"] += 1
        r["net"] += t.get("net") or 0.0
        if t.get("result") in ("TARGET", "STOP", "TIME"):
            r["closed"] += 1
            r["wins"] += (t.get("net") or 0) > 0
    out = [{**r, "win_rate": r["wins"] / r["closed"] if r["closed"] else None} for r in rows.values()]
    recorded = sum(r["trades"] for r in out if r["level"] != "NONE")
    return {"rows": out, "recorded": recorded, "need": NEED, "ready": recorded >= NEED,
            "note": "Information only: support does not change which trades are taken. The split means little until about 30 recorded trades."}


# ------------------------------------------------------------------ lookups for the journal pages (read-only)
DAY_TYPES = ("RANGE", "TREND_UP", "TREND_DOWN", "MIXED")
PLAYBOOKS = ("PLAN_A", "FOLLOW", "STAND_ASIDE")
LEVEL_KEYS = ("STRONG", "MEDIUM", "WEAK")


async def all_snaps():
    """Every saved support snapshot, keyed by signal id."""
    return {x["_id"]: {k: x.get(k) for k in ("level", "passed", "of", "checks", "plan", "trade_dir", "at")} async for x in db.switch_support.find({})}


async def filter_ids(day="", playbook="", level=""):
    """Signal ids whose frozen day type, switch action and recorded support level match. Empty values match everything."""
    ids = {t["_id"] async for t in db.signal_tags.find({}, {"type": 1, "playbook": 1})
           if (not day or t.get("type") == day) and (not playbook or t.get("playbook") == playbook)}
    if level:
        ids &= {x["_id"] async for x in db.switch_support.find({"level": level}, {"_id": 1})}
    return ids


def follow_summary(rows, ids):
    """Result of trading each matching, filled signal in its own direction (every signal counted, not one at a time)."""
    closed = [r for r in rows or [] if r.get("signal_id") in ids and r.get("fill_status") == "FILLED" and r.get("follow_result") in ("TARGET", "STOP", "TIME")]
    wins = sum(1 for r in closed if (r.get("follow_net") or 0) > 0)
    return {"signals": len(ids), "trades": len(closed), "wins": wins, "win_rate": wins / len(closed) if closed else None, "net": sum(r.get("follow_net") or 0 for r in closed)}
