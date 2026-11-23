"""Fund Manager: one PAPER fund over the three strategies that passed their checks (owner request 23 Nov 2026).

It never sends an order. It follows the paper trades of Elite 4 Edge, Option Picks (pick 5) and Flush Short v3, sizes each one
from the capital the owner allocates to it, and applies the fund's risk rules to decide which trades the fund takes:

  allocation      capital x share = each strategy's capital; size factor = that capital / the capital one "page-size" trade needs
                  (Elite $5,000 for a $10,000 position at 10x with its drawdown reserve; Option Picks its two legs' capital;
                  Flush Short $1,000 margin for a $10,000 position at 10x).
  utilisation     a strategy cannot have more margin in use than its capital; the fund cannot have more than max_use_pct in use.
  strategy stop   a strategy whose closed P&L falls strat_dd_pct of its capital below its own peak is paused.
  fund stop       closed P&L falling fund_dd_pct of the fund below its peak stops every strategy.
  month stop      closed P&L of the calendar month (IST) at -month_loss_pct of the fund: no new trades until the next month.
  monthly target  tracked; with lock_at_target the fund stops opening trades for the rest of the month once it is reached.
Live: a strategy stop and the fund stop stay until an admin presses Resume. In the history table they are assumed to be
released on the 1st of the next month (there is no admin in a backtest). Sizes do not compound: capital is the setting.

Decisions are frozen when a trade is first seen (db.fund_trades), so later setting changes never rewrite the fund's past.
The history table replays the same rules over the strategies' backtest trades; it is a backtest, not a promise.
"""
import asyncio
import calendar
import logging
import statistics
import time
from datetime import date, datetime, timedelta, timezone

from core import db, iso, now_utc, parse_iso

log = logging.getLogger("fundmgr")
NAME = "Fund Manager"
IST = timezone(timedelta(hours=5, minutes=30))
ORDER = ["elite", "optpicks", "flush"]
OPT_KEY = "SSTR"
FLUSH_UNIT = 1000.0                               # margin for one $10,000 Flush Short position at 10x
INFO = {
    "elite": {"name": "Elite 4 Edge", "to": "/elite-4-edge", "what": "BTCUSD futures · one trade at a time · stop 2 x ATR"},
    "optpicks": {"name": "Option Picks · pick 5", "to": "/option-picks", "what": "short ATM straddle 17:00 IST · 30 minutes to expiry · stop 2x each leg"},
    "flush": {"name": "Flush Short v3", "to": "/flush-short", "what": "SHORT breakdowns · only while BTC is down more than 5% over 30 days"},
}
DEFAULTS = {"capital": 10000.0, "alloc": {"elite": 50.0, "optpicks": 40.0, "flush": 10.0}, "target_pct": 10.0, "month_loss_pct": 10.0, "fund_dd_pct": 25.0,
            "strat_dd_pct": {"elite": 80.0, "optpicks": 22.0, "flush": 30.0}, "max_use_pct": 70.0, "lock_at_target": False}
LIMITS = {"capital": (100.0, 10_000_000.0), "target_pct": (0.5, 100.0), "month_loss_pct": (1.0, 100.0), "fund_dd_pct": (1.0, 100.0), "max_use_pct": (5.0, 100.0)}
WHY_DEFAULT = ("Defaults: strategy stops are about 1.5 x each strategy's worst backtest drawdown (Elite $4,000 of $5,000, the limit already used on its page; "
               "Option Picks $880 of $4,000; Flush Short $300 of $1,000, thin evidence). They were not tuned to make the history table look better.")
STATE = {"computed_at": None, "error": None, "data": None, "history": None, "history_at": 0.0, "integrity": None, "integrity_at": 0.0}


def ym(t):
    d = datetime.fromtimestamp(t, IST)
    return f"{d.year}-{d.month:02d}"


def _ist(t, fmt="%d %b %Y %H:%M"):
    return datetime.fromtimestamp(t, IST).strftime(fmt)


