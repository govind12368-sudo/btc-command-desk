"""Option Picks: BTC daily-option selling on Delta Exchange India (paper only: no orders, no alerts).

On the site (owner, 23 Nov 2026): only pick 5, BTC-SSTR-ATM-1700-SL2X-F20, short ATM straddle at 17:00 IST, expiry 17:30 IST.
The other four picks stay in the code, switched off:
  IFLY  pick 2  BTC-IFLY-ATM2-1715-NS       iron fly: sell ATM put + call, buy put ~spot-2% and call ~spot+2%, 17:15 IST, hold
  SP16  pick 4  BTC-SP-ATM-1600-SL2X-F20    short ATM put 16:00, stop 2x on the 1-minute mark high
  BPS   pick 1  BTC-BPS-S2B5-2130-NS        bull put spread 2%/5%, 21:30 the day before
  BCS   pick 3  BTC-BCS-S2B5-2130-NS        bear call spread 2%/5%, 21:30 the day before

Daily expiry 17:30 IST (12:00 UTC). Entry value = the candle that OPENS at the entry time (1-minute for SSTR/SP16, 5-minute for the
others), so the live paper entry is at the end of that bar (SSTR 17:01). Strike = nearest listed strike (BTCUSD futures for SSTR/SP16,
BTC index .DEXBTUSD for the others). SELL at mark - max($0.10, 1%), BUY at mark + max($0.10, 1%) (rounded to $0.1 for SSTR/SP16).
SSTR/SP16: each leg stopped when a 1-minute mark high reaches 2x its sell price, bought back at stop + max($0.10, 1%) rounded up; the
other leg stays open; skip the day if the ATM put mark < $20 (the +2% rule was removed on 27 Nov 2026, owner: the days it skipped
made money in every year 2024-2026). Otherwise legs
settle at Delta's official settlement index.

Trades are stored as FACTS (strikes, marks, fills, stop times, settlement index, each expiry's fee record); size, P&L and fees are
worked out from the size settings, so changing a setting re-sizes every backtest and paper trade:
  qty per leg = floor(capital x leverage / reference price at entry / 0.001) x 0.001 BTC   (put legs use the put setting, calls the call)
  fees = Delta fee record (taker rate on notional, capped at a share of the premium) + 18% GST at entry, on a stop buy-back and at
         settlement (Delta's settlement fee is capped on the settlement value, so worthless legs pay none)
Settings (admin, db.opt_settings): put_capital, put_leverage, call_capital, call_leverage; default $2,000 x 25 each (owner, 23 Nov 2026),
and compound (owner, 26 Nov 2026: ★ Recommended = pick 5 with compounded capital, the default). Compounded: trades are priced in entry
order and each leg's capital = its share of the starting capital x (equity after the previous closed trade ÷ starting capital); a period
shown on its own (the last 13 months) starts again from the starting capital. The Straddle Bot page runs these rules as the
"Option Picks bot" (straddle.py, bot "op"): paper by default, real orders only after its own Live checklist.
"""
import asyncio
import json
import logging
import math
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from core import db, iso, now_utc

log = logging.getLogger("optpicks")
NAME = "Option Picks"
IST = timezone(timedelta(hours=5, minutes=30))
DELTA = "https://api.india.delta.exchange"
GST = 0.18
SIZING_DEFAULT = {"put_capital": 2000.0, "put_leverage": 25.0, "call_capital": 2000.0, "call_leverage": 25.0, "compound": 1.0}
SIZING_LIMITS = {"put_capital": (0, 1_000_000), "call_capital": (0, 1_000_000), "put_leverage": (1, 100), "call_leverage": (1, 100)}
RECOMMENDED = {"key": "SSTR", "compound": True, "label": "★ Recommended: Option Picks · Compounded"}   # the owner's pick (26 Nov 2026)
RECENT_FROM = "2025-10-30"
NOT_CHECKED = "Not checked: Delta margin for $100,000 of short options on $4,000, and order-book depth at ~5× the original size"
STRATS = {
    "IFLY": {"pick": 2, "code": "BTC-IFLY-ATM2-1715-NS", "name": "Iron fly, ATM body, wings ±2%", "kind": "hold", "ref": "index", "entry": (17, 15), "bar": 300,
             "legs": [(-1, "P", 1.0), (-1, "C", 1.0), (1, "P", 0.98), (1, "C", 1.02)], "enabled": False,
             "verdict": "Profitable on Delta's real prices, but loses with 5% worse fills and 2x fees; real trades at the fill price only about 1 day in 4."},
    "SP16": {"pick": 4, "code": "BTC-SP-ATM-1600-SL2X-F20", "name": "Short ATM put 16:00, stop 2x", "kind": "stop", "ref": "futures", "entry": (16, 0), "bar": 60,
             "typs": "P", "enabled": False, "verdict": "Profitable on Delta's real prices, but loses with 5% worse fills and 2x fees."},
    "SSTR": {"pick": 5, "code": "BTC-SSTR-ATM-1700-SL2X-F20", "name": "Short ATM straddle 17:00, stop 2x each leg", "kind": "stop", "ref": "futures", "entry": (17, 0),
             "bar": 60, "typs": "PC", "enabled": True,
             "verdict": "Profitable in 2024, 2025 and 2026 on Delta's real prices. Sensitive to fills: with 5% worse fills and 2× fees the full period turns negative."},
    "BPS": {"pick": 1, "code": "BTC-BPS-S2B5-2130-NS", "name": "Bull put spread sell 2% / buy 5%", "kind": "hold", "ref": "index", "entry": (21, 30), "bar": 300,
            "legs": [(-1, "P", 0.98), (1, "P", 0.95)], "enabled": False, "verdict": "Little profit on Delta's real prices and a deep drawdown; a loss in 2025."},
    "BCS": {"pick": 3, "code": "BTC-BCS-S2B5-2130-NS", "name": "Bear call spread sell 2% / buy 5%", "kind": "hold", "ref": "index", "entry": (21, 30), "bar": 300,
            "legs": [(-1, "C", 1.02), (1, "C", 1.05)], "enabled": False, "verdict": "A loss on Delta's real prices over 2024-2026."},
}
ORDER = ["IFLY", "SP16", "SSTR", "BPS", "BCS"]
ENABLED = [k for k in ORDER if STRATS[k]["enabled"]]
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "optpicks_history.json")
HISTORY_TO = date(2026, 11, 22)                    # the history file covers expiries up to this day; the server backtests later days
B_FROM = date(2026, 10, 1)                         # journal B: the server re-runs the same code on Delta's data from this day (owner, 26 Nov 2026)
STATE = {"computed_at": None, "error": None, "data": None, "sizing": dict(SIZING_DEFAULT)}
_HIST = {}


