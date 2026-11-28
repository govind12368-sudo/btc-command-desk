"""Research & Edge page (owner, 28 Nov 2026): how the strategies on this site were found.

What data was loaded, how many strategy versions were backtested in each search, how many passed each check, which ones were
kept, and what the kept strategies made. The counts come from the research records (the strategy lab's REPORT.md, SCREENER.md,
EXPLORE.md, research/WORKBOOK_TOP10.md and journals, and the owner's workbooks); the results of the kept strategies are read
live from their own pages, so this page always shows the same numbers as Elite 4 Edge, Option Picks and the Straddle Bot.
Read-only. No strategy rules are shown here; users do not get the admin-only pages' rows (server.py).
"""
import logging
from datetime import date

from core import now_utc, iso

log = logging.getLogger("research")

DATA = [
    {"what": "BTC price · Binance BTCUSDT perpetual, 5-minute candles", "from": "2023-01-01", "used": "Indicator strategies (owner's 3-year workbook)"},
    {"what": "BTC price · Delta Exchange BTCUSD, 5-minute candles", "from": "2024-02-01", "used": "Re-checks on the exchange we trade: Elite 4 Edge, Candle Runs, Delta Top 3"},
    {"what": "BTC daily options · Delta Exchange India (every expiry: strikes, mark prices, real trades, settlement index, fee record)", "from": "2024-01-01",
     "used": "Option Picks and the options bots"},
    {"what": "Futures market data · funding rate, perp premium, open interest, long/short ratios, Coinbase premium", "from": "2023-01-01",
     "used": "Research on crowding and flows"},
    {"what": "This site's own scalp signals, replayed", "from": "2024-09-01", "used": "Flush Short (26 months)"},
]

# Every search, with what passed each check. Counts are versions backtested (one rule set on one timeframe with one exit style
# and direction = one version).
FUNNEL = [
    {"search": "Indicator strategies (owner's 3-year backtest workbook)", "data": "Binance BTCUSDT, 3 years", "tested": 31116,
     "passed": "Top 10 re-tested with our own code: 2 passed (also profitable on unseen 2023 data, and luck never did as well), 1 weaker one kept for its 60% win rate",
     "kept": 3, "kept_as": "Candle Runs (P&L #1, P&L #5, Win #1); P&L #1 and #5 also in Elite 4 Edge"},
    {"search": "Delta top-20 list (owner's Delta workbook)", "data": "Delta BTCUSD", "tested": 20,
     "passed": "Top 3 by net profit re-tested on Delta candles with Delta fees", "kept": 3, "kept_as": "Delta Top 3; Row 9 and Row 18 also in Elite 4 Edge"},
    {"search": "BTC option-selling strategies (owner's options workbook)", "data": "Delta options, Jan 2024 → Nov 2026", "tested": 2540,
     "passed": "5 picks rebuilt on Delta's own option prices: 1 confirmed, 2 profitable but sensitive to fills, 2 not confirmed",
     "kept": 1, "kept_as": "Option Picks (★ Recommended)"},
    {"search": "Rules built on this site's scalp signals", "data": "26 months of the site's signals", "tested": 10656,
     "passed": "40 passed both halves of the data; only the down-market rule beat a luck test", "kept": 1, "kept_as": "Flush Short (down markets)"},
]

STEPS = [
    ("Backtest on real exchange data", "Every version is run on 3–4 years of real prices with the exchange's fees and 18% GST."),
    ("Re-test with our own code", "A pick from a workbook is rebuilt from scratch and must give the same trades."),
    ("Unseen data", "It must also make money on a period it was not picked on."),
    ("Luck test", "Its result is compared with hundreds of runs of random trades in the same market; luck must not do as well."),
    ("Paper trading", "It runs on live prices for weeks with no money at risk; paper and backtest are compared day by day."),
    ("Real account, small size", "Only then a small real-money test (Real Account Tested)."),
]


def _years(d0, d1):
    a, b = date.fromisoformat(d0[:10]), date.fromisoformat(d1[:10])
    return round((b - a).days / 365.25, 1)


PROVEN_RULE = ("Proven = a profit after all fees on real exchange data, at least 50% winning trades, a profit in every calendar year tested, "
               "and at least 2 years and 100 trades. Profitable = a profit after all fees, but not every one of these yet.")


def _by_year(pairs):
    """[(date string, net)] -> {year: net}."""
    out = {}
    for d, v in pairs:
        if d and v is not None:
            out[d[:4]] = out.get(d[:4], 0.0) + v
    return {y: round(v, 2) for y, v in sorted(out.items())}


def verdict(net, win_rate, yrs, trades, yearly):
    """Proven / Profitable / Not profitable, with what is still missing (see PROVEN_RULE)."""
    if net is None or net <= 0:
        return "Not profitable", []
    why = []
    if win_rate is None or win_rate < 0.5:
        why.append("win rate below 50%")
    lost = [y for y, v in (yearly or {}).items() if v <= 0]
    if lost:
        why.append("loss in " + ", ".join(lost))
    if not yrs or yrs < 2:
        why.append("less than 2 years tested")
    elif (trades or 0) < 100:
        why.append("fewer than 100 trades")
    return ("Proven" if not why else "Profitable"), why


