"""Daily 8 PM IST recap: direction changes, signals captured, today's actual P&L."""
from html import escape
from core import db, now_utc, iso, parse_iso, ist_day_bounds, IST, APP_NAME
import journal as JN
import engine
import alerts
import messaging


async def build():
    start, end = ist_day_bounds(now_utc())
    s, e = iso(start), iso(end)
    snaps = [x async for x in db.validation_snapshots.find({"capture_time": {"$gte": s, "$lt": e}}).sort("capture_time", 1)]
    sigs = [x async for x in db.signals.find({"captured_at": {"$gte": s, "$lt": e}}).sort("captured_at", 1)]
    fills = [x async for x in db.fills.find({"exit_time": {"$gte": s, "$lt": e}})]
    pn = [JN.fill_pnl(f) for f in fills]
    known = [p["net"] for p in pn if p["net_known"]]
    st = engine.STATE or {}
    return {"day": start.astimezone(IST).strftime("%d %b %Y"), "snaps": snaps, "signals": sigs, "closed": len(fills),
            "net_known": round(sum(known), 2) if known else None, "unknown_costs": len(pn) - len(known),
            "gross": round(sum(p["gross"] or 0 for p in pn), 2),
            "decision": (st.get("decision") or {}).get("decision"), "direction": (st.get("live_direction") or {}).get("direction"),
            "six": (st.get("six_factor") or {}).get("direction")}


def _t(v):
    return parse_iso(v).astimezone(IST).strftime("%H:%M IST")


def render(d):
    pnl = "no closed trades" if not d["closed"] else (f"net {d['net_known']:+.2f} USD" if d["net_known"] is not None else "net unknown") + \
        (f" ({d['unknown_costs']} trade(s) with missing fees/funding)" if d["unknown_costs"] else "") + f"; gross {d['gross']:+.2f} USD on {d['closed']} trade(s)"
    dirs = [f"{_t(x['capture_time'])} {x['model']} → {x['direction']} @ {x['entry']}" for x in d["snaps"]]
    sigs = [f"{_t(x['captured_at'])} {x['strategy'].upper()} {x['direction']} entry {x['entry']} → {x.get('outcome', 'PENDING')}" for x in d["signals"]]
    text = (f"{APP_NAME} — daily recap {d['day']}\n"
            f"Now: decision {d['decision']}, live direction {d['direction']}, six-factor {d['six']}\n"
            f"Direction changes ({len(dirs)}):\n" + ("\n".join(dirs[-20:]) + (f"\n(+{len(dirs) - 20} earlier)" if len(dirs) > 20 else "") if dirs else "none") +
            f"\nSignals captured 12/12 ({len(sigs)}):\n" + ("\n".join(sigs) or "none") + f"\nActual P&L today: {pnl}")
    li = lambda xs: "".join(f"<li>{escape(x)}</li>" for x in xs[-20:]) or "<li>none</li>"
    html = (f"<table role='presentation' width='100%'><tr><td style='padding:24px;font-family:Arial,sans-serif;color:#111'>"
            f"<h2 style='margin:0 0 8px'>Daily recap — {escape(d['day'])}</h2>"
            f"<p>Now: decision <b>{escape(str(d['decision']))}</b>, live direction <b>{escape(str(d['direction']))}</b>, six-factor <b>{escape(str(d['six']))}</b></p>"
            f"<h3>Direction changes ({len(dirs)})</h3><ul>{li(dirs)}</ul><h3>Signals captured, 12/12 ({len(sigs)})</h3><ul>{li(sigs)}</ul>"
            f"<h3>Actual P&amp;L today</h3><p>{escape(pnl)}</p>"
            f"<p style='font-size:12px;color:#888'>Model outcomes are candle observations, not exchange fills. Sent by {escape(APP_NAME)}.</p></td></tr></table>")
    return text, html


async def send(run_id, scheduled=False):
    """Send the recap. Scheduled runs (the 20:00 IST timer or an external cron) only send while
    alert mode is ARMED with the daily-recap channel on; otherwise the recap is saved as a dry run.
    A manual "send now" from the admin is always allowed."""
    import alert_mode
    from channel_config import CFG
    if await db.daily_summaries.find_one({"_id": run_id}):
        return
    d = await build()
    text, html = render(d)
    res = {}
    if scheduled and not await alert_mode.channel_armed("daily_recap"):
        res = {"email": "dry run — not sent (alert mode OFF or daily recap switched off)", "telegram": "dry run — not sent"}
        await db.daily_summaries.insert_one({"_id": run_id, "at": iso(now_utc()), "day": d["day"], "result": res, "text": text, "dry_run": True})
        return
    for ch, fn in (("email", lambda: alerts.send_email(to=CFG["ALERT_EMAIL"], subject=f"{APP_NAME} daily recap — {d['day']}", html=html)),
                   ("telegram", lambda: messaging.send_telegram(text))):
        if ch == "telegram" and not messaging.telegram_configured():
            res[ch] = "not configured"
            continue
        if ch == "email" and not (CFG.get("ALERT_EMAIL") and alerts.email_provider()):
            res[ch] = "not configured"
            continue
        try:
            await fn()
            res[ch] = "sent"
        except Exception as e:
            res[ch] = f"failed: {str(e)[:160]}"
    await db.daily_summaries.insert_one({"_id": run_id, "at": iso(now_utc()), "day": d["day"], "result": res, "text": text})


RECAP_HOUR_IST = 20  # 8 PM IST


async def scheduler_loop():
    """Built-in daily timer (replaces the hosting platform's cron). Runs inside the backend, so it
    keeps working 24/7 on the server — your own PC can be off. Idempotent: one recap per IST day,
    and a missed run (server was down at 20:00) is sent when the server comes back the same evening."""
    import asyncio
    import logging
    log = logging.getLogger("daily_summary")
    while True:
        try:
            now = now_utc().astimezone(IST)
            if now.hour >= RECAP_HOUR_IST:
                await send(f"daily-{now.strftime('%Y-%m-%d')}", scheduled=True)
        except Exception:
            log.exception("daily recap failed")
        await asyncio.sleep(60)