# ================================================================ size settings and pricing (P&L from trade facts)
def leg_qty(typ, ref_price, sizing):
    cap, lev = (sizing["put_capital"], sizing["put_leverage"]) if typ == "P" else (sizing["call_capital"], sizing["call_leverage"])
    return math.floor(cap * lev / ref_price / 0.001 + 1e-9) * 0.001 if ref_price else 0.0


def capital_for(key, sizing):
    typs = STRATS[key]["typs"] if STRATS[key]["kind"] == "stop" else {t for _, t, _ in STRATS[key]["legs"]}
    return sum(sizing["put_capital"] if t == "P" else sizing["call_capital"] for t in set(typs))


def compounding(sizing):
    return bool(sizing.get("compound"))


def size_label(sizing):
    p, c = sizing, sizing
    if p["put_capital"] == c["call_capital"] and p["put_leverage"] == c["call_leverage"]:
        lab = f"${p['put_capital']:,.0f} × {p['put_leverage']:g}x per leg (put + call)"
    else:
        lab = f"put ${p['put_capital']:,.0f} × {p['put_leverage']:g}x, call ${c['call_capital']:,.0f} × {c['call_leverage']:g}x"
    return lab + (" · compounded" if compounding(sizing) else " · fixed")


def scaled(key, sizing, equity):
    """Compounded sizing: each leg gets its share of the current equity (shares = the starting put / call capital)."""
    start = capital_for(key, sizing)
    if not compounding(sizing) or not start or equity is None:
        return sizing
    f = max(equity, 0.0) / start
    return {**sizing, "put_capital": sizing["put_capital"] * f, "call_capital": sizing["call_capital"] * f}


def price_seq(key, rows, sizing, start=None):
    """Prices trades in entry order. Fixed: every trade at the set size. Compounded: every trade sized from the equity after the
    previous closed trade. Adds capital (used for the trade) and equity (after it) to each row. The worse-fills view (net_v2)
    compounds along its own balance, so its total is what that account would really have made."""
    eq = eq2 = capital_for(key, sizing) if start is None else start
    comp = compounding(sizing)
    out = []
    for t in sorted(rows, key=lambda r: (r.get("entry_ist") or r["day"], r["day"])):
        s = scaled(key, sizing, eq)
        p = price(t, s)
        p["capital"] = round(capital_for(key, s), 2)
        if comp and p.get("net_v2") is not None:
            p["net_v2"] = price(t, scaled(key, sizing, eq2))["net_v2"]
            eq2 += p["net_v2"]
        if p.get("net") is not None:
            eq += p["net"]
            p["equity"] = round(eq, 2)
        else:
            p["equity"] = None
        out.append(p)
    return out


def price(t, sizing):
    """P&L of a trade (backtest or paper) at the given size, from its facts. Net is None while any leg is still open."""
    day = date.fromisoformat(t["day"])
    ref, settle = t.get("price"), t.get("settle")
    legs, gross, fees, gross2, fees2, open_ = [], 0.0, 0.0, 0.0, 0.0, False
    for l in t.get("legs", []):
        typ = l.get("type") or l["sym"][0]
        q = round(leg_qty(typ, ref, sizing), 3)
        p = {"taker": l.get("taker"), "cap": l.get("cap")}
        sd = -1 if l["side"] == "SELL" else 1
        px, ex, xp = l["fill"], l.get("exit"), l.get("exit_price")
        f_in = fee(px, q, ref, p, day)
        px2 = px * 0.95 if sd < 0 else px * 1.05
        if ex is None or xp is None:
            open_ = True
            legs.append({**l, "qty": q, "gross": None, "fees": round(f_in, 4)})
            fees += f_in
            continue
        if ex == "Stop":
            f_out, xp2 = fee(xp, q, ref, p, day), xp * 1.05
            f2 = 2 * (fee(px2, q, ref, p, day) + fee(xp2, q, ref, p, day))
        else:
            f_out, xp2 = fee(xp, q, settle, p, day), xp
            f2 = 2 * (fee(px2, q, ref, p, day) + fee(xp, q, settle, p, day))
        g = (px - xp) * q if sd < 0 else (xp - px) * q
        g2 = (px2 - xp2) * q if sd < 0 else (xp2 - px2) * q
        gross += g; fees += f_in + f_out; gross2 += g2; fees2 += f2
        legs.append({**l, "qty": q, "gross": round(g, 4), "fees": round(f_in + f_out, 4)})
    qtys = sorted({l["qty"] for l in legs})
    return {**t, "legs": legs, "qty": qtys[0] if len(qtys) == 1 else None, "gross": None if open_ else gross, "fees": fees,
            "net": None if open_ else gross - fees, "net_v2": None if open_ else gross2 - fees2}


async def get_sizing():
    doc = await db.opt_settings.find_one({"_id": "sizing"}) or {}
    s = {k: float(doc.get(k, v)) for k, v in SIZING_DEFAULT.items()}
    STATE["sizing"] = s
    return s


async def set_sizing(values, by):
    s = await get_sizing()
    for k, v in values.items():
        if k not in SIZING_DEFAULT or v is None:
            continue
        if k == "compound":
            s[k] = 1.0 if v in (True, 1, 1.0, "1", "true", "compounded") else 0.0
            continue
        lo, hi = SIZING_LIMITS[k]
        v = float(v)
        if not (lo < v <= hi) and not (k.endswith("capital") and v == 0):
            raise ValueError(f"{k} must be between {lo} and {hi}")
        s[k] = v
    await db.opt_settings.update_one({"_id": "sizing"}, {"$set": {**s, "by": by, "at": iso(now_utc())}}, upsert=True)
    STATE["sizing"] = s
    await compute()
    return s


# ================================================================ pure rules (the lab runs this same code on its cached data)
def settle_t(d):
    return int(datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc).timestamp())


def entry_t(key, d):
    """Open time of the entry bar (21:30 entries are the day before the expiry)."""
    h, m = STRATS[key]["entry"]
    day = d - timedelta(days=1) if (h, m) > (17, 30) else d
    return int(datetime(day.year, day.month, day.day, h, m, tzinfo=IST).timestamp())


def at(cmap, t):
    c = cmap.get(t)
    return c[3] if c else None


def after(cmap, t, res, end):
    return [cmap[x] for x in sorted(cmap) if t + res <= x < end]