def _row(name, source, d0, d1, trades, wins, net, capital, stage, note="", page=None, compound=False, yearly=None):
    """One kept strategy. Return = net ÷ capital. A year: fixed size = return ÷ years; compounded = the yearly growth rate."""
    yrs = _years(d0, d1) if d0 and d1 else None
    ret = net / capital if capital and net is not None else None
    per_year = None
    if ret is not None and yrs and yrs >= 0.5:
        per_year = ((1 + ret) ** (1 / yrs) - 1 if ret > -1 else -1.0) if compound else ret / yrs
    wr = wins / trades if trades else None
    status, why = verdict(net, wr, yrs, trades, yearly)
    return {"strategy": name, "page": page, "source": source, "from": d0, "to": d1, "years": yrs, "trades": trades,
            "wins": wins, "win_rate": wr, "net": round(net, 2) if net is not None else None, "capital": capital,
            "return": ret, "per_year": per_year, "compound": bool(compound), "status": status, "status_why": why, "yearly": yearly or {},
            "stage": stage, "note": note}


async def kept():
    """The kept strategies' results, read from their own pages (same numbers as those pages)."""
    out, today = [], now_utc().date().isoformat()
    try:
        import elite
        d = elite.STATE.get("data") or {}
        a = d.get("A") or {}
        c = a.get("combined") or {}
        if c.get("trades"):
            cap = a.get("capital") or elite.PAPER_CAPITAL
            out.append(_row("Elite 4 Edge", "Indicator workbook + Delta top-20", a.get("from_ist", "2023-01-01"), (d.get("last_candle_ist") or today)[:10],
                            c["trades"], c["wins"], c["net"], cap, "Paper trading", f"$10,000 position at 10x, one trade at a time; return on a ${cap:,.0f} account", "/elite-4-edge",
                            yearly={y["year"]: y["net"] for y in a.get("yearly") or [] if y["year"][:1].isdigit()}))
    except Exception as e:
        log.warning("research elite: %s", e)
    try:
        import optpicks as op
        raw = (op.STATE.get("rows") or {}).get("SSTR") or []
        if raw:
            sizing = (op.STATE.get("data") or {}).get("sizing") or dict(op.SIZING_DEFAULT)
            cap = op.capital_for("SSTR", sizing)
            d0 = min([op.history().get("from") or "2024-01-01"] + [r["day"] for r in raw])
            d1 = max(r["day"] for r in raw)
            for comp, label in ((1.0, "Option Picks · compounded ★ Recommended"), (0.0, "Option Picks · fixed size")):
                priced = op.price_seq("SSTR", raw, {**sizing, "compound": comp})
                s = op.stats(priced, cap, bool(comp))
                out.append(_row(label, "Options workbook (2,540 strategies)", d0, d1, s["trades"], s["wins"], s["net"], cap, "Paper trading · bot on paper",
                                f"${sizing['put_capital']:,.0f} put + ${sizing['call_capital']:,.0f} call at {sizing['put_leverage']:g}x"
                                + ("; each trade sized from the balance" if comp else "; same size every trade"), "/option-picks", bool(comp),
                                _by_year((r["day"], r.get("net")) for r in priced)))
    except Exception as e:
        log.warning("research option picks: %s", e)
    try:
        import straddle
        bt = await straddle.backtest("sb")
        v = (bt.get("variants") or {}).get("mark_fixed")
        if v and v.get("rows"):
            m, rows, inp = v["metrics"], v["rows"], bt["inputs"]
            out.append(_row("Straddle Bot", "Owner's own straddle rules", rows[0]["date"], rows[-1]["date"], m["trades"], m["wins"], m["net"],
                            inp["starting_capital"], "Paper trading", f"${inp['starting_capital']:,.0f} at {inp['leverage']:g}x, fixed size", "/straddle-bot",
                            yearly=_by_year((r["date"], (r.get("net") if r.get("lots") else 0.0) + (r.get("skip_net") or 0.0)) for r in rows)))
    except Exception as e:
        log.warning("research straddle: %s", e)
    for mod, name, src, pg in (("runs", "Candle Runs", "Indicator workbook (31,116 backtests)", "/candle-runs"), ("trio", "Delta Top 3", "Delta top-20 workbook", "/delta-top3")):
        try:
            m = __import__(mod)
            d = m.STATE.get("data") or {}
            t = (d.get("totals") or {}).get("full") or {}
            if t.get("closed"):
                out.append(_row(name, src, ((d.get("backtest") or {}).get("from_ist") or "")[:10] or None, (d.get("last_candle_ist") or today)[:10],
                                t["closed"], t["wins"], t["net"], 10000.0, "Paper trading", "Return on the $10,000 position (leverage not counted)", pg,
                                yearly=_by_year((p["entry_ist"], p["net"]) for p in d.get("positions") or [] if p.get("status") == "CLOSED")))
        except Exception as e:
            log.warning("research %s: %s", mod, e)
    try:
        import flush
        d = flush.STATE.get("data") or {}
        t = d.get("all") or {}
        rows = d.get("rows") or []
        if t.get("closed") and rows:
            out.append(_row("Flush Short (down markets only)", "Site scalp-signal search (10,656 rules)", rows[-1]["time_ist"][:10], rows[0]["time_ist"][:10],
                            t["closed"], t["wins"], t["net"], 10000.0, "Paper trading", "Trades only in down markets; return on the $10,000 position", "/flush-short",
                            yearly=_by_year((r["time_ist"], r.get("net")) for r in rows if r.get("status") == "CLOSED")))
    except Exception as e:
        log.warning("research flush: %s", e)
    return out


