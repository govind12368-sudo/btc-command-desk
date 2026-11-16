"""Desk summary for the BTC Dashboard: one short read of every module and the weight each one carries.

It is a summary of what the other pages already show, not a signal:
  * it never changes the scalping or swing signals, their checks or their settings;
  * the weights are a judgement and the total has not been backtested;
  * a module without data is left out and the remaining weights are shared out again; nothing is estimated.
Each module is turned into a reading from -1 (bearish) to +1 (bullish); the market read is the weighted average x 100.
"""
from core import now_utc, iso, parse_iso

MODULES = {"scanner": ("BTC Scanner", "/scanner"), "pillars": ("Five Pillars", "/pillars"), "intel": ("Market Intelligence", "/intel"), "news": ("News & Events", "/news")}
WEIGHTS = {"intraday": {"scanner": 30, "pillars": 30, "intel": 30, "news": 10}, "swing": {"scanner": 25, "pillars": 25, "intel": 40, "news": 10}}
PILLARS = {"money_flow": "Money flow", "liquidity": "Liquidity", "positioning": "Positioning", "macro": "Macro", "technical": "Technical"}
NOTE = ("A weighted summary of what the four modules already show. The weights are a judgement, the modules partly overlap (the scanner reads some pillars), "
        "and the total has not been backtested. It does not change any scalping or swing signal.")


def clamp(v):
    return max(-1.0, min(1.0, v))


def label(v):
    """v on the -1..+1 scale."""
    return None if v is None else "BULLISH" if v >= 0.5 else "LEAN BULLISH" if v >= 0.2 else "BEARISH" if v <= -0.5 else "LEAN BEARISH" if v <= -0.2 else "NEUTRAL"


BIAS = {"BULLISH": 1, "BEARISH": -1, "NEUTRAL": 0, "BUYERS": 1, "SELLERS": -1, "BALANCED": 0}
low = lambda s: str(s or "").capitalize()


def _mean(parts):
    v = [p[1] for p in parts if p[1] is not None]
    return (sum(v) / len(v), "; ".join(p[0] for p in parts if p[1] is not None)) if v else (None, "No data")


def r_scanner(scan, view):
    d = (scan or {}).get(view) or {}
    if not d.get("factors"):
        return None, "No data"
    v = 1.0 if d["decision"] == "BUY" else -1.0 if d["decision"] == "SELL" else clamp(0.5 * d["score"])
    return v, f"{d['decision']}: {d['for_buy']} for buy, {d['for_sell']} for sell, {d['neutral']} neutral"


def r_pillars(st, view):
    six, live = (st or {}).get("six_factor") or {}, (st or {}).get("live_direction") or {}
    if view == "swing" and six.get("complete") and six.get("score") is not None:
        return clamp(six["score"] / 0.30), f"Six-factor model {six['direction']} ({six['score']:+.2f}, {low(six.get('strength'))})"
    if live.get("score") is None:
        return None, low(live.get("reason")) or "No data"
    if live.get("direction") == "WAIT":                   # tilt exists but the 15-minute confirmation is not complete: count it at half strength
        return clamp(live["score"] / 0.30) * 0.5, f"Direction model not confirmed yet (tilt {live['score']:+.2f}, {live.get('coverage', 0)} of 5 pillars): counted at half strength"
    return clamp(live["score"] / 0.30), f"Direction model {live['direction']} ({live['score']:+.2f}, {live.get('coverage', 0)} of 5 pillars)"