def nearest(prods, typ, target, t):
    best = None
    for sym, p in prods.items():
        if p["type"] != typ or (p.get("launch") and p["launch"] > t):
            continue
        dd = abs(p["k"] - target)
        if best is None or dd < best[0] or (dd == best[0] and p["k"] < prods[best[1]]["k"]):
            best = (dd, sym)
    return best[1] if best else None


def fee_rates(p, day):
    taker = p.get("taker") or (0.00015 if day < date(2025, 12, 11) else 0.0001)
    cap = p.get("cap") or (0.05 if day < date(2025, 12, 11) else 0.035)
    return taker, cap


def fee(px, qty, notional_px, p, day):
    taker, cap = fee_rates(p, day)
    return min(taker * notional_px * qty, cap * max(px, 0.0) * qty) * (1 + GST)


def intrinsic(p, settle):
    return max(p["k"] - settle, 0.0) if p["type"] == "P" else max(settle - p["k"], 0.0)


def fl1(x):
    return math.floor(x * 10 + 1e-7) / 10


def ce1(x):
    return math.ceil(x * 10 - 1e-7) / 10


def real_print(trades, t0, t1, side, px):
    vol, ok = 0.0, False
    for t in sorted(trades or {}):
        if t0 <= t <= t1:
            o, h, l, c, v = trades[t]
            if v > 0:
                vol += v
                if (side < 0 and h >= px) or (side > 0 and l <= px):
                    ok = True
    return ok, vol


def pick_legs(key, prods, price, t):
    """[(side, sym)] for a strategy at reference price `price`, or (None, reason)."""
    s = STRATS[key]
    if s["kind"] == "hold":
        legs = [(side, nearest(prods, typ, price * f, t)) for side, typ, f in s["legs"]]
        if any(sym is None for _, sym in legs):
            return None, "strike not listed at entry"
        if key in ("BPS", "BCS") and legs[0][1] == legs[1][1]:
            return None, "both legs the same strike"
        return legs, None
    legs = [(-1, nearest(prods, typ, price, t)) for typ in s["typs"]]
    if any(sym is None for _, sym in legs):
        return None, "strike not listed at entry"
    return legs, None


PUT_MIN = 20.0          # pick 5's one skip rule: ATM put mark below $20 (premium too small for the fees)


def skip_rule(t):
    """Pick 5's skip rule on a trade's facts. The +2% rule (BTC futures up > 2% since 17:31 IST yesterday) was removed on
    27 Nov 2026 by the owner; chg_1731 is still recorded."""
    put = next((l["mark"] for l in t.get("legs") or [] if (l.get("type") or l["sym"][0]) == "P"), None)
    return "put mark below $20" if put is not None and put < PUT_MIN else None


def apply_rules(key, rows):
    """Re-checks stored facts with today's rules (days stored before 27 Nov 2026 can carry the old +2% skip)."""
    if STRATS[key]["kind"] != "stop":
        return rows
    return [{**r, "skip": skip_rule(r)} if r.get("legs") else r for r in rows]


def build_trade(key, D):
    """The backtest trade of one strategy on one expiry day from fetched data D (see needs()): FACTS only (no size, no P&L).
    Returns (trade, reason). A trade the skip rules reject has t["skip"] set."""
    s, prods, settle, day = STRATS[key], D["prods"], D["settle"], D["day"]
    st, et = settle_t(day), entry_t(key, day)
    out = {"key": key, "day": day.isoformat(), "entry_ist": datetime.fromtimestamp(et, IST).strftime("%Y-%m-%d %H:%M"), "settle": settle, "legs": [], "skip": None}
    if s["kind"] == "hold":
        spot = at(D["index"], et)
        if spot is None:
            return None, "no BTC index at entry"
        legs, why = pick_legs(key, prods, spot, et)
        if not legs:
            return None, why
        out["price"] = spot
        near = et > st - 43200
        real_all = True
        for side, sym in legs:
            p = prods[sym]
            mark = at(D["m5"].get(sym, {}), et)
            if mark is None:
                return None, f"no mark for {sym} at entry"
            px = mark - max(0.10, 0.01 * mark) if side < 0 else mark + max(0.10, 0.01 * mark)
            w = (et - 120, et + 120) if near else (et - 600, et + 600)
            ok, vol = real_print((D["t1"] if near else D["t5"]).get(sym), *w, side, px)
            real_all &= ok
            out["legs"].append({"side": "SELL" if side < 0 else "BUY", "sym": sym, "type": p["type"], "k": p["k"], "mark": round(mark, 4), "fill": round(px, 4),
                                "exit": "Settle", "exit_price": round(intrinsic(p, settle), 4), "taker": p.get("taker"), "cap": p.get("cap"), "real": ok, "vol": vol})
        out.update(exit="Settle", real=real_all)
        return out, None
    # short ATM leg(s) with a 2x stop and the put skip rule
    f = at(D["fut"], et)
    fprev = at(D["futp"], st - 86400 + 60)
    if f is None:
        return None, "no futures price at entry"
    chg = f / fprev - 1 if fprev else None
    legs, why = pick_legs(key, prods, f, et)
    if not legs:
        return None, why
    marks = {}
    for _, sym in legs:
        m = at(D["m1"].get(sym, {}), et)
        if m is None:
            return None, f"no mark for {sym} at entry"
        marks[sym] = m
    put = next((marks[sym] for _, sym in legs if prods[sym]["type"] == "P"), None)
    skip = "put mark below $20" if put is not None and put < PUT_MIN else None
    out.update(price=f, chg_1731=chg, skip=skip)
    real_all, exits = True, []
    for _, sym in legs:
        p, m = prods[sym], marks[sym]
        sell = fl1(m - max(0.10, 0.01 * m))
        stop = 2 * sell
        ex, ex_t = None, None
        for c_t in sorted(D["m1"].get(sym, {})):
            if et + 60 <= c_t < st and D["m1"][sym][c_t][1] >= stop - 1e-9:
                ex, ex_t = ce1(stop + max(0.10, 0.01 * stop)), c_t
                break
        ok, vol = real_print(D["t1"].get(sym), et - 120, et + 120, -1, sell)
        real_all &= ok
        exits.append("Stop" if ex is not None else "Settle")
        out["legs"].append({"side": "SELL", "sym": sym, "type": p["type"], "k": p["k"], "mark": round(m, 4), "fill": sell, "stop": round(stop, 2), "exit": exits[-1],
                            "exit_price": ex if ex is not None else round(intrinsic(p, settle), 4),
                            "stop_ist": datetime.fromtimestamp(ex_t + 60, IST).strftime("%H:%M") if ex_t else None,
                            "taker": p.get("taker"), "cap": p.get("cap"), "real": ok, "vol": vol})
    out.update(exit="/".join(exits), real=real_all)
    return out, None