# Same idea counted once (owner, 28 Nov 2026): rows that repeat trades already counted are shown as variants, not added to the total.
IDEAS = [("straddle", "Short ATM straddle into Delta's 17:30 daily expiry"), ("trend", "Indicator trades on BTC futures"),
         ("down", "Short breakdowns in down markets")]
ROLES = {
    "Option Picks · fixed size": ("straddle", None),
    "Option Picks · compounded ★ Recommended": ("straddle", "Same trades as Option Picks · fixed size, each sized from the balance"),
    "Straddle Bot": ("straddle", "Same trade on the same days as Option Picks (your own rules)"),
    "Candle Runs": ("trend", None),
    "Delta Top 3": ("trend", None),
    "Elite 4 Edge": ("trend", "Made of P&L #1 and #5 (counted in Candle Runs) and Row 9 and Row 18 (counted in Delta Top 3)"),
    "Flush Short (down markets only)": ("down", None),
}


def merge(rows):
    """Tag each row with its idea; counted rows first within an idea. Returns (rows, combined row of counted rows only)."""
    order = {k: i for i, (k, _) in enumerate(IDEAS)}
    names = dict(IDEAS)
    out, names_in = [], {r["strategy"] for r in rows}
    trend_parts = {"Candle Runs", "Delta Top 3"} <= names_in    # both there: they hold every Elite rule; else Elite is counted
    for r in rows:
        idea, same = ROLES.get(r["strategy"], ("other", None))
        if not trend_parts and r["strategy"] == "Elite 4 Edge":
            same = None
        elif not trend_parts and r["strategy"] in ("Candle Runs", "Delta Top 3"):
            same = "Shares rules with Elite 4 Edge, which is counted instead"
        out.append({**r, "idea": idea, "idea_name": names.get(idea, "Other"), "counted": same is None, "same_as": same})
    out.sort(key=lambda r: (order.get(r["idea"], 99), not r["counted"]))
    c = [r for r in out if r["counted"] and r.get("trades")]
    if not c:
        return out, None
    trades, wins = sum(r["trades"] for r in c), sum(r.get("wins") or 0 for r in c)
    net, cap = sum(r["net"] or 0.0 for r in c), sum(r["capital"] or 0.0 for r in c)
    yearly = {}
    for r in c:
        for y, v in (r.get("yearly") or {}).items():
            yearly[y] = round(yearly.get(y, 0.0) + v, 2)
    yrs = max(r["years"] or 0 for r in c)
    per_year = sum(r["net"] / r["years"] for r in c if r["years"] and r["years"] >= 0.5) / cap if cap else None
    status, why = verdict(net, wins / trades if trades else None, yrs, trades, dict(sorted(yearly.items())))
    comb = {"strategy": "All ideas together, each trade counted once", "includes": [r["strategy"] for r in c], "from": min(r["from"] for r in c if r["from"]),
            "to": max(r["to"] for r in c if r["to"]), "years": yrs, "trades": trades, "wins": wins, "win_rate": wins / trades if trades else None,
            "net": round(net, 2), "capital": cap, "return": net / cap if cap else None, "per_year": per_year, "yearly": dict(sorted(yearly.items())),
            "status": status, "status_why": why, "ideas": len({r["idea"] for r in c})}
    return out, comb


async def overview(hidden=None):
    today = now_utc().date().isoformat()
    data = [{**d, "to": today, "years": _years(d["from"], today)} for d in DATA]
    tested = sum(f["tested"] for f in FUNNEL)
    k = await kept()
    if hidden:
        k = [r for r in k if r.get("page") not in hidden]
    k, combined = merge(k)
    return {"computed_at": iso(now_utc()), "data": data, "funnel": FUNNEL, "steps": [{"title": a, "text": b} for a, b in STEPS], "kept": k, "combined": combined,
            "totals": {"tested": tested, "searches": len(FUNNEL), "kept": sum(f["kept"] for f in FUNNEL), "years": max(d["years"] for d in data),
                       "pass_rate": sum(f["kept"] for f in FUNNEL) / tested},
            "proven_rule": PROVEN_RULE, "note": "Backtests and paper trading, not a promise of future results. Each strategy's page shows its paper trading next to its backtest."}