def r_intel(I, view):
    tf, parts = I.get("tf") or {}, []
    frames = ("15m", "1h", "4h") if view == "intraday" else ("4h", "1d")
    got = [(f, tf[f]["bias"]) for f in frames if (tf.get(f) or {}).get("bias") in BIAS]
    if got:
        parts.append(("Trend " + ", ".join(f"{f} {low(b)}" for f, b in got), sum(BIAS[b] for _, b in got) / len(got)))
    if view == "intraday":
        pos = ((I.get("deriv") or {}).get("positioning") or {}).get("label")
        if pos in BIAS:
            parts.append((f"Futures positioning {low(pos)}", BIAS[pos]))
        w = ((I.get("pressure") or {}).get("windows") or {}).get("1h") or {}
        if w.get("label") in BIAS:
            parts.append((f"Spot 1h {w['buy_share'] * 100:.0f}% buying", BIAS[w["label"]]))
    else:
        e, m, r = I.get("etf") or {}, (I.get("macro") or {}).get("summary") or {}, I.get("risk") or {}
        if e.get("label"):
            parts.append((f"ETF: {e['label'][0].lower() + e['label'][1:]}", 1 if "buying" in e["label"] else -1 if "selling" in e["label"] else 0))
        if m.get("label"):
            parts.append((f"Macro: {m['headwinds']} against, {m['tailwinds']} in favour", 1 if "favour" in m["label"] else -1 if "against" in m["label"] else 0))
        c, rc = (r.get("crash") or {}).get("score"), (r.get("recovery") or {}).get("score")
        if c is not None and rc is not None:
            parts.append((f"Crash risk {c} vs recovery {rc}", (rc - c) / 100))
    return _mean(parts)


def r_news(I):
    nw = (I.get("sentiment") or {}).get("news")
    if not nw or not nw.get("total"):
        return None, "No data"
    v = 0.5 if "positive than" in nw["tone"] else -0.5 if "negative than" in nw["tone"] else 0.0
    return v, f"{nw['tone']} ({nw['bullish_words']} positive, {nw['bearish_words']} negative of {nw['total']} headlines)"


def compose(view, readings):
    """readings: {module: (value or None, detail)} -> weighted market read on a -100..+100 scale."""
    w = WEIGHTS[view]
    avail = sum(w[k] for k in w if readings[k][0] is not None)
    rows, total = [], 0.0
    for k in w:
        v, detail = readings[k]
        share = w[k] / avail * 100 if v is not None and avail else None
        pts = v * share if share is not None else None
        total += pts or 0
        rows.append({"key": k, "name": MODULES[k][0], "to": MODULES[k][1], "weight": w[k], "share": share, "reading": v, "reading_label": label(v), "points": pts, "detail": detail})
    score = round(total) if avail else None
    return {"view": view, "score": score, "label": label(None if score is None else score / 100), "rows": rows, "coverage": avail,
            "rule": "Bullish at +50 or more, lean bullish from +20, neutral between -20 and +20, lean bearish to -50, bearish below."}


def plan_link(ev, read, name, to):
    """How the wider picture sits against a strategy's own signal. Information only: the signal is never altered."""
    ev = ev or {}
    d, sc = ev.get("direction"), read["score"]
    side = 1 if d == "LONG" else -1 if d == "SHORT" else 0
    lean = "bearish" if (sc or 0) <= -20 else "bullish" if (sc or 0) >= 20 else None
    word = "signal" if ev.get("status") in ("BUY", "SELL") else "plan"
    if sc is None:
        fit, text = "UNKNOWN", "The market read has no data yet."
    elif side == 0:
        fit = "NO_SIGNAL"
        text = "No signal direction right now. " + (f"The market read leans {lean}: a {'SHORT' if lean == 'bearish' else 'LONG'} signal would have the wider picture behind it, a {'LONG' if lean == 'bearish' else 'SHORT'} signal would be going against it."
                                                    if lean else "The market read is mixed, so it favours neither side.")
    elif side * sc >= 20:
        fit, text = "AGREES", f"The market read agrees with this {d} {word}."
    elif side * sc <= -20:
        fit, text = "AGAINST", f"The market read is against this {d} {word}. The {word} and its checks are unchanged; treat this as a caution."
    else:
        fit, text = "MIXED", f"The market read is mixed, so it neither supports nor opposes this {d} {word}."
    lv = ev.get("levels") or {}
    return {"name": name, "to": to, "status": ev.get("status"), "direction": d, "passed": ev.get("passed"), "total": ev.get("total_checks") or len(ev.get("checks") or []) or None,
            "fit": fit, "text": text, "read": read["score"], "read_label": read["label"],
            "levels": {k: lv.get(k) for k in ("entry", "stop", "target")} if lv.get("entry") else None}