def needs(key, D):
    """Candle requests a strategy needs once the day's products, index and futures are known: [(name, symbol, resolution, start, end)]."""
    s, day, prods = STRATS[key], D["day"], D["prods"]
    st, et = settle_t(day), entry_t(key, day)
    if s["kind"] == "hold":
        spot = at(D["index"], et)
        legs, _ = pick_legs(key, prods, spot, et) if spot else (None, None)
        out = []
        for _, sym in legs or []:
            out.append(("m5", sym, "MARK:" + sym, "5m", et - 1200, et + 900))
            near = et > st - 43200
            out.append(("t1" if near else "t5", sym, sym, "1m" if near else "5m", et - (300 if near else 3600), et + (300 if near else 3600)))
        return out
    f = at(D["fut"], et)
    legs, _ = pick_legs(key, prods, f, et) if f else (None, None)
    return [x for _, sym in legs or [] for x in (("m1", sym, "MARK:" + sym, "1m", et - 300, st), ("t1", sym, sym, "1m", et - 300, et + 300))]


def stats(rows, capital, compound=False):
    """Priced rows (net set) -> totals; equity, peak and drawdown start from `capital`.
    Compounded rows: drawdown % is of the peak equity (the size grows with the equity)."""
    rows = sorted([r for r in rows if r.get("net") is not None], key=lambda r: r["entry_ist"])
    n = len(rows)
    if not n:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": None, "gross": 0.0, "fees": 0.0, "net": 0.0, "return": 0.0, "max_dd": 0.0, "max_dd_pct": 0.0,
                "worst": None, "best": None, "view2_net": 0.0, "real_fill": None, "lowest_balance": capital, "capital": capital,
                "final_equity": capital, "compounded": bool(compound)}
    eq = pk = low = capital
    dd = ddp = 0.0
    for r in rows:
        eq += r["net"]; pk = max(pk, eq); dd = max(dd, pk - eq); low = min(low, eq)
        ddp = max(ddp, (pk - eq) / pk if pk > 0 else 0.0)
    w = sum(r["net"] > 0 for r in rows)
    net = sum(r["net"] for r in rows)
    return {"trades": n, "wins": w, "losses": n - w, "win_rate": w / n, "gross": round(sum(r["gross"] for r in rows), 2), "fees": round(sum(r["fees"] for r in rows), 2),
            "net": round(net, 2), "return": net / capital if capital else None, "max_dd": round(dd, 2),
            "max_dd_pct": ddp if compound else (dd / capital if capital else None), "final_equity": round(eq, 2), "compounded": bool(compound),
            "worst": round(min(r["net"] for r in rows), 2), "best": round(max(r["net"] for r in rows), 2),
            "view2_net": round(sum(r.get("net_v2") if r.get("net_v2") is not None else r["net"] for r in rows), 2),
            "real_fill": sum(1 for r in rows if r.get("real")) / n, "lowest_balance": round(low, 2), "capital": capital}


def tables(rows, capital, compound=False):
    """Yearly and monthly tables. Compounded: each period starts from the equity it began with (return = net ÷ that equity)."""
    rows = sorted([r for r in rows if r.get("net") is not None], key=lambda r: r["entry_ist"])
    by_y, by_m = defaultdict(list), defaultdict(list)
    for r in rows:
        by_y[r["day"][:4]].append(r); by_m[r["day"][:7]].append(r)
    start_of = lambda sub: (sub[0]["equity"] - sub[0]["net"]) if compound and sub and sub[0].get("equity") is not None else capital
    cum, months = 0.0, []
    for m in sorted(by_m):
        s = stats(by_m[m], start_of(by_m[m]), compound); cum += s["net"]
        months.append({"month": m, **{k: s[k] for k in ("trades", "wins", "win_rate", "gross", "fees", "net")}, "cumulative": round(cum, 2),
                       "equity": s["final_equity"] if compound else round(capital + cum, 2)})
    cum, years = 0.0, []
    for y in sorted(by_y):
        s = stats(by_y[y], start_of(by_y[y]), compound); cum += s["net"]
        years.append({"year": y, **{k: s[k] for k in ("trades", "wins", "win_rate", "gross", "fees", "net", "max_dd", "return")}, "cumulative": round(cum, 2),
                      "start_equity": round(s["capital"], 2) if compound else capital, "equity": s["final_equity"] if compound else round(capital + cum, 2)})
    tot = stats(rows, capital, compound)
    years.append({"year": "Total till date", **{k: tot[k] for k in ("trades", "wins", "win_rate", "gross", "fees", "net", "max_dd", "return")}, "cumulative": tot["net"],
                  "start_equity": capital, "equity": tot["final_equity"]})
    return {"stats": tot, "yearly": years, "monthly": months}


def curve(rows):
    """Balance after every closed trade, oldest first (the page sends only the newest trades, so the chart needs its own series)."""
    return [{"t": r["day"], "v": r["equity"]} for r in sorted(rows, key=lambda r: r["entry_ist"]) if r.get("equity") is not None]


def realdata_rows(key, rows, sizing, period_from="2024-01-01", period_to=None):
    """The two rows of the real-data backtest table: the whole period and the last 13 months (from 30 Oct 2025).
    `rows` are trade facts (priced or not); each period is priced from the starting capital, so a compounded period starts fresh."""
    cap = capital_for(key, sizing)
    comp = compounding(sizing)
    rows = [r for r in price_seq(key, rows, sizing) if r.get("net") is not None]
    if not rows:
        return []
    last = period_to or max(r["day"] for r in rows)
    fmt = lambda d: date.fromisoformat(d).strftime("%-d %b %Y")
    out = []
    recent = [r for r in price_seq(key, [r for r in rows if r["day"] >= RECENT_FROM], sizing) if r.get("net") is not None]
    for lab, sub in ((f"{fmt(period_from)} – {fmt(last)}", rows), (f"{fmt(RECENT_FROM)} – {fmt(last)}", recent)):
        st = stats(sub, cap, comp)
        out.append({"size": size_label(sizing), "period": lab, **{k: st[k] for k in ("trades", "wins", "losses", "win_rate", "gross", "fees", "net", "return", "max_dd",
                                                                                     "max_dd_pct", "worst", "best", "view2_net", "real_fill")},
                    "years": {y: (round(sum(r["net"] for r in sub if r["day"][:4] == y), 2) if any(r["day"][:4] == y for r in sub) else None) for y in ("2024", "2025", "2026")},
                    "verdict": STRATS[key]["verdict"] if sub is rows else None})
    return out