def month_range(a, b):
    """'2026-01' .. '2026-03' inclusive."""
    y, m = map(int, a.split("-"))
    out = []
    while f"{y}-{m:02d}" <= b:
        out.append(f"{y}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


# ================================================================ settings
def clean_settings(doc):
    """Stored values -> a complete, valid settings dict. Raises ValueError with a plain reason."""
    s = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for k, (lo, hi) in LIMITS.items():
        if doc.get(k) is not None:
            v = float(doc[k])
            if not (lo <= v <= hi):
                raise ValueError(f"{k} must be between {lo:g} and {hi:g}")
            s[k] = v
    for grp in ("alloc", "strat_dd_pct"):
        for k in ORDER:
            v = (doc.get(grp) or {}).get(k)
            if v is not None:
                v = float(v)
                if not (0 <= v <= 100) or (grp == "strat_dd_pct" and v < 1):
                    raise ValueError(f"{grp} for {INFO[k]['name']} must be between {'1' if grp == 'strat_dd_pct' else '0'} and 100")
                s[grp][k] = v
    if sum(s["alloc"].values()) > 100.0001:
        raise ValueError(f"The shares add up to {sum(s['alloc'].values()):g}%. They cannot be more than 100% of the fund.")
    if doc.get("lock_at_target") is not None:
        s["lock_at_target"] = bool(doc["lock_at_target"])
    return s


async def settings():
    doc = await db.fund_settings.find_one({"_id": "cfg"})
    if not doc:
        doc = {"_id": "cfg", "start": iso(now_utc())}
        await db.fund_settings.insert_one(doc)
        await event("start", "Fund Manager started as a paper fund with the default plan. No order is sent to any exchange.")
    try:
        s = clean_settings(doc)
    except ValueError:
        s = clean_settings({})
    s["start"] = doc.get("start") or iso(now_utc())
    return s


async def save_settings(values, by):
    cur = await db.fund_settings.find_one({"_id": "cfg"}) or {}
    merged = {**{k: cur.get(k) for k in DEFAULTS}, **{k: v for k, v in (values or {}).items() if k in DEFAULTS and v is not None}}
    for grp in ("alloc", "strat_dd_pct"):
        merged[grp] = {**(cur.get(grp) or {}), **((values or {}).get(grp) or {})}
    s = clean_settings(merged)
    await db.fund_settings.update_one({"_id": "cfg"}, {"$set": {**s, "by": by, "at": iso(now_utc())}, "$setOnInsert": {"start": iso(now_utc())}}, upsert=True)
    a = s["alloc"]
    await event("settings", f"Plan saved by {by}: ${s['capital']:,.0f} · Elite {a['elite']:g}% · Option Picks {a['optpicks']:g}% · Flush Short {a['flush']:g}% · "
                            f"target {s['target_pct']:g}% a month · month stop −{s['month_loss_pct']:g}% · fund stop −{s['fund_dd_pct']:g}%")
    return s


async def event(kind, text, level="info", at=None, key=None):
    doc = {"at": at or iso(now_utc()), "kind": kind, "level": level, "text": text}
    if key:
        await db.fund_events.update_one({"_id": key}, {"$setOnInsert": doc}, upsert=True)
    else:
        await db.fund_events.insert_one(doc)


# ================================================================ sizes
def units(sizing=None):
    """What one page-size trade of each strategy needs: capital (with its reserve), margin while open, position size."""
    import elite
    import flush
    import optpicks
    s = sizing or optpicks.STATE.get("sizing") or dict(optpicks.SIZING_DEFAULT)
    oc = optpicks.capital_for(OPT_KEY, s)
    return {"elite": {"capital": elite.PAPER_CAPITAL, "margin": elite.NOTIONAL / elite.LEVERAGE, "position": elite.NOTIONAL},
            "optpicks": {"capital": oc, "margin": oc, "position": s["put_capital"] * s["put_leverage"] + s["call_capital"] * s["call_leverage"]},
            "flush": {"capital": FLUSH_UNIT, "margin": FLUSH_UNIT, "position": flush.NOTIONAL}}


# ================================================================ the fund book (pure)
def replay(trades, cfg, unit, controls=(), rules=True, frozen=None, monthly_resume=False, scale=1.0):
    """Walk the strategies' trades in time order and decide, with the fund's rules, which ones the fund takes.

    trades: [{id, strat, entry_t, exit_t, net, status ('OPEN'|'CLOSED'), label}] where net is the P&L of ONE page-size trade
            (for an open trade: its open P&L). controls: [{t, action: pause|resume|stop_all|resume_all, strat?}].
    frozen: {id: {decision, reason, k}} decisions already made (live book). scale multiplies every size (history what-if).
    """
    cap = float(cfg["capital"])
    scap = {k: cap * cfg["alloc"].get(k, 0.0) / 100.0 for k in ORDER}
    K = {k: (scale * scap[k] / unit[k]["capital"] if unit[k]["capital"] else 0.0) for k in ORDER}
    ev = []
    for tr in trades:
        ev.append((tr["entry_t"], 2, "in", tr))
        if tr.get("status") == "CLOSED" and tr.get("exit_t") is not None and tr.get("net") is not None:
            ev.append((max(tr["exit_t"], tr["entry_t"]), 0, "out", tr))
    for c in controls:
        ev.append((c["t"], 1, "ctl", c))
    ev.sort(key=lambda x: (x[0], x[1]))
    st = {"eq": 0.0, "peak": 0.0, "maxdd": 0.0, "stop_all": None, "mstop": None, "month": None, "mpnl": 0.0, "target_hit": False}
    S = {k: {"pnl": 0.0, "peak": 0.0, "paused": None, "open": {}, "taken": 0, "skipped": 0, "month": 0.0, "maxdd": 0.0} for k in ORDER}
    rows, months, events, curve = {}, {}, [], []

    def mrow(m):
        return months.setdefault(m, {"month": m, "net": 0.0, "by": {k: 0.0 for k in ORDER}, "taken": 0, "skipped": 0, "stops": []})

    def note(t, kind, text, level="info", strat=None):
        events.append({"t": t, "kind": kind, "text": text, "level": level, "strat": strat, "key": f"{kind}:{strat or 'fund'}:{t}"})
        if kind in ("pause", "fund_stop", "month_stop"):
            mrow(ym(t))["stops"].append(text)

    def roll(t):
        m = ym(t)
        if m == st["month"]:
            return
        first = st["month"] is None
        st.update(month=m, mpnl=0.0, mstop=None, target_hit=False)
        for k in ORDER:
            S[k]["month"] = 0.0
        if monthly_resume and not first:
            if st["stop_all"]:
                st.update(stop_all=None, peak=st["eq"])
            for k in ORDER:
                if S[k]["paused"]:
                    S[k].update(paused=None, peak=S[k]["pnl"])

    for t, _, kind, x in ev:
        roll(t)
        if kind == "ctl":
            a, k = x.get("action"), x.get("strat")
            if a == "pause" and k in S:
                S[k]["paused"] = "paused by an admin"
            elif a == "resume" and k in S:
                S[k].update(paused=None, peak=S[k]["pnl"])
            elif a == "stop_all":
                st["stop_all"] = "stopped by an admin"
            elif a == "resume_all":
                st.update(stop_all=None, mstop=None, peak=st["eq"])
            continue
        k = x["strat"]
        s = S[k]
        if kind == "in":
            fr = (frozen or {}).get(x["id"])
            kk, why = K[k], None
            if fr:
                kk, why = fr.get("k", kk), (fr.get("reason") if fr.get("decision") == "SKIPPED" else None)
            elif kk <= 0:
                why = "no capital allocated to this strategy"
            elif rules:
                need = unit[k]["margin"] * kk
                used = sum(sum(S[j]["open"].values()) for j in ORDER)
                if st["stop_all"]:
                    why = f"fund stopped ({st['stop_all']})"
                elif s["paused"]:
                    why = f"strategy paused ({s['paused']})"
                elif st["mstop"]:
                    why = "monthly loss limit reached: no new trades until next month"
                elif cfg.get("lock_at_target") and st["target_hit"]:
                    why = "monthly target reached and locked: no new trades until next month"
                elif sum(s["open"].values()) + need > scap[k] * max(scale, 1.0) + 0.01:
                    why = "this strategy's capital is fully in use"
                elif used + need > cap * max(scale, 1.0) * cfg["max_use_pct"] / 100.0 + 0.01:
                    why = f"fund utilisation cap ({cfg['max_use_pct']:g}%) would be passed"
            take = why is None
            rows[x["id"]] = {"id": x["id"], "strat": k, "label": x.get("label"), "entry_t": t, "k": round(kk, 4), "margin": round(unit[k]["margin"] * kk, 2) if take else 0.0,
                             "decision": "TAKEN" if take else "SKIPPED", "reason": why, "status": x.get("status"), "exit_t": x.get("exit_t"),
                             "unit_net": x.get("net"), "net": (kk * x["net"] if take and x.get("net") is not None else None)}
            mrow(ym(t))["taken" if take else "skipped"] += 1
            s["taken" if take else "skipped"] += 1
            if take:
                s["open"][x["id"]] = unit[k]["margin"] * kk
            continue
        r = rows.get(x["id"])                                    # "out"
        if not r or r["decision"] != "TAKEN":
            continue
        net = r["k"] * x["net"]
        s["open"].pop(x["id"], None)
        st["eq"] += net; st["mpnl"] += net; s["pnl"] += net; s["month"] += net
        st["peak"] = max(st["peak"], st["eq"]); s["peak"] = max(s["peak"], s["pnl"])
        st["maxdd"] = min(st["maxdd"], st["eq"] - st["peak"]); s["maxdd"] = min(s["maxdd"], s["pnl"] - s["peak"])
        m = mrow(ym(t)); m["net"] += net; m["by"][k] += net
        curve.append((t, round(st["eq"], 2)))
        if rules:
            if not s["paused"] and scap[k] > 0 and s["pnl"] - s["peak"] <= -cfg["strat_dd_pct"][k] / 100.0 * scap[k]:
                s["paused"] = f"down ${s['peak'] - s['pnl']:,.0f} from its peak, limit ${cfg['strat_dd_pct'][k] / 100.0 * scap[k]:,.0f}"
                note(t, "pause", f"{INFO[k]['name']} paused: {s['paused']}.", "warn", k)
            if not st["stop_all"] and st["eq"] - st["peak"] <= -cfg["fund_dd_pct"] / 100.0 * cap:
                st["stop_all"] = f"fund down ${st['peak'] - st['eq']:,.0f} from its peak, limit ${cfg['fund_dd_pct'] / 100.0 * cap:,.0f}"
                note(t, "fund_stop", f"Fund stopped: {st['stop_all']}.", "error")
            if not st["mstop"] and st["mpnl"] <= -cfg["month_loss_pct"] / 100.0 * cap:
                st["mstop"] = True
                note(t, "month_stop", f"Month stop: {ym(t)} is down ${-st['mpnl']:,.0f}, the monthly loss limit is ${cfg['month_loss_pct'] / 100.0 * cap:,.0f}. No new trades until next month.", "warn")
        if not st["target_hit"] and st["mpnl"] >= cfg["target_pct"] / 100.0 * cap:
            st["target_hit"] = True
            note(t, "target", f"Monthly target reached: {ym(t)} is up ${st['mpnl']:,.0f} ({st['mpnl'] / cap:.1%}).")
    out_rows = sorted(rows.values(), key=lambda r: r["entry_t"])
    open_net = sum(r["net"] or 0.0 for r in out_rows if r["decision"] == "TAKEN" and r["status"] == "OPEN")
    return {"rows": out_rows, "events": events, "months": months, "curve": curve, "capital": cap, "scap": scap, "K": K,
            "state": {"closed": round(st["eq"], 2), "open": round(open_net, 2), "peak": round(st["peak"], 2), "dd": round(st["eq"] - st["peak"], 2), "maxdd": round(st["maxdd"], 2),
                      "stop_all": st["stop_all"], "month_stop": bool(st["mstop"]), "month": st["month"], "month_net": round(st["mpnl"], 2), "target_hit": st["target_hit"],
                      "in_use": round(sum(sum(S[k]["open"].values()) for k in ORDER), 2),
                      "strategies": {k: {"pnl": round(S[k]["pnl"], 2), "peak": round(S[k]["peak"], 2), "dd": round(S[k]["pnl"] - S[k]["peak"], 2), "maxdd": round(S[k]["maxdd"], 2),
                                         "paused": S[k]["paused"], "in_use": round(sum(S[k]["open"].values()), 2), "open_trades": len(S[k]["open"]),
                                         "taken": S[k]["taken"], "skipped": S[k]["skipped"], "month_net": round(S[k]["month"], 2)} for k in ORDER}}}


def month_stats(res, first, last, target_pct):
    """Monthly table (every month from first to last, in % of the fund) and its summary."""
    cap = res["capital"]
    ms = []
    for m in month_range(first, last):
        x = res["months"].get(m) or {"month": m, "net": 0.0, "by": {k: 0.0 for k in ORDER}, "taken": 0, "skipped": 0, "stops": []}
        ms.append({"month": m, "net": round(x["net"], 2), "pct": round(x["net"] / cap * 100, 2), "by": {k: round(v, 2) for k, v in x["by"].items()},
                   "taken": x["taken"], "skipped": x["skipped"], "stops": x["stops"]})
    p = [m["pct"] for m in ms]
    if not p:
        return {"months": [], "summary": None}
    return {"months": ms, "summary": {
        "months": len(p), "from": first, "to": last, "net": round(sum(m["net"] for m in ms), 2), "avg_pct": round(statistics.mean(p), 2), "median_pct": round(statistics.median(p), 2),
        "hit": sum(1 for v in p if v >= target_pct), "negative": sum(1 for v in p if v < 0), "worst_pct": min(p), "worst_month": ms[p.index(min(p))]["month"],
        "best_pct": max(p), "max_dd": res["state"]["maxdd"], "max_dd_pct": round(res["state"]["maxdd"] / cap * 100, 2),
        "last12_avg_pct": round(statistics.mean(p[-12:]), 2), "last12_hit": sum(1 for v in p[-12:] if v >= target_pct), "last12_n": len(p[-12:]),
        "taken": sum(m["taken"] for m in ms), "skipped": sum(m["skipped"] for m in ms),
        "stops": sum(len(m["stops"]) for m in ms)}}


# ================================================================ history (backtest of this plan)
def history_trades(stress=False):
    """Backtest trades of the three strategies as page-size trades. Option Picks 'stress' = 5% worse fills and double fees."""
    import elite
    import flush
    import optpicks
    out = []
    for r in ((elite.STATE.get("data") or {}).get("A") or {}).get("trades") or []:
        if r.get("status") == "CLOSED":
            out.append({"id": f"elite:{r['strategy']}-{r['signal_t']}", "strat": "elite", "entry_t": r["entry_t"], "exit_t": r["exit_t"], "net": r["net"], "status": "CLOSED",
                        "label": f"{r['wb']} {r['side']}"})
    for r in (optpicks.STATE.get("rows") or {}).get(OPT_KEY) or []:
        v = r.get("net_v2") if stress else r.get("net")
        if v is None:
            continue
        d = date.fromisoformat(r["day"])
        out.append({"id": f"optpicks:{r['day']}", "strat": "optpicks", "entry_t": optpicks.entry_t(OPT_KEY, d), "exit_t": optpicks.settle_t(d), "net": v, "status": "CLOSED",
                    "label": "Short straddle"})
    for r in (flush.STATE.get("data") or {}).get("rows") or []:
        if r.get("status") == "CLOSED" and r.get("fill_t") and r.get("exit_t"):
            out.append({"id": f"flush:{r['signal_id']}", "strat": "flush", "entry_t": r["fill_t"], "exit_t": r["exit_t"], "net": r["net"], "status": "CLOSED", "label": "Flush SHORT"})
    return sorted(out, key=lambda x: x["entry_t"])


def history(cfg, unit, trades=None, stress_trades=None, now_t=None):
    """The plan replayed over the backtests, from the first month all three data sets could trade (Option Picks starts Jan 2024)."""
    tr = history_trades() if trades is None else trades
    st = history_trades(stress=True) if stress_trades is None else stress_trades
    opt = [x["entry_t"] for x in tr if x["strat"] == "optpicks"]
    if not tr:
        return None
    first = ym(min(opt)) if opt else ym(tr[0]["entry_t"])
    y, m = map(int, first.split("-"))
    t0 = int(datetime(y, m, 1, tzinfo=IST).timestamp())
    last = ym(now_t or time.time())
    keep = lambda rows: [x for x in rows if x["entry_t"] >= t0]
    tgt = cfg["target_pct"]
    views = {}
    for key, rows, rules in (("rules", keep(tr), True), ("plain", keep(tr), False), ("stress", keep(st), True)):
        res = replay(rows, cfg, unit, rules=rules, monthly_resume=True)
        views[key] = {**month_stats(res, first, last, tgt), "curve": _thin(res["curve"]),
                      "by": {k: {"net": res["state"]["strategies"][k]["pnl"], "max_dd": res["state"]["strategies"][k]["maxdd"], "taken": res["state"]["strategies"][k]["taken"],
                                 "skipped": res["state"]["strategies"][k]["skipped"]} for k in ORDER}}
    scale = []
    for f in (0.5, 1.0, 1.25, 1.5, 2.0):
        s = month_stats(replay(keep(tr), cfg, unit, rules=True, monthly_resume=True, scale=f), first, last, tgt)["summary"]
        if s:
            scale.append({"size": f, "avg_pct": s["avg_pct"], "hit": s["hit"], "months": s["months"], "negative": s["negative"], "worst_pct": s["worst_pct"],
                      "max_dd_pct": s["max_dd_pct"], "stops": s["stops"]})
    fl = [x for x in tr if x["strat"] == "flush"]
    return {"from": first, "to": last, "views": views, "scale": scale, "target_pct": tgt, "stop_pct": cfg["month_loss_pct"],
            "counts": {k: sum(1 for x in keep(tr) if x["strat"] == k) for k in ORDER},
            "flush_note": (f"Flush Short is in this table only from {ym(fl[0]['entry_t'])} (the site has {len(fl)} of its trades). Its 26-month replay averaged about "
                           "1.7 trades and about $42 a month on a $10,000 position, only in falling markets.") if fl else
                          "Flush Short has no trades in this table yet. Its 26-month replay averaged about 1.7 trades and about $42 a month on a $10,000 position."}


def _thin(curve, n=400):
    if len(curve) <= n:
        return [{"t": iso(datetime.fromtimestamp(t, timezone.utc)), "v": v} for t, v in curve]
    step = len(curve) / n
    pts = [curve[int(i * step)] for i in range(n)] + [curve[-1]]
    return [{"t": iso(datetime.fromtimestamp(t, timezone.utc)), "v": v} for t, v in pts]


# ================================================================ live sources
async def live_trades(start_t):
    """Paper (or real) trades of the three strategies since the fund started, as page-size trades."""
    import elite
    import flush
    import optpicks
    out = []
    async for t in db.elite_trades.find({"status": {"$in": ["OPEN", "CLOSED"]}}):
        if (t.get("entry_t") or 0) < start_t:
            continue
        opened = t["status"] == "OPEN"
        out.append({"id": f"elite:{t['_id']}", "strat": "elite", "entry_t": t["entry_t"], "exit_t": t.get("exit_t"), "status": t["status"],
                    "net": elite.Engine.unrealized(t, elite.ENGINE.last_price) if opened else t.get("net"),
                    "label": f"{t.get('wb')} {t.get('side')}" + ("" if t.get("mode") == "PAPER" else f" · {t.get('mode')}")})
    sizing = optpicks.STATE.get("sizing") or dict(optpicks.SIZING_DEFAULT)
    async for t in db.opt_live.find({"key": OPT_KEY, "status": {"$in": ["OPEN", "CLOSED"]}}):
        if not (t.get("legs") and t.get("price") and t.get("entry_ist")):
            continue
        et = int(datetime.fromisoformat(t["entry_ist"]).replace(tzinfo=IST).timestamp())
        if et < start_t:
            continue
        p = optpicks.price(t, sizing)
        d = date.fromisoformat(t["day"])
        closed = t["status"] == "CLOSED" and p.get("net") is not None
        out.append({"id": f"optpicks:{t['_id']}", "strat": "optpicks", "entry_t": et, "exit_t": optpicks.settle_t(d) if closed else None,
                    "status": "CLOSED" if closed else "OPEN", "net": p.get("net") if closed else 0.0, "label": f"Short straddle {t['day']}"})
    for r in (flush.STATE.get("data") or {}).get("rows") or []:
        if r.get("status") in ("OPEN", "CLOSED") and (r.get("fill_t") or 0) >= start_t:
            out.append({"id": f"flush:{r['signal_id']}", "strat": "flush", "entry_t": r["fill_t"], "exit_t": r.get("exit_t"), "status": r["status"], "net": r.get("net"),
                        "label": "Flush SHORT"})
    return sorted(out, key=lambda x: x["entry_t"])


# ================================================================ signal checks
def _chk(name, status, detail, more=None):
    return {"name": name, "status": status, "detail": detail, "more": more}


def candle_gaps(b5, step=300):
    """(missing candles, longest gap in candles, where it starts)."""
    missing, worst, at = 0, 0, None
    for a, b in zip(b5, b5[1:]):
        g = (b["t"] - a["t"]) // step - 1
        if g > 0:
            missing += g
            if g > worst:
                worst, at = g, a["t"] + step
    return missing, worst, at


async def check_elite(now_t):
    import elite
    out = []
    b5 = elite.STATE.get("bars5") or []
    if not b5:
        return [_chk("5-minute candles", "WAIT", "The candle store is still loading (a few minutes after a restart).")]
    missing, worst, at = candle_gaps(b5)
    out.append(_chk("5-minute candles, no gaps", "OK" if missing == 0 else "FAIL" if worst >= 12 else "WARN",
                    f"{len(b5):,} candles since {_ist(b5[0]['t'], '%d %b %Y')}, {missing} missing" + (f" (longest gap {worst * 5} min from {_ist(at)} IST)" if missing else ""),
                    "Signals are built from Delta BTCUSD 5-minute candles. A 1h or 4h candle is used only when at least 90% of its 5-minute candles are present."))
    age = now_t - (b5[-1]["t"] + 300)
    out.append(_chk("Latest candle on time", "OK" if age <= 720 else "FAIL", f"last candle closed {_ist(b5[-1]['t'] + 300, '%d %b %H:%M')} IST, {int(age // 60)} min ago"))
    st = await db.elite_state.find_one({"_id": "state"}) or {}
    fr = elite.STATE.get("frames")
    if fr and st.get("live_start"):
        last_tc = max(fr[tf][0][-1]["tc"] for tf in fr if fr[tf][0])
        seen = st.get("last_tc") or 0
        out.append(_chk("Every candle close scanned", "OK" if seen >= last_tc else "WARN",
                        f"scanned up to {_ist(seen, '%d %b %H:%M')} IST" + ("" if seen >= last_tc else f"; newest close {_ist(last_tc, '%d %b %H:%M')} IST is waiting")))
        live_t = parse_iso(st["live_start"]).timestamp()
        cands = elite.candidates(fr, live_t - 86400, seen)
        want = {f"{g['key']}-{g['t']}": g for g in cands if g["t"] > live_t}
        allc = {f"{g['key']}-{g['t']}" for g in cands}
        logged = {d["_id"]: d async for d in db.elite_signals.find({}, {"status": 1, "reason": 1, "ist": 1, "wb": 1, "t": 1})}
        miss = sorted(set(want) - set(logged))
        extra = sorted(i for i, d in logged.items() if i not in allc and (d.get("t") or 0) > live_t - 86400 + 3600)
        took = sum(1 for d in logged.values() if d.get("status") == "TAKEN")
        out.append(_chk("No signal missed", "OK" if not miss and not extra else "FAIL",
                        f"{len(want)} signals on the candles since the live start, {len(logged)} recorded ({took} taken)" +
                        (f"; NOT recorded: {', '.join(elite.STRATS[want[i]['key']]['wb'] + ' ' + _ist(want[i]['t'], '%d %b %H:%M') for i in miss[:4])}" if miss else "") +
                        (f"; recorded but no longer on the candles: {len(extra)}" if extra else ""),
                        "The signal rules are run again over the stored candles and compared with what the live engine recorded at the time."))
        bad = [d for d in logged.values() if d.get("status") == "SKIPPED" and any(w in (d.get("reason") or "") for w in ("15 minutes", "no live price", "not placed", "did not fill"))]
        out.append(_chk("No signal lost to a delay or an error", "OK" if not bad else "WARN",
                        "none" if not bad else f"{len(bad)} skipped for a technical reason, last: {bad[-1].get('wb')} {bad[-1].get('ist')} ({bad[-1].get('reason')})"))
    else:
        out.append(_chk("Every candle close scanned", "WAIT", "The live engine has not finished its first scan yet."))
    out.append(_chk("Engine", "OK" if not elite.STATE.get("error") else "FAIL", elite.STATE.get("error") or "running, no error"))
    return out


async def check_flush(now_t):
    import engine
    import flush
    d = flush.STATE.get("data")
    if not d:
        return [_chk("Signals", "WAIT", "The journal is still loading (a few minutes after a restart).")]
    rows = d.get("rows") or []
    out = [_chk("Scalp signals read", "OK" if rows else "WAIT", f"{len(rows)} signals checked" + (f", {rows[-1]['time_ist']} to {rows[0]['time_ist']} IST" if rows else ""))]
    nodata = [r for r in rows if "not available" in (r.get("reason") or "")]
    out.append(_chk("Market data for every signal", "OK" if not nodata else "WARN",
                    "RSI, ATR, trends and the 30-day change are present for every signal" if not nodata else
                    f"{len(nodata)} signals without data, last {nodata[0]['time_ist']} IST ({nodata[0]['reason']})"))
    tr = [r for r in rows if r.get("status") in ("OPEN", "CLOSED")]
    wrong = [r for r in tr if abs(r["sl"] - (r["entry"] + r["d"])) > 0.01 or abs(r["tp"] - (r["entry"] - flush.RR * r["d"])) > 0.01]
    out.append(_chk("Stop and target follow the rule", "OK" if not wrong else "FAIL",
                    f"{len(tr)} trades: stop = entry + 1R, target = entry − {flush.RR:g}R" + (f"; {len(wrong)} do not match" if wrong else "")))
    mk = d.get("market_now") or {}
    out.append(_chk("Market type now", "OK" if mk else "WARN",
                    f"{mk.get('type')} (BTC {mk.get('chg30', 0):+.1%} over 30 days): " + ("signals are being taken" if mk.get("active") else "standing aside, it trades only in a Down market")
                    if mk else "the 30-day change could not be read"))
    try:
        age = now_t - parse_iso(engine.STATE.get("computed_at")).timestamp()
    except Exception:
        age = None
    out.append(_chk("Signal engine running", "OK" if age is not None and age <= 60 else "FAIL", "no reading" if age is None else f"last calculation {int(age)} s ago"))
    out.append(_chk("Journal", "OK" if not d.get("error") and not flush.STATE.get("error") else "FAIL", flush.STATE.get("error") or "running, no error"))
    return out


async def check_optpicks(now_t):
    import optpicks
    out = []
    docs = [d async for d in db.opt_live.find({"key": OPT_KEY})]
    by_day = {d["day"]: d for d in docs}
    if not docs:
        out.append(_chk("Daily entry", "WAIT", "No live day yet."))
    else:
        d0, today = date.fromisoformat(min(by_day)), datetime.fromtimestamp(now_t, IST).date()
        due, d = [], d0
        while d <= today:
            if now_t > optpicks.live_entry_t(OPT_KEY, d) + 240:
                due.append(d.isoformat())
            d += timedelta(days=1)
        norec = [x for x in due if x not in by_day]
        missed = [x for x in due if (by_day.get(x) or {}).get("status") == "MISSED"]
        took = [x for x in due if (by_day.get(x) or {}).get("status") in ("OPEN", "CLOSED")]
        rule = [x for x in due if (by_day.get(x) or {}).get("status") == "SKIPPED"]
        out.append(_chk("Daily entry at 17:01 IST", "OK" if not norec and not missed else "FAIL",
                        f"{len(due)} days since {d0.strftime('%d %b')}: {len(took)} traded, {len(rule)} skipped by its rules" +
                        (f", {len(missed)} MISSED ({', '.join(missed[-3:])}: {by_day[missed[-1]].get('reason')})" if missed else "") +
                        (f", {len(norec)} with no record ({', '.join(norec[-3:])})" if norec else ""),
                        "Its rules skip a day when the put is under $20 or BTC is up more than 2% since 17:31 IST the day before."))
        chk = [d for d in docs if d.get("fill_checked") and d.get("legs")]
        legs = [l for d in chk for l in d["legs"]]
        unreal = sum(1 for l in legs if not l.get("real"))
        out.append(_chk("Paper fills against real trades", "OK" if legs and not unreal else "WAIT" if not legs else "WARN",
                        "no checked trade yet" if not legs else f"{len(legs) - unreal} of {len(legs)} legs had a real Delta trade at our price or better within 2 minutes"))
        sizing = optpicks.STATE.get("sizing") or dict(optpicks.SIZING_DEFAULT)
        bt = {r["day"]: r for r in (optpicks.STATE.get("rows") or {}).get(OPT_KEY) or []}
        skips = optpicks.STATE.get("skips") or {}
        diffs, clash = [], []
        for d in docs:
            b, live_took = bt.get(d["day"]), d.get("status") == "CLOSED" and d.get("legs")
            if live_took and b and b.get("net") is not None:
                ln = optpicks.price(d, sizing).get("net")
                if ln is not None:
                    diffs.append((abs(ln - b["net"]), d["day"], ln, b["net"]))
            elif (live_took and (OPT_KEY, d["day"]) in skips) or (d.get("status") == "SKIPPED" and b):
                clash.append(d["day"])
        big = [x for x in diffs if x[0] > max(15.0, 0.25 * abs(x[3]))]
        if clash:
            out.append(_chk("Paper result matches the backtest", "WARN", f"paper and backtest disagree on taking the trade on {', '.join(clash[-3:])}"))
        elif not diffs:
            out.append(_chk("Paper result matches the backtest", "WAIT", "no closed day to compare yet"))
        else:
            w = max(diffs)
            out.append(_chk("Paper result matches the backtest", "WARN" if big else "OK",
                            f"{len(diffs)} days compared, largest difference ${w[0]:,.2f} on {w[1]} (paper {w[2]:+,.2f}, backtest {w[3]:+,.2f})",
                            "The paper trade opens at the live price at 17:01 IST; the backtest uses the candle of that minute, so small differences are normal."))
    last_bt = None
    async for x in db.opt_backtest.find({}, {"_id": 1}).sort("_id", -1).limit(1):
        last_bt = x["_id"]
    last_bt = max(last_bt or "", optpicks.HISTORY_TO.isoformat())
    today = datetime.fromtimestamp(now_t, IST).date()
    want = today if now_t >= optpicks.settle_t(today) + 2400 else today - timedelta(days=1)
    out.append(_chk("Backtest up to date", "OK" if last_bt >= want.isoformat() else "WARN", f"real Delta prices checked up to the {last_bt} expiry"))
    out.append(_chk("Engine", "OK" if not optpicks.STATE.get("error") else "FAIL", optpicks.STATE.get("error") or "running, no error"))
    return out


async def check_server(now_t):
    out = []
    try:
        ts = [r["at_dt"] async for r in db.server_stats.find({}, {"at_dt": 1}).sort("at_dt", 1)]
    except Exception:
        ts = []
    ts = [x.replace(tzinfo=timezone.utc).timestamp() if x.tzinfo is None else x.timestamp() for x in ts]
    if len(ts) >= 2:
        gaps = [(a, b - a) for a, b in zip(ts, ts[1:]) if b - a > 660]
        out.append(_chk("Server running without a break", "OK" if not gaps else "WARN",
                        f"heartbeat every 5 minutes since {_ist(ts[0], '%d %b %H:%M')} IST, " + ("no break" if not gaps else
                        f"{len(gaps)} breaks, longest {max(g for _, g in gaps) / 60:.0f} min (from {_ist(max(gaps, key=lambda x: x[1])[0], '%d %b %H:%M')} IST)"),
                        "An update of the site restarts the server for about a minute. A signal whose candle closes during a longer break can be late; Elite and Option Picks record that as skipped or missed."))
    else:
        out.append(_chk("Server running without a break", "WAIT", "The heartbeat history is still short."))
    try:
        from sources import REGISTRY, WS
        ok = WS.get("status") == "connected"
        c = REGISTRY.get("delta_candles")
        cs = c.public()["status"] if c else None
        out.append(_chk("Delta price feed", "OK" if ok and cs in (None, "ok") else "FAIL", f"websocket {WS.get('status')}" + (f", candles {cs}" if cs else "")))
    except Exception as e:
        out.append(_chk("Delta price feed", "WARN", f"could not be read ({str(e)[:80]})"))
    return out


async def integrity():
    now_t = time.time()
    res = {}
    for key, fn in (("elite", check_elite), ("optpicks", check_optpicks), ("flush", check_flush), ("server", check_server)):
        try:
            res[key] = await fn(now_t)
        except Exception as e:
            log.exception("integrity %s", key)
            res[key] = [_chk("Check", "FAIL", f"the check itself failed: {str(e)[:160]}")]
    allc = [c for v in res.values() for c in v]
    res["summary"] = {"ok": sum(c["status"] == "OK" for c in allc), "total": len(allc), "warn": sum(c["status"] == "WARN" for c in allc),
                      "fail": sum(c["status"] == "FAIL" for c in allc), "wait": sum(c["status"] == "WAIT" for c in allc), "at": iso(now_utc())}
    return res


# ================================================================ decisions
def decisions(cfg, unit, res, integ, flush_market, opt_next, hist):
    st, cap = res["state"], cfg["capital"]
    out = []
    tgt, lim = cfg["target_pct"] / 100 * cap, cfg["month_loss_pct"] / 100 * cap
    if st["stop_all"]:
        out.append({"key": "fund", "status": "STOPPED", "text": f"Fund stopped: {st['stop_all']}. No strategy opens a trade until an admin presses Resume all."})
    elif st["month_stop"]:
        out.append({"key": "fund", "status": "STOPPED", "text": f"Month stop: this month is down ${-st['month_net']:,.0f} (limit ${lim:,.0f}). New trades start again next month."})
    elif st["target_hit"]:
        out.append({"key": "fund", "status": "TARGET", "text": f"Monthly target reached: +${st['month_net']:,.0f} of ${tgt:,.0f}. " +
                    ("Locked: no new trades until next month." if cfg["lock_at_target"] else "Trading continues (lock at target is off).")})
    else:
        out.append({"key": "fund", "status": "TRADE", "text": f"Fund active. This month {'+' if st['month_net'] >= 0 else '−'}${abs(st['month_net']):,.0f} of the ${tgt:,.0f} target; "
                    f"room to the month stop ${lim + st['month_net']:,.0f}."})
    for k in ORDER:
        s, sc = st["strategies"][k], res["scap"][k]
        nm = INFO[k]["name"]
        if sc <= 0:
            out.append({"key": k, "status": "OFF", "text": f"{nm}: no capital allocated."}); continue
        size = f"{res['K'][k]:.2f}× its page size (position ${unit[k]['position'] * res['K'][k]:,.0f})"
        room = cfg["strat_dd_pct"][k] / 100 * sc + s["dd"]
        if s["paused"]:
            out.append({"key": k, "status": "PAUSED", "text": f"{nm}: paused, {s['paused']}. Press Resume to let it trade again."})
        elif k == "flush" and flush_market and not flush_market.get("active"):
            out.append({"key": k, "status": "STAND ASIDE", "text": f"{nm}: standing aside. BTC is {flush_market.get('chg30', 0):+.1%} over 30 days ({flush_market.get('type')} market); it trades only below −5%."})
        elif s["open_trades"]:
            out.append({"key": k, "status": "IN TRADE", "text": f"{nm}: in a trade at {size}, ${s['in_use']:,.0f} margin in use. Room to its stop ${room:,.0f}."})
        else:
            extra = f" Next entry {opt_next}." if k == "optpicks" and opt_next else ""
            out.append({"key": k, "status": "TRADE", "text": f"{nm}: waiting for its next signal at {size}. Room to its stop ${room:,.0f}.{extra}"})
    if integ:
        sm = integ.get("summary") or {}
        bad = [f"{INFO.get(g, {'name': 'Server'})['name']}: {c['name']}" for g in ("elite", "optpicks", "flush", "server") for c in integ.get(g, []) if c["status"] in ("FAIL", "WARN")]
        out.append({"key": "signals", "status": "OK" if not bad else "CHECK", "text": f"Signal check: {sm.get('ok')} of {sm.get('total')} checks pass." +
                    (f" Look at: {'; '.join(bad[:4])}." if bad else "")})
    h = ((hist or {}).get("views") or {}).get("rules", {}).get("summary")
    if h:
        out.append({"key": "target", "status": "INFO", "text": f"Target check: in the backtest this plan reached {cfg['target_pct']:g}% in {h['hit']} of {h['months']} months "
                    f"(average {h['avg_pct']:+.1f}%, worst {h['worst_pct']:+.1f}%). A target cannot be forced; the fund never raises size to chase it."})
    out.append({"key": "real", "status": "PAPER", "text": "Paper fund: no order is sent. Real money is possible only through Elite 4 Edge's own switch (off unless you turn it on); "
                "Option Picks and Flush Short have no real-order code."})
    return out


# ================================================================ sync and dashboard
async def sync():
    import flush
    import optpicks
    cfg = await settings()
    unit = units()
    start_t = parse_iso(cfg["start"]).timestamp()
    src = await live_trades(start_t)
    led = {d["_id"]: d async for d in db.fund_trades.find({})}
    ctl = [{"t": c["t"], "action": c["action"], "strat": c.get("strat")} async for c in db.fund_controls.find({"t": {"$gte": start_t}}).sort("t", 1)]
    res = replay(src, cfg, unit, controls=ctl, rules=True, frozen=led)
    for r in res["rows"]:
        if r["id"] in led:
            continue
        doc = {"_id": r["id"], "strat": r["strat"], "label": r["label"], "entry_t": r["entry_t"], "decision": r["decision"], "reason": r["reason"], "k": r["k"],
               "margin": r["margin"], "decided_at": iso(now_utc())}
        try:
            await db.fund_trades.insert_one(doc)
        except Exception:
            continue
        nm = INFO[r["strat"]]["name"]
        if r["decision"] == "TAKEN":
            await event("taken", f"{nm} · {r['label']}: taken at {r['k']:.2f}× its page size, ${r['margin']:,.0f} margin.", key=f"taken:{r['id']}")
        else:
            await event("skipped", f"{nm} · {r['label']}: not taken by the fund ({r['reason']}).", "warn", key=f"skipped:{r['id']}")
    for e in res["events"]:
        await event(e["kind"], e["text"], e["level"], at=iso(datetime.fromtimestamp(e["t"], timezone.utc)), key=e["key"])
    for r in res["rows"]:
        if r["status"] == "CLOSED" and r["decision"] == "TAKEN" and r.get("net") is not None and (r.get("exit_t") or 0) > time.time() - 2 * 86400:
            await event("closed", f"{INFO[r['strat']]['name']} · {r['label']}: closed {'+' if r['net'] >= 0 else '−'}${abs(r['net']):,.2f} for the fund.",
                        at=iso(datetime.fromtimestamp(r["exit_t"], timezone.utc)) if r.get("exit_t") else None, key=f"closed:{r['id']}")
    now = time.time()
    if STATE["history"] is None or now - STATE["history_at"] > 600 or STATE.get("history_cfg") != _cfg_key(cfg, unit):
        try:
            import elite
            ready = bool(elite.STATE.get("data")) and bool((optpicks.STATE.get("rows") or {}).get(OPT_KEY))      # both backtests loaded (a few minutes after a restart)
            STATE.update(history=history(cfg, unit) if ready else None, history_at=now if ready else 0.0, history_cfg=_cfg_key(cfg, unit))
        except Exception as e:
            log.exception("fund history")
            STATE["error"] = f"history: {str(e)[:160]}"
    if STATE["integrity"] is None or now - STATE["integrity_at"] > 240:
        STATE.update(integrity=await integrity(), integrity_at=now)
    st, cap = res["state"], cfg["capital"]
    today = datetime.fromtimestamp(now, IST)
    dim = calendar.monthrange(today.year, today.month)[1]
    mnet = st["month_net"] if st["month"] == ym(now) else 0.0
    tgt = cfg["target_pct"] / 100 * cap
    fmk = (flush.STATE.get("data") or {}).get("market_now")
    nxt = None
    for d in (today.date(), today.date() + timedelta(days=1), today.date() + timedelta(days=2)):
        if optpicks.live_entry_t(OPT_KEY, d) > now:
            nxt = datetime.fromtimestamp(optpicks.live_entry_t(OPT_KEY, d), IST).strftime("%d %b %H:%M IST"); break
    stv = dict(st, month=ym(now), month_net=mnet, month_stop=st["month_stop"] and st["month"] == ym(now), target_hit=st["target_hit"] and st["month"] == ym(now))
    view = dict(res, state=stv)
    strategies = []
    for k in ORDER:
        s, sc = st["strategies"][k], res["scap"][k]
        open_net = sum(r["net"] or 0.0 for r in res["rows"] if r["strat"] == k and r["decision"] == "TAKEN" and r["status"] == "OPEN")
        strategies.append({"key": k, **INFO[k], "alloc_pct": cfg["alloc"][k], "capital": round(sc, 2), "k": round(res["K"][k], 4), "position": round(unit[k]["position"] * res["K"][k], 2),
                           "margin_per_trade": round(unit[k]["margin"] * res["K"][k], 2), "unit_capital": unit[k]["capital"], "in_use": s["in_use"], "open_trades": s["open_trades"],
                           "open_net": round(open_net, 2), "month_net": s["month_net"] if st["month"] == ym(now) else 0.0, "net": s["pnl"], "dd": s["dd"],
                           "dd_limit": round(cfg["strat_dd_pct"][k] / 100 * sc, 2), "dd_limit_pct": cfg["strat_dd_pct"][k], "paused": s["paused"], "taken": s["taken"], "skipped": s["skipped"]})
    data = {"name": NAME, "paper_only": True, "computed_at": iso(now_utc()), "settings": cfg, "defaults": DEFAULTS, "why_defaults": WHY_DEFAULT, "strategies": strategies,
            "fund": {"capital": cap, "value": round(cap + st["closed"] + st["open"], 2), "closed": st["closed"], "open": st["open"], "peak": st["peak"],
                     "dd": round(min(0.0, st["closed"] + st["open"] - st["peak"]), 2), "dd_limit": round(cfg["fund_dd_pct"] / 100 * cap, 2), "in_use": st["in_use"],
                     "in_use_pct": round(st["in_use"] / cap * 100, 2) if cap else 0.0, "max_use_pct": cfg["max_use_pct"], "free": round(cap - st["in_use"], 2),
                     "reserve_pct": round(100 - sum(cfg["alloc"].values()), 2), "stop_all": st["stop_all"], "start": cfg["start"],
                     "month": {"key": ym(now), "net": round(mnet, 2), "pct": round(mnet / cap * 100, 2) if cap else 0.0, "target_pct": cfg["target_pct"], "target": round(tgt, 2),
                               "loss_limit": round(cfg["month_loss_pct"] / 100 * cap, 2), "gap": round(max(0.0, tgt - mnet), 2), "days_left": dim - today.day + 1,
                               "per_day": round(max(0.0, tgt - mnet) / (dim - today.day + 1), 2), "stopped": stv["month_stop"], "target_hit": stv["target_hit"]}},
            "decisions": decisions(cfg, unit, view, STATE.get("integrity"), fmk, nxt, STATE.get("history")),
            "ledger": [dict(r, entry_ist=_ist(r["entry_t"], "%Y-%m-%d %H:%M"), exit_ist=_ist(r["exit_t"], "%Y-%m-%d %H:%M") if r.get("exit_t") else None,
                            name=INFO[r["strat"]]["name"]) for r in res["rows"][::-1][:300]]}
    STATE.update(computed_at=data["computed_at"], data=data, error=None)
    return data


def _cfg_key(cfg, unit):
    return repr(sorted((k, repr(v)) for k, v in cfg.items() if k != "start")) + repr(sorted((k, v["capital"]) for k, v in unit.items()))


async def dashboard(is_admin=False):
    d = STATE.get("data")
    if not d:
        return {"name": NAME, "paper_only": True, "loading": True, "error": STATE.get("error"), "is_admin": is_admin}
    events = [{k: v for k, v in e.items() if k != "_id"} async for e in db.fund_events.find({}).sort("at", -1).limit(120)]
    out = {**d, "events": events, "history": STATE.get("history"), "integrity": STATE.get("integrity"), "error": STATE.get("error"), "is_admin": is_admin}
    if is_admin:
        try:
            import autotrade
            import broker_keys
            ks, rate = await broker_keys.status(), float((await autotrade.settings()).get("usd_inr") or 85.0)
            out["exchange"] = {m: {"connected": bool(ks[m].get("active")), "balance_inr": ks[m].get("checked_balance_inr"),
                                   "balance_usd": round(ks[m]["checked_balance_inr"] / rate, 2) if ks[m].get("checked_balance_inr") is not None else None,
                                   "checked_at": ks[m].get("checked_at"), "error": ks[m].get("check_error")} for m in ("TESTNET", "LIVE")}
        except Exception as e:
            out["exchange"] = {"error": str(e)[:120]}
    return out


async def control(action, strat, by):
    """pause / resume one strategy, stop_all / resume_all, restart (new empty fund book from now)."""
    if action in ("pause", "resume"):
        if strat not in ORDER:
            raise ValueError("Unknown strategy")
        text = f"{INFO[strat]['name']} {'paused' if action == 'pause' else 'resumed'} by {by}." + (" Its drawdown is measured afresh from now." if action == "resume" else "")
    elif action == "stop_all":
        text = f"STOP ALL by {by}: the fund takes no new trade until Resume all."
    elif action == "resume_all":
        text = f"Resume all by {by}: the fund trades again; its drawdown is measured afresh from now."
    elif action == "restart":
        await db.fund_trades.delete_many({})
        await db.fund_controls.delete_many({})
        await db.fund_settings.update_one({"_id": "cfg"}, {"$set": {"start": iso(now_utc())}}, upsert=True)
        await event("start", f"Fund book restarted by {by}: P&L, peaks and stops begin again from now. Settings are kept.", "warn")
        return await sync()
    else:
        raise ValueError("Unknown action")
    await db.fund_controls.insert_one({"t": time.time(), "action": action, "strat": strat if action in ("pause", "resume") else None, "by": by, "at": iso(now_utc())})
    await event("control", text, "warn")
    return await sync()


async def loop():
    await asyncio.sleep(200)                          # after the strategies' first calculations
    while True:
        try:
            await sync()
        except Exception as e:
            STATE["error"] = str(e)[:200]
            log.exception("fund manager")
        await asyncio.sleep(60)