def upcoming(events, now, hours=72, n=3):
    out = []
    for e in events or []:
        t = parse_iso(e.get("time") or e.get("scheduled_utc"))
        if t and 0 <= (t - now).total_seconds() <= hours * 3600:
            out.append({"title": e.get("title"), "time": iso(t), "impact": e.get("impact"), "hours": round((t - now).total_seconds() / 3600, 1)})
    return sorted(out, key=lambda x: x["time"])[:n]


def cards(st, scan, I, events):
    """One short card per module, in plain words, each with a link to its page."""
    st, scan, out = st or {}, scan or {}, []
    intr, sw, day = scan.get("intraday") or {}, scan.get("swing") or {}, scan.get("day_type") or {}
    out.append({"key": "scanner", "title": "BTC Scanner", "to": "/scanner", "ok": bool(intr.get("factors")),
                "headline": f"Today {intr.get('decision', '—')} · Swing {sw.get('decision', '—')}", "tone": label(r_scanner(scan, "intraday")[0]),
                "lines": [{"k": "Day type", "v": day.get("label") or "—"},
                          {"k": "Today view", "v": f"{intr.get('for_buy', 0)} for buy, {intr.get('for_sell', 0)} for sell, {intr.get('neutral', 0)} neutral"},
                          {"k": "Swing view", "v": f"{sw.get('for_buy', 0)} for buy, {sw.get('for_sell', 0)} for sell, {sw.get('neutral', 0)} neutral"},
                          {"k": "Playbook", "v": scan.get("playbook") or "—"}]})
    r, tf, fc = I.get("risk") or {}, I.get("tf") or {}, (I.get("forecast") or {}).get("rows") or []
    c, rc, e, m = r.get("crash") or {}, r.get("recovery") or {}, I.get("etf") or {}, (I.get("macro") or {}).get("summary") or {}
    f, pos = (I.get("sentiment") or {}).get("fng") or {}, ((I.get("deriv") or {}).get("positioning") or {}).get("label")
    d24 = next((x for x in fc if x.get("horizon") == "24h" and x.get("status") == "OK"), None)
    lines = [{"k": "Trend", "v": ", ".join(f"{k} {low(tf[k]['bias'])}" for k in ("1d", "4h", "1h") if (tf.get(k) or {}).get("bias")) or "—"},
             {"k": "Futures", "v": low(pos) or "—"},
             {"k": "ETF flows", "v": f"{e['label']} ({e['streak']['days']} {e['streak']['side'].lower()} day{'s' if e['streak']['days'] != 1 else ''} in a row)" if e.get("label") else "—"},
             {"k": "US macro", "v": m.get("label") or "—"},
             {"k": "Fear & Greed", "v": f"{f['value']} ({f['label']})" if f else "—"}]
    if d24:
        lines.append({"k": "Likely 24h range", "v": f"{d24['range68'][0]:,.0f} – {d24['range68'][1]:,.0f}"})
    out.append({"key": "intel", "title": "Market Intelligence", "to": "/intel", "ok": bool(c or tf),
                "headline": f"Crash risk {c.get('score', '—')} ({low(c.get('band')) or 'no data'}) · Recovery {rc.get('score', '—')} ({low(rc.get('band')) or 'no data'})",
                "tone": label(r_intel(I, "swing")[0]), "lines": lines})
    live, pil = st.get("live_direction") or {}, st.get("pillars") or {}
    out.append({"key": "pillars", "title": "Five Pillars", "to": "/pillars", "ok": live.get("score") is not None,
                "headline": f"Direction model {live.get('direction', '—')}" + (f" ({live['score']:+.2f})" if live.get("score") is not None else ""), "tone": label(r_pillars(st, "intraday")[0]),
                "pillars": [{"key": k, "name": nm, "valid": bool((pil.get(k) or {}).get("valid")), "score": (pil.get(k) or {}).get("score"), "max": 2 if k == "macro" else 1,
                             "share": (live.get("active_shares") or {}).get(k)} for k, nm in PILLARS.items()],
                "lines": [{"k": "Coverage", "v": f"{live.get('coverage', 0)} of 5 pillars have data"}]})
    nw = (I.get("sentiment") or {}).get("news") or {}
    ev = upcoming(events, now_utc())
    soon = [x for x in ev if x["impact"] in ("HIGH", "CRITICAL") and x["hours"] <= 24]
    out.append({"key": "news", "title": "News & Events", "to": "/news", "ok": bool(nw),
                "headline": f"{nw.get('total', 0)} headlines in 24h · {nw.get('high_impact', 0)} high impact", "tone": label(r_news(I)[0]),
                "lines": [{"k": "Tone", "v": nw.get("tone") or "—"}], "events": ev, "headlines": (nw.get("top") or [])[:2],
                "event_risk": f"High-impact event within 24 hours: {soon[0]['title']}. Expect sharp moves around it." if soon else None})
    return out