# ================================================================ Delta data (server)
async def _json(path):
    from sources import http_get
    return (await http_get(DELTA + path)).json()


async def candles(sym, res, s, e):
    import urllib.parse
    r = (await _json(f"/v2/history/candles?resolution={res}&symbol={urllib.parse.quote(sym)}&start={int(s)}&end={int(e)}")).get("result") or []
    return {int(c["time"]): (float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]), float(c.get("volume") or 0)) for c in r}


def _ts(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()) if s else None


def parse_product(p):
    sym = p.get("symbol", "")
    if not sym.startswith(("P-BTC-", "C-BTC-")):
        return None
    sp = p.get("product_specs") or {}
    try:
        k = float(p.get("strike_price") or sym.split("-")[2])
    except Exception:
        return None
    return sym, {"k": k, "type": sym[0], "settle": float(sp["settlement_index_price"]) if sp.get("settlement_index_price") else None,
                 "taker": float(p.get("taker_commission_rate") or 0) or None, "cap": float(sp.get("premium_commission_rate") or 0) or None,
                 "launch": _ts(p.get("launch_time")), "settle_t": _ts(p.get("settlement_time"))}


async def products(d, states="expired"):
    import urllib.parse
    out, after_ = {}, None
    for _ in range(10):
        q = f"/v2/products?contract_types=call_options,put_options&states={states}&expiry={d.isoformat()}&page_size=500"
        body = await _json(q + ("&after=" + urllib.parse.quote(after_) if after_ else ""))
        for p in body.get("result") or []:
            x = parse_product(p)
            if x:
                out[x[0]] = x[1]
        after_ = (body.get("meta") or {}).get("after")
        if not after_:
            break
    return out


async def chain(d):
    """Live option chain for the expiry on day d: {sym: {k, type, mark, bid, ask, spot}}."""
    body = await _json(f"/v2/tickers?contract_types=call_options,put_options&underlying_asset_symbols=BTC&expiry_date={d.strftime('%d-%m-%Y')}")
    out = {}
    for t in body.get("result") or []:
        sym = t.get("symbol", "")
        if not sym.startswith(("P-BTC-", "C-BTC-")):
            continue
        q = t.get("quotes") or {}
        f = lambda v: float(v) if v not in (None, "") else None
        out[sym] = {"k": float(t.get("strike_price") or sym.split("-")[2]), "type": sym[0], "mark": f(t.get("mark_price")), "bid": f(q.get("best_bid")),
                    "ask": f(q.get("best_ask")), "spot": f(t.get("spot_price")), "product_id": t.get("product_id")}
    return out


async def fetch_day(d, keys):
    """Everything build_trade needs for expiry day d (server-side backtest of a finished day)."""
    st = settle_t(d)
    prods = await products(d)
    settle = next((p["settle"] for p in prods.values() if p["settle"]), None)
    D = {"day": d, "prods": prods, "settle": settle, "index": {}, "fut": {}, "futp": {}, "m1": {}, "m5": {}, "t1": {}, "t5": {}}
    if not prods or not settle:
        return D
    D["index"] = await candles(".DEXBTUSD", "5m", st - 86400 - 1800, st + 300)
    D["fut"] = await candles("BTCUSD", "1m", st - 7500, st + 300)
    D["futp"] = await candles("BTCUSD", "1m", st - 86400 - 600, st - 86400 + 600)
    for key in keys:
        for name, sym, csym, res, s, e in needs(key, D):
            got = await candles(csym, res, s, e)
            D[name][sym] = {**D[name].get(sym, {}), **got}
    return D


# ================================================================ history + server backtest days
def history():
    if "doc" not in _HIST:
        try:
            with open(HISTORY_FILE) as f:
                _HIST["doc"] = json.load(f)
        except Exception as e:
            log.warning("option history not loaded: %s", e)
            _HIST["doc"] = {"trades": {}, "check": {}}
    return _HIST["doc"]


async def backtest_day(d):
    """Backtest one finished expiry day on the server (same code as the history file) and store it. Days the history file
    covers (journal B only) are run for the enabled pick; later days for every pick. Returns True when a day was added."""
    if await db.opt_backtest.find_one({"_id": d.isoformat()}, {"_id": 1}):
        return False
    keys = ORDER if d > HISTORY_TO else ENABLED
    D = await fetch_day(d, keys)
    if not D["settle"]:
        return False                                  # not settled yet: try again later
    doc = {"_id": d.isoformat(), "at": iso(now_utc()), "trades": {}, "skips": {}}
    for key in keys:
        try:
            t, why = build_trade(key, D)
        except Exception as e:
            t, why = None, f"error: {str(e)[:120]}"
        if t:
            doc["trades"][key] = t
        else:
            doc["skips"][key] = why
    await db.opt_backtest.insert_one(doc)
    return True


def _facts(t):
    """What must be equal for journal B to confirm journal A on a day: contracts, fills, exits, stop times, prices."""
    if not t or not t.get("legs"):                      # facts are compared whether the rule trades the day or not
        return None
    return (round(float(t["price"]), 1), round(float(t.get("settle") or 0), 2),
            tuple((l["sym"], round(l["fill"], 4), l.get("exit"), round(l.get("exit_price") or 0, 4), l.get("stop_ist")) for l in t.get("legs", [])))


async def server_rows(key, since=B_FROM):
    """Journal B: the server's own backtest of every expiry from `since` (trade facts, skips included)."""
    out = []
    async for doc in db.opt_backtest.find({"_id": {"$gte": since.isoformat()}}).sort("_id", 1):
        if key in doc.get("trades", {}):
            out.append({**doc["trades"][key], "day": doc["_id"], "source": "server"})
        elif key in doc.get("skips", {}):
            out.append({"day": doc["_id"], "skip": doc["skips"][key], "source": "server", "legs": []})
    return apply_rules(key, out)


def match_a(key, rows_b):
    """Days up to the end of the history file where journal B (server) and journal A (history file) agree."""
    hist = {t["day"]: t for t in history().get("trades", {}).get(key, [])}
    checked = [r for r in rows_b if r["day"] <= HISTORY_TO.isoformat()]
    diff = [r["day"] for r in checked if _facts(r) != _facts(hist.get(r["day"]))]
    return {"checked": len(checked), "same": len(checked) - len(diff), "differ": diff[:20], "to": HISTORY_TO.isoformat()}


