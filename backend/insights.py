"""Desk Brief: a plain-English reading of the live state.

Rule-based and deterministic (no AI service, no API cost). It only re-states what the models
already computed — it never creates a trade signal, never changes a decision and is never used
by the strategy engine. Every sentence can be traced to a field on the dashboard.
"""


def _dir(x):
    return (x or "").upper() if isinstance(x, str) else ""


def _pct(x):
    try:
        return f"{float(x) * 100:.0f}%"
    except (TypeError, ValueError):
        return "—"


def brief(state, sources=None):
    st = state or {}
    dec = (st.get("decision") or {})
    live = st.get("live_direction") or {}
    six = st.get("six_factor") or {}
    mtf = st.get("mtf") or {}
    flow = st.get("flow") or {}
    ready = st.get("readiness") or {}
    scalp, swing = st.get("scalp") or {}, st.get("swing") or {}

    votes, points, watch = [], [], []

    ld = _dir(live.get("direction"))
    if ld in ("LONG", "SHORT"):
        votes.append(1 if ld == "LONG" else -1)
        points.append(f"Confirmed 15-minute direction is {ld} with {live.get('coverage', '—')}/5 pillars covered.")
    elif ld:
        why = str(live.get("reason") or "waiting for completed-candle confirmation")
        why = why if len(why) <= 140 else why[:137].rsplit(" ", 1)[0] + "…"
        points.append(f"Confirmed direction: {live.get('status') or ld} — {why}")

    sd = _dir(six.get("direction"))
    if six.get("complete") and sd in ("LONG", "SHORT"):
        votes.append(1 if sd == "LONG" else -1)
        points.append(f"Six-factor model leans {sd} ({six.get('strength') or 'unrated'}).")
    elif six and not six.get("complete"):
        watch.append("Six-factor model is incomplete (missing: " + (", ".join(six.get("missing") or []) or "inputs") + ").")

    up, dn = mtf.get("up"), mtf.get("down")
    if isinstance(up, int) and isinstance(dn, int) and (up or dn):
        if up > dn:
            votes.append(1)
        elif dn > up:
            votes.append(-1)
        points.append(f"Timeframes: {up} up, {dn} down on completed Delta candles.")

    if flow.get("valid"):
        lab = flow.get("label") or ""
        points.append(f"Last 5 min order flow: {lab.lower()} (book/flow {str(flow.get('agreement') or '').lower()}).")
        if flow.get("agreement") == "CONFLICT":
            watch.append("Trade flow and order book disagree — short-term moves may be noisy.")

    for ev in (scalp, swing):
        name = (ev.get("strategy") or "").title() or "Strategy"
        names = {c.get("id"): c.get("name") for c in (ev.get("checks") or []) if isinstance(c, dict)}
        fails = [f"{f} {names[f]}" if names.get(f) else str(f) for f in (ev.get("failing") or [])]
        if ev.get("status") in ("BUY", "SELL"):
            points.append(f"{name}: all active checks pass → {ev['status']} signal.")
        elif fails:
            watch.append(f"{name} needs: " + ", ".join(fails[:5]) + ("…" if len(fails) > 5 else "") + ".")

    bad = [s.get("name") or s.get("key") for s in (sources or []) if s.get("status") not in ("ok", None)]
    if bad:
        watch.append(f"{len(bad)} data source(s) not healthy: " + ", ".join(str(b) for b in bad[:4]) + ("…" if len(bad) > 4 else "") + ".")

    agree = sum(votes)
    if votes and abs(agree) == len(votes) and len(votes) >= 2:
        tone = "BULLISH" if agree > 0 else "BEARISH"
        alignment = "aligned"
    elif votes and agree != 0:
        tone = "LEAN BULLISH" if agree > 0 else "LEAN BEARISH"
        alignment = "mixed"
    else:
        tone = "NEUTRAL"
        alignment = "not enough data" if not votes else "split"

    why = {"aligned": "models aligned", "mixed": "models mixed", "split": "models split"}.get(alignment, "not enough live data yet")
    d = dec.get("decision") or "WAIT"
    if d in ("BUY", "SELL"):
        headline = f"{d} signal active — {dec.get('reason', '')}".strip(" —")
    elif d == "MONITOR":
        headline = "Position open — monitoring only."
    elif d == "READY TO CAPTURE":
        headline = f"Setup forming — market reads {tone.lower()} ({why}). Not executable yet."
    else:
        headline = f"Stand aside — market reads {tone.lower()} ({why}). No strategy has a fully checked signal."

    return {"headline": headline, "tone": tone, "alignment": alignment, "decision": d,
            "readiness": _pct(ready.get("score")), "points": points[:6], "watch": watch[:6],
            "note": "Plain-English summary of the models above. Information only — it never creates or changes a signal."}