BOOKS = (("switch", "Day-type switch", "/autotrade", "Range day: Plan A. Trend day with the signal: follow. Otherwise no trade."),
         ("follow", "Follow the signal (1:2)", "/journal", "Each scalp signal traded in its own direction."),
         ("plan_a", "Plan A (reverse 1:2)", "/plan-a", "Each scalp signal traded the opposite way."))
BOOK_KEYS = ("signals", "taken", "skipped", "stood_aside", "open", "target", "stop", "time", "win_rate", "net", "max_drawdown")


def book_trades(rows, picks):
    """The trades one paper book actually took, oldest first, with the leg (follow or Plan A) it used for each signal."""
    out = []
    for r in rows or []:
        pick = (picks or {}).get(r.get("signal_id"))
        if pick not in ("follow", "plan_a"):
            continue
        f = pick == "follow"
        out.append({"signal_id": r["signal_id"], "day_type": r.get("day_label") or r.get("day_type"), "way": "Follow the signal" if f else "Plan A (reverse)",
                    "direction": r.get("model_dir") if f else r.get("plan_dir"), "entry_time": r.get("fill_ist"), "entry": r.get("entry"),
                    "stop": r.get("model_stop") if f else r.get("plan_stop"), "target": r.get("follow_target") if f else r.get("plan_target"),
                    "exit_time": (r.get("follow_exit_ist") if f else r.get("exit_ist")) or None, "exit_price": r.get("follow_exit_price") if f else r.get("exit_price"),
                    "result": r.get("follow_result") if f else r.get("plan_result"), "net": r.get("follow_net") if f else r.get("net")})
    return out


def paper_books(books, rows=None, snaps=None):
    """The three paper books from the Trade Journal, without their curves. Same signals, same fees, same rules."""
    if not books or not books.get("since"):
        return None
    period = lambda p: [{"key": k, "name": name, "to": to, "how": how, **{x: (books.get(p) or {}).get(k, {}).get(x) for x in BOOK_KEYS}} for k, name, to, how in BOOKS]
    import support
    trades = lambda p: book_trades(rows, ((books.get(p) or {}).get("switch") or {}).get("picks"))
    tr = {"since": trades("since"), "all": trades("all")}
    return {"since_date": books.get("since_date"), "since": period("since"), "all": period("all"), "switch_trades": tr,
            "support_split": {p: support.split(tr[p], snaps) for p in tr},
            "note": "Paper results after estimated fees, one trade at a time. A handful of trades proves nothing either way: in the long history test, switching by day type did not make money."}


def summary(st, scan, I, events, books=None, rows=None, auto=None, snaps=None, support_now=None):
    st = st or {}
    reads = {v: compose(v, {"scanner": r_scanner(scan, v), "pillars": r_pillars(st, v), "intel": r_intel(I, v), "news": r_news(I)}) for v in ("intraday", "swing")}
    cs = cards(st, scan, I, events)
    plans = [plan_link(st.get("scalp"), reads["intraday"], "Scalping plan", "/scalping"), plan_link(st.get("swing"), reads["swing"], "Swing plan", "/swing")]
    day = (scan or {}).get("day_type") or {}
    if day.get("label") and (scan or {}).get("playbook"):
        plans[0]["playbook"] = {"day_type": day["label"], "text": scan["playbook"], "to": "/scanner"}
    return {"updated": iso(now_utc()), "price": st.get("price"), "decision": (st.get("decision") or {}).get("decision"), "reads": reads, "weights": WEIGHTS,
            "plans": plans, "books": paper_books(books, rows, snaps), "auto": auto, "support_now": support_now,
            "cards": cs, "event_risk": next((c["event_risk"] for c in cs if c.get("event_risk")), None), "note": NOTE}