async def backtest_rows():
    hist = history().get("trades", {})
    rows = {k: [r for r in hist.get(k, [])] for k in ORDER}
    async for doc in db.opt_backtest.find({"_id": {"$gt": HISTORY_TO.isoformat()}}).sort("_id", 1):
        for k, t in doc["trades"].items():
            rows.setdefault(k, []).append({**t, "source": "server"})
    return {k: apply_rules(k, v) for k, v in rows.items()}


# ================================================================ live paper engine
def live_entry_t(key, d):
    return entry_t(key, d) + STRATS[key]["bar"]       # end of the entry bar = the moment the backtest's entry value is known


class Engine:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.started = clock()
        self.lock = asyncio.Lock()
        self.fprev = {}

    async def futures_price(self):
        """BTCUSD futures: the site's Delta websocket quote, else the REST ticker (Auto-Trade's own price cursor is not touched)."""
        import autotrade
        from sources import WS
        last = ((WS.get("quote") or {}).get("last")) if WS.get("status") == "connected" else None
        if last:
            return float(last)
        return (await autotrade.rest_price())[0]

    async def fut_1731(self, d):
        """BTC futures at 17:31 IST the day before expiry d (close of the 1-minute candle opening at 17:31)."""
        if d not in self.fprev:
            st = settle_t(d)
            c = await candles("BTCUSD", "1m", st - 86400 - 300, st - 86400 + 600)
            v = at(c, st - 86400 + 60)
            if v is None:
                return None
            self.fprev[d] = v
        return self.fprev[d]

    async def open_trade(self, key, d, now):
        s = STRATS[key]
        doc = {"_id": f"{key}-{d.isoformat()}", "key": key, "day": d.isoformat(), "created": iso(now_utc()), "entry_ist": datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d %H:%M:%S"),
               "status": "OPEN", "legs": []}
        ch = await chain(d)
        if not ch:
            doc.update(status="SKIPPED", reason="no option chain for this expiry")
            return doc
        prods = {sym: {"k": c["k"], "type": c["type"]} for sym, c in ch.items() if c["mark"] is not None}
        if s["ref"] == "index":
            price = next((c["spot"] for c in ch.values() if c["spot"]), None)
        else:
            price = await self.futures_price()
        if not price:
            doc.update(status="SKIPPED", reason="no BTC price")
            return doc
        legs, why = pick_legs(key, prods, price, now)
        if not legs:
            doc.update(status="SKIPPED", reason=why)
            return doc
        doc.update(price=price, ref=s["ref"])
        if s["kind"] == "stop":
            fp = await self.fut_1731(d)
            chg = price / fp - 1 if fp else None
            put = next((ch[sym]["mark"] for _, sym in legs if sym.startswith("P-")), None)
            doc["chg_1731"] = chg
            if put is not None and put < PUT_MIN:
                doc.update(status="SKIPPED", reason=f"put mark ${put:.2f} below $20")
        for side, sym in legs:
            c = ch[sym]
            info = await self.fee_info(sym)
            m = c["mark"]
            if s["kind"] == "stop":
                px = fl1(m - max(0.10, 0.01 * m))
            else:
                px = m - max(0.10, 0.01 * m) if side < 0 else m + max(0.10, 0.01 * m)
            doc["legs"].append({"side": "SELL" if side < 0 else "BUY", "sym": sym, "k": c["k"], "type": c["type"], "mark": m, "bid": c["bid"], "ask": c["ask"],
                                "fill": round(px, 4), "stop": round(2 * px, 2) if s["kind"] == "stop" else None, "status": "OPEN", "taker": info[0], "cap": info[1],
                                "high": m})
        return doc

    async def fee_info(self, sym):
        try:
            p = (await _json(f"/v2/products/{sym}")).get("result") or {}
            x = parse_product(p)
            if x:
                return x[1]["taker"], x[1]["cap"]
        except Exception as e:
            log.warning("fee info %s: %s", sym, e)
        return None, None

    async def check_stops(self, t, now):
        """SP16/SSTR: a leg is bought back when a 1-minute mark high since entry reaches 2x its sell price."""
        changed = False
        d = date.fromisoformat(t["day"])
        st = settle_t(d)
        et = live_entry_t(t["key"], d)
        for leg in t["legs"]:
            if leg["status"] != "OPEN" or not leg.get("stop"):
                continue
            cs = await candles("MARK:" + leg["sym"], "1m", et - 60, min(now, st) + 60)
            hi = max([c[1] for ct, c in cs.items() if et <= ct < st] + [leg.get("high") or 0])
            if hi > (leg.get("high") or 0):
                leg["high"] = hi; changed = True
            if hi >= leg["stop"] - 1e-9:
                hit = min((ct for ct, c in cs.items() if et <= ct < st and c[1] >= leg["stop"] - 1e-9), default=None)
                ex = ce1(leg["stop"] + max(0.10, 0.01 * leg["stop"]))
                leg.update(status="STOPPED", exit="Stop", exit_price=ex, stop_ist=datetime.fromtimestamp((hit or now) + 60, IST).strftime("%H:%M"))
                changed = True
        return changed

    async def settle(self, t):
        d = date.fromisoformat(t["day"])
        prods = await products(d)
        settle = next((p["settle"] for p in prods.values() if p["settle"]), None)
        if not settle:
            return False
        for leg in t["legs"]:
            if leg["status"] == "OPEN":
                leg.update(status="SETTLED", exit="Settle", exit_price=round(intrinsic(leg, settle), 4))
        t.update(status="CLOSED", settle=settle, closed=iso(now_utc()), exit="/".join(l["exit"] for l in t["legs"]))
        return True

    async def fill_check(self, t):
        """Each leg: did a real trade print at our fill price or better within ±2 minutes of the paper entry? (Delta traded candles)"""
        et = int(datetime.fromisoformat(t["entry_ist"]).replace(tzinfo=IST).timestamp())
        for leg in t["legs"]:
            cs = await candles(leg["sym"], "1m", et - 180, et + 180)
            ok, vol = real_print(cs, et - 120, et + 120, -1 if leg["side"] == "SELL" else 1, leg["fill"])
            leg.update(real=ok, vol=vol)
        t["real"] = all(l["real"] for l in t["legs"])
        t["fill_checked"] = True

    async def tick(self):
        async with self.lock:
            now = self.clock()
            today = datetime.fromtimestamp(now, IST).date()
            for key in ENABLED:
                for d in (today, today + timedelta(days=1)):
                    le = live_entry_t(key, d)
                    if not (le <= now < settle_t(d) + 6 * 3600):
                        continue
                    _id = f"{key}-{d.isoformat()}"
                    if await db.opt_live.find_one({"_id": _id}, {"_id": 1}):
                        continue
                    if now > le + 180 or now >= settle_t(d):
                        reason = "server was not running at the entry time" if self.started > le else "entry window missed (data delay)"
                        await db.opt_live.insert_one({"_id": _id, "key": key, "day": d.isoformat(), "status": "MISSED", "reason": reason, "created": iso(now_utc())})
                        continue
                    try:
                        doc = await self.open_trade(key, d, now)
                    except Exception as e:
                        log.warning("option entry %s: %s", _id, e)
                        continue                       # retried on the next tick while inside the 3-minute window
                    await db.opt_live.insert_one(doc)
            async for t in db.opt_live.find({"status": {"$in": ["OPEN", "CLOSED"]}, "fill_checked": {"$ne": True}}):
                try:
                    if t.get("entry_ist") and t.get("legs") and now >= datetime.fromisoformat(t["entry_ist"]).replace(tzinfo=IST).timestamp() + 180:
                        await self.fill_check(t)
                        await db.opt_live.update_one({"_id": t["_id"]}, {"$set": {"legs": t["legs"], "real": t["real"], "fill_checked": True}})
                except Exception as e:
                    log.warning("option fill check %s: %s", t["_id"], e)
            async for t in db.opt_live.find({"status": "OPEN"}):
                d = date.fromisoformat(t["day"])
                try:
                    changed = False
                    if now < settle_t(d) and any(l.get("stop") and l["status"] == "OPEN" for l in t["legs"]):
                        changed = await self.check_stops(t, now)
                    if now >= settle_t(d) + 60:
                        changed = await self.settle(t) or changed
                    if changed:
                        await db.opt_live.replace_one({"_id": t["_id"]}, t)
                except Exception as e:
                    log.warning("option position %s: %s", t["_id"], e)


ENGINE = Engine()


async def compute():
    sizing = await get_sizing()
    raw = await backtest_rows()
    STATE["skips"] = {(k, r["day"]): r["skip"] for k in ORDER for r in raw.get(k, []) if r.get("skip")}
    rows = {k: price_seq(k, [r for r in raw.get(k, []) if not r.get("skip")], sizing) for k in ORDER}     # days the skip rules reject are not trades
    days = [doc["_id"] async for doc in db.opt_backtest.find({"_id": {"$gt": HISTORY_TO.isoformat()}}, {"_id": 1})]
    period_to = max(days + [HISTORY_TO.isoformat()])           # the last expiry checked (history file, then the server's own days)
    per, rows_b = {}, {}
    for k in ORDER:
        cap = capital_for(k, sizing)
        per[k] = {**tables(rows[k], cap, compounding(sizing)), "trades": sorted(rows[k], key=lambda r: r["entry_ist"], reverse=True),
                  "realdata": realdata_rows(k, rows[k], sizing, history().get("from", "2024-01-01"), period_to), "curve": curve(rows[k])}
        if k in ENABLED:                                       # journal B: the server's own run from 1 Oct 2026, starting again from the set capital
            raw_b = await server_rows(k)
            rows_b[k] = price_seq(k, [r for r in raw_b if not r.get("skip")], sizing)
            per[k]["B"] = {**tables(rows_b[k], cap, compounding(sizing)), "trades": sorted(rows_b[k], key=lambda r: r["entry_ist"], reverse=True), "curve": curve(rows_b[k]),
                           "from": B_FROM.isoformat(), "to": raw_b[-1]["day"] if raw_b else None, "days": len(raw_b),
                           "skipped": sum(1 for r in raw_b if r.get("skip")), "match": match_a(k, raw_b)}
    STATE["rows"], STATE["rows_b"] = rows, rows_b
    STATE.update(data={"backtest": per, "sizing": sizing}, computed_at=iso(now_utc()), error=None)


FILL = {"running": False, "done": 0, "total": 0, "failed": [], "at": None}
FILL_PAUSE = 0.3                                       # seconds between back-filled days: stay gentle with Delta's API
_BG = set()


def _trading_now():
    t = datetime.now(IST)
    return (16, 45) <= (t.hour, t.minute) < (17, 40)


async def fill_b():
    """Journal B back-fill: every expiry from 1 Oct 2026 to the end of the history file, re-run by the server on Delta's data.
    Runs in the background (the paper engine and journal A never wait for it), one day at a time; a day that fails is listed
    and tried again on the next pass; the journals are re-computed every 10 days added and at the end."""
    if FILL["running"]:
        return
    FILL.update(running=True, failed=[], at=iso(now_utc()))
    try:
        days, d = [], B_FROM
        while d <= HISTORY_TO:
            days.append(d)
            d += timedelta(days=1)
        have = {doc["_id"] async for doc in db.opt_backtest.find({"_id": {"$gte": B_FROM.isoformat(), "$lte": HISTORY_TO.isoformat()}}, {"_id": 1})}
        todo = [x for x in days if x.isoformat() not in have]
        FILL.update(total=len(days), done=len(days) - len(todo))
        added = 0
        for x in todo:
            if _trading_now():
                break                                  # 16:45-17:40 IST belongs to the paper trade; the next pass carries on
            try:
                if await backtest_day(x):
                    added += 1
                    FILL["done"] += 1
                    await asyncio.sleep(FILL_PAUSE)
                else:
                    FILL["failed"].append(x.isoformat())
            except Exception as e:
                FILL["failed"].append(x.isoformat())
                log.warning("option picks journal B %s: %s", x, e)
            if added and added % 10 == 0:
                await compute()
        if added:
            await compute()
    finally:
        FILL["running"] = False


async def loop():
    await asyncio.sleep(30)
    last_bt = 0.0
    while True:
        try:
            await ENGINE.tick()
        except Exception as e:
            STATE["error"] = str(e)[:200]
            log.exception("option picks tick")
        if time.time() - last_bt > 900 or STATE["data"] is None:
            try:
                if STATE["data"] is None:
                    await compute()                        # journal A at once after a restart
                today = datetime.now(IST).date()
                d = HISTORY_TO + timedelta(days=1)         # journal A: the server's own days after the history file
                while d <= today:
                    if time.time() >= settle_t(d) + 600:
                        try:
                            await backtest_day(d)
                        except Exception as e:             # one bad day never blocks the journals
                            log.warning("option picks backtest %s: %s", d, e)
                    d += timedelta(days=1)
                await compute()
            except Exception as e:
                STATE["error"] = str(e)[:200]
                log.exception("option picks backtest")
            last_bt = time.time()
            if not FILL["running"] and not _trading_now():
                t = asyncio.ensure_future(fill_b())        # journal B: older days in the background
                _BG.add(t)
                t.add_done_callback(_BG.discard)
        await asyncio.sleep(10)


def _strat_view(k, sizing):
    s = STRATS[k]
    return {"key": k, "pick": s["pick"], "code": s["code"], "name": s["name"], "enabled": s["enabled"], "verdict": s["verdict"],
            "entry_ist": "%02d:%02d" % s["entry"] + (" (day before)" if s["entry"] > (17, 30) else ""),
            "live_entry_ist": time.strftime("%H:%M", time.gmtime(s["entry"][0] * 3600 + s["entry"][1] * 60 + s["bar"])),
            "stop": "2x sell price (1-min mark high)" if s["kind"] == "stop" else "none (hold to 17:30 settlement)", "capital": capital_for(k, sizing),
            "recommended": RECOMMENDED["label"] if k == RECOMMENDED["key"] else None,
            "recommended_active": k == RECOMMENDED["key"] and compounding(sizing) == RECOMMENDED["compound"]}


async def dashboard():
    data = STATE.get("data") or {}
    sizing = STATE.get("sizing") or dict(SIZING_DEFAULT)
    raw_live = [{k: v for k, v in x.items() if k != "_id"} | {"id": x["_id"]} async for x in db.opt_live.find({"key": {"$in": ENABLED}}).sort("created", -1).limit(400)]
    live = []
    for k in ENABLED:            # paper trades priced in entry order: compounded sizing grows from the equity after each closed trade
        mine = [t for t in raw_live if t["key"] == k]
        tradable = [t for t in mine if t.get("legs") and t.get("price") and t["status"] in ("OPEN", "CLOSED")]
        live += price_seq(k, tradable, sizing)
        live += [{**t, "gross": None, "fees": None, "net": None, "qty": None, "capital": None, "equity": None} for t in mine if t not in tradable]
    live.sort(key=lambda t: t.get("created") or t["day"], reverse=True)
    bt = data.get("backtest") or {}
    btmap = {(k, r["day"]): r for k in ORDER for r in (bt.get(k) or {}).get("trades", [])}
    per_live = {k: stats([t for t in live if t["key"] == k and t["status"] == "CLOSED"], capital_for(k, sizing), compounding(sizing)) for k in ENABLED}
    cmp_rows = []
    for t in live:
        b = btmap.get((t["key"], t["day"]))
        bskip = (STATE.get("skips") or {}).get((t["key"], t["day"]))
        if b is not None:      # like for like: the backtest trade of that day at the paper trade's own size (compounded backtests grow far larger)
            eq = t.get("capital") or (per_live.get(t["key"]) or {}).get("final_equity") or capital_for(t["key"], sizing)
            b = price(b, scaled(t["key"], sizing, eq))
        cmp_rows.append({"day": t["day"], "key": t["key"], "live_status": t["status"], "live_reason": t.get("reason"), "live_net": t.get("net"),
                         "bt_status": "TAKEN" if b else "SKIPPED" if bskip else "not yet", "bt_net": b["net"] if b else None, "bt_reason": bskip})
    now = time.time()
    today = datetime.fromtimestamp(now, IST).date()
    nxt = {}
    for k in ENABLED:
        for d in (today, today + timedelta(days=1), today + timedelta(days=2)):
            if live_entry_t(k, d) > now:
                nxt[k] = datetime.fromtimestamp(live_entry_t(k, d), IST).strftime("%d %b %H:%M IST"); break
    notional = sizing["put_capital"] * sizing["put_leverage"] + sizing["call_capital"] * sizing["call_leverage"]
    return {"name": NAME, "computed_at": STATE.get("computed_at"), "error": STATE.get("error"), "paper_only": True,
            "strategies": [_strat_view(k, sizing) for k in ENABLED], "enabled": ENABLED,
            "backtest": {k: ({**bt[k], "trades": bt[k]["trades"][:100], "trades_total": len(bt[k]["trades"])} if bt.get(k) else None) for k in ENABLED},
            "b_from": B_FROM.isoformat(), "b_fill": {**FILL, "failed": FILL["failed"][:20]},
            "history_to": HISTORY_TO.isoformat(), "live": live, "live_stats": per_live, "compare": cmp_rows, "next_entry": nxt,
            "sizing": sizing, "size_label": size_label(sizing), "order_notional": notional, "not_checked": NOT_CHECKED,
            "compounded": compounding(sizing), "recommended": RECOMMENDED}


def trades_csv(key, period="A"):
    """All backtest trades of one strategy at the current size as CSV (newest first). period A = 1 Jan 2024 → today, B = the
    server's own run from 1 Oct 2026."""
    import csv
    import io
    src = STATE.get("rows_b" if period == "B" else "rows") or {}
    rows = sorted([r for r in src.get(key, []) if r.get("net") is not None], key=lambda r: r["entry_ist"], reverse=True)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Strategy", "Size", "Expiry day", "Entry (IST)", "BTC futures at entry", "Qty per leg (BTC)", "Settlement index", "Exit", "Legs (sell fill -> exit)",
                "Capital used ($)", "Gross P&L ($)", "Fees + GST ($)", "Net P&L ($)", "Equity after ($)", "Net with 5% worse fills + 2x fees ($)",
                "Real trades on all legs at fill", "Source"])
    lab = size_label(STATE.get("sizing") or SIZING_DEFAULT)
    for r in rows:
        legs = " ; ".join(f"{l['side']} {l['sym']} {l['qty']:.3f} BTC fill {l['fill']} -> {l['exit']} {l['exit_price']}" + (f" at {l['stop_ist']}" if l.get("stop_ist") else "")
                          for l in r.get("legs", []))
        w.writerow([STRATS[key]["code"], lab, r["day"], r["entry_ist"], r.get("price"), r.get("qty"), r.get("settle"), r.get("exit"), legs, r.get("capital"),
                    round(r["gross"], 4), round(r["fees"], 4), round(r["net"], 4), r.get("equity"),
                    round(r["net_v2"], 4) if r.get("net_v2") is not None else "", "Yes" if r.get("real") else "No",
                    r.get("source", "backtest file")])
    return buf.getvalue()
