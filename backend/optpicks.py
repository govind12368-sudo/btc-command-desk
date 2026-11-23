"""Option Picks: the owner's BTC daily-option selling picks on Delta Exchange India (paper only, owner request 23 Nov 2026).

Cross-checked first on Delta's own data (lab: research/opt_verify, the owner's workbook "BTC_Option_Picks_Crosscheck"):
  IFLY  pick 2  BTC-IFLY-ATM2-1715-NS       iron fly: sell ATM put + call, buy put ~spot-2% and call ~spot+2%, 17:15 IST, hold   profitable, fragile
  SP16  pick 4  BTC-SP-ATM-1600-SL2X-F20    short ATM put (strike nearest BTC futures) 16:00, stop 2x on 1-min mark high      profitable, fragile
  SSTR  pick 5  BTC-SSTR-ATM-1700-SL2X-F20  short ATM straddle 17:00, each leg stop 2x                                       CONFIRMED
  BPS   pick 1  BTC-BPS-S2B5-2130-NS        bull put spread 2%/5%, 21:30 the day before                                     NOT confirmed (kept in code, off)
  BCS   pick 3  BTC-BCS-S2B5-2130-NS        bear call spread 2%/5%, 21:30 the day before                                    NOT confirmed (kept in code, off)

Rules as written in the owner's workbooks. Daily expiry 17:30 IST (12:00 UTC). Entry value = the candle that OPENS at the entry
time (5-minute bars for IFLY/BPS/BCS, 1-minute for SP16/SSTR), which is how the owner's 5 PM workbook was built (matched on all 389
days), so the live paper entry is at the end of that bar (IFLY 17:20, SP16 16:01, SSTR 17:01). Strike = nearest listed strike to the
target (spot = BTC index .DEXBTUSD for IFLY/BPS/BCS, BTC futures BTCUSD for SP16/SSTR). SELL at mark - max($0.10, 1%), BUY at
mark + max($0.10, 1%) (rounded to $0.1 for SP16/SSTR). Size floor($10,000 / price / 0.001) x 0.001 BTC per leg ($1,000 at 10x).
Fees: each expiry's Delta fee record (taker rate on notional, capped at a share of the premium) + 18% GST at entry, on a stop
buy-back, and at settlement (settlement fee, capped on the settlement value, so worthless legs pay none). SP16/SSTR: stop when a
1-minute mark high reaches 2x the sell price, fill at stop + max($0.10, 1%) rounded up; skip the day if the put mark < $20 or BTC
futures are up > 2% since 17:31 IST yesterday. Everything else settles at Delta's official settlement index.
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
CAPITAL, LEVERAGE, NOTIONAL = 1000.0, 10, 10000.0
GST = 0.18
STRATS = {
    "IFLY": {"pick": 2, "id": 1995, "code": "BTC-IFLY-ATM2-1715-NS", "name": "Iron fly, ATM body, wings ±2%", "kind": "hold", "ref": "index", "entry": (17, 15), "bar": 300,
             "legs": [(-1, "P", 1.0), (-1, "C", 1.0), (1, "P", 0.98), (1, "C", 1.02)], "enabled": True, "grade": "caution",
             "verdict": "Profitable on real data but smaller than the workbook; loses with 5% worse fills + 2x fees; real trades at the fill price only 1 day in 4"},
    "SP16": {"pick": 4, "id": None, "code": "BTC-SP-ATM-1600-SL2X-F20", "name": "Short ATM put 16:00, stop 2x", "kind": "stop", "ref": "futures", "entry": (16, 0), "bar": 60,
             "typs": "P", "enabled": True, "grade": "caution",
             "verdict": "Profitable on real data, about 25% smaller than the workbook; loses with 5% worse fills + 2x fees"},
    "SSTR": {"pick": 5, "id": None, "code": "BTC-SSTR-ATM-1700-SL2X-F20", "name": "Short ATM straddle 17:00, stop 2x each leg", "kind": "stop", "ref": "futures", "entry": (17, 0),
             "bar": 60, "typs": "PC", "enabled": True, "grade": "pass",
             "verdict": "Confirmed: matches the workbook (same max drawdown and worst trade); profit in 2024, 2025 and 2026"},
    "BPS": {"pick": 1, "id": 1525, "code": "BTC-BPS-S2B5-2130-NS", "name": "Bull put spread sell 2% / buy 5%", "kind": "hold", "ref": "index", "entry": (21, 30), "bar": 300,
            "legs": [(-1, "P", 0.98), (1, "P", 0.95)], "enabled": False, "grade": "fail",
            "verdict": "Not confirmed: +$365 in 3 years instead of +$5,254, the $1,000 account fell to $73, loss in 2025"},
    "BCS": {"pick": 3, "id": 1565, "code": "BTC-BCS-S2B5-2130-NS", "name": "Bear call spread sell 2% / buy 5%", "kind": "hold", "ref": "index", "entry": (21, 30), "bar": 300,
            "legs": [(-1, "C", 1.02), (1, "C", 1.05)], "enabled": False, "grade": "fail",
            "verdict": "Not confirmed: a $574 loss instead of +$3,758; the $1,000 account fell below $0"},
}
ORDER = ["IFLY", "SP16", "SSTR", "BPS", "BCS"]
ENABLED = [k for k in ORDER if STRATS[k]["enabled"]]
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "optpicks_history.json")
HISTORY_TO = date(2026, 11, 22)                    # the history file covers expiries up to this day; the server backtests later days
STATE = {"computed_at": None, "error": None, "data": None}
_HIST = {}


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


def qty_for(px):
    return math.floor(NOTIONAL / px / 0.001 + 1e-9) * 0.001


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


def build_trade(key, D):
    """The backtest trade of one strategy on one expiry day from fetched data D (see needs()). Returns (trade, reason)."""
    s, prods, settle, day = STRATS[key], D["prods"], D["settle"], D["day"]
    st, et = settle_t(day), entry_t(key, day)
    out = {"key": key, "day": day.isoformat(), "entry_ist": datetime.fromtimestamp(et, IST).strftime("%Y-%m-%d %H:%M"), "settle": settle, "legs": []}
    if s["kind"] == "hold":
        spot = at(D["index"], et)
        if spot is None:
            return None, "no BTC index at entry"
        legs, why = pick_legs(key, prods, spot, et)
        if not legs:
            return None, why
        qty = qty_for(spot)
        out.update(price=spot, qty=round(qty, 3))
        near = et > st - 43200
        gross = fees = gross2 = fees2 = 0.0
        real_all = True
        for side, sym in legs:
            p = prods[sym]
            mark = at(D["m5"].get(sym, {}), et)
            if mark is None:
                return None, f"no mark for {sym} at entry"
            px = mark - max(0.10, 0.01 * mark) if side < 0 else mark + max(0.10, 0.01 * mark)
            px2 = px * 0.95 if side < 0 else px * 1.05
            intr = intrinsic(p, settle)
            g = (px - intr) * qty if side < 0 else (intr - px) * qty
            g2 = (px2 - intr) * qty if side < 0 else (intr - px2) * qty
            f = fee(px, qty, spot, p, day) + fee(intr, qty, settle, p, day)
            gross += g; fees += f; gross2 += g2; fees2 += 2 * (fee(px2, qty, spot, p, day) + fee(intr, qty, settle, p, day))
            w = (et - 120, et + 120) if near else (et - 600, et + 600)
            ok, vol = real_print((D["t1"] if near else D["t5"]).get(sym), *w, side, px)
            real_all &= ok
            out["legs"].append({"side": "SELL" if side < 0 else "BUY", "sym": sym, "k": p["k"], "mark": round(mark, 4), "fill": round(px, 4), "exit": "Settle",
                                "exit_price": round(intr, 4), "gross": round(g, 4), "fee": round(f, 4), "real": ok, "vol": vol})
        out.update(gross=gross, fees=fees, net=gross - fees, net_v2=gross2 - fees2, exit="Settle", real=real_all, skip=None)
        return out, None
    # short ATM leg(s) with a 2x stop and the two skip rules
    f = at(D["fut"], et)
    fprev = at(D["futp"], st - 86400 + 60)
    if f is None:
        return None, "no futures price at entry"
    chg = f / fprev - 1 if fprev else None
    legs, why = pick_legs(key, prods, f, et)
    if not legs:
        return None, why
    qty = qty_for(f)
    marks = {}
    for _, sym in legs:
        m = at(D["m1"].get(sym, {}), et)
        if m is None:
            return None, f"no mark for {sym} at entry"
        marks[sym] = m
    put = next((marks[sym] for _, sym in legs if prods[sym]["type"] == "P"), None)
    skip = "put mark below $20" if put is not None and put < 20 else "BTC up more than 2% since 17:31 IST yesterday" if chg is not None and chg > 0.02 else None
    out.update(price=f, qty=round(qty, 3), chg_1731=chg, skip=skip)
    gross = fees = gross2 = fees2 = 0.0
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
        intr = intrinsic(p, settle)
        if ex is not None:
            g, fe = (sell - ex) * qty, fee(sell, qty, f, p, day) + fee(ex, qty, f, p, day)
            g2, fe2 = (sell * 0.95 - ex * 1.05) * qty, 2 * (fee(sell * 0.95, qty, f, p, day) + fee(ex * 1.05, qty, f, p, day))
        else:
            g, fe = (sell - intr) * qty, fee(sell, qty, f, p, day) + fee(intr, qty, settle, p, day)
            g2, fe2 = (sell * 0.95 - intr) * qty, 2 * (fee(sell * 0.95, qty, f, p, day) + fee(intr, qty, settle, p, day))
        gross += g; fees += fe; gross2 += g2; fees2 += fe2
        ok, vol = real_print(D["t1"].get(sym), et - 120, et + 120, -1, sell)
        real_all &= ok
        exits.append("Stop" if ex is not None else "Settle")
        out["legs"].append({"side": "SELL", "sym": sym, "k": p["k"], "mark": round(m, 4), "fill": sell, "stop": round(stop, 2), "exit": exits[-1],
                            "exit_price": ex if ex is not None else round(intr, 4), "stop_ist": datetime.fromtimestamp(ex_t + 60, IST).strftime("%H:%M") if ex_t else None,
                            "gross": round(g, 4), "fee": round(fe, 4), "real": ok, "vol": vol})
    out.update(gross=gross, fees=fees, net=gross - fees, net_v2=gross2 - fees2, exit="/".join(exits), real=real_all)
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


def stats(rows):
    rows = sorted(rows, key=lambda r: r["entry_ist"])
    n = len(rows)
    if not n:
        return {"trades": 0, "wins": 0, "win_rate": None, "gross": 0.0, "fees": 0.0, "net": 0.0, "max_dd": 0.0, "worst": None, "best": None, "view2_net": 0.0,
                "real_fill": None, "lowest_balance": CAPITAL}
    eq = pk = low = CAPITAL
    dd = 0.0
    for r in rows:
        eq += r["net"]; pk = max(pk, eq); dd = max(dd, pk - eq); low = min(low, eq)
    w = sum(r["net"] > 0 for r in rows)
    return {"trades": n, "wins": w, "win_rate": w / n, "gross": round(sum(r["gross"] for r in rows), 2), "fees": round(sum(r["fees"] for r in rows), 2),
            "net": round(sum(r["net"] for r in rows), 2), "max_dd": round(dd, 2), "worst": round(min(r["net"] for r in rows), 2), "best": round(max(r["net"] for r in rows), 2),
            "view2_net": round(sum(r.get("net_v2", r["net"]) for r in rows), 2), "real_fill": sum(1 for r in rows if r.get("real")) / n, "lowest_balance": round(low, 2)}


def tables(rows):
    rows = sorted(rows, key=lambda r: r["entry_ist"])
    by_y, by_m = defaultdict(list), defaultdict(list)
    for r in rows:
        by_y[r["day"][:4]].append(r); by_m[r["day"][:7]].append(r)
    cum, months = 0.0, []
    for m in sorted(by_m):
        s = stats(by_m[m]); cum += s["net"]
        months.append({"month": m, **{k: s[k] for k in ("trades", "wins", "win_rate", "gross", "fees", "net")}, "cumulative": round(cum, 2)})
    cum, years = 0.0, []
    for y in sorted(by_y):
        s = stats(by_y[y]); cum += s["net"]
        years.append({"year": y, **{k: s[k] for k in ("trades", "wins", "win_rate", "gross", "fees", "net", "max_dd")}, "cumulative": round(cum, 2)})
    tot = stats(rows)
    years.append({"year": "Total till date", **{k: tot[k] for k in ("trades", "wins", "win_rate", "gross", "fees", "net", "max_dd")}, "cumulative": tot["net"]})
    return {"stats": tot, "yearly": years, "monthly": months}


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
    """Backtest one finished expiry day on the server (same code as the history file) and store it."""
    if await db.opt_backtest.find_one({"_id": d.isoformat()}, {"_id": 1}):
        return
    D = await fetch_day(d, ORDER)
    if not D["settle"]:
        return                                        # not settled yet: try again later
    doc = {"_id": d.isoformat(), "at": iso(now_utc()), "trades": {}, "skips": {}}
    for key in ORDER:
        try:
            t, why = build_trade(key, D)
        except Exception as e:
            t, why = None, f"error: {str(e)[:120]}"
        if t:
            doc["trades"][key] = t
        else:
            doc["skips"][key] = why
    await db.opt_backtest.insert_one(doc)


async def backtest_rows():
    hist = history().get("trades", {})
    rows = {k: [r for r in hist.get(k, [])] for k in ORDER}
    async for doc in db.opt_backtest.find({"_id": {"$gt": HISTORY_TO.isoformat()}}).sort("_id", 1):
        for k, t in doc["trades"].items():
            rows.setdefault(k, []).append({**t, "source": "server"})
    return rows


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
        qty = qty_for(price)
        doc.update(price=price, qty=round(qty, 3), ref=s["ref"])
        if s["kind"] == "stop":
            fp = await self.fut_1731(d)
            chg = price / fp - 1 if fp else None
            put = next((ch[sym]["mark"] for _, sym in legs if sym.startswith("P-")), None)
            doc["chg_1731"] = chg
            if put is not None and put < 20:
                doc.update(status="SKIPPED", reason=f"put mark ${put:.2f} below $20")
            elif chg is not None and chg > 0.02:
                doc.update(status="SKIPPED", reason=f"BTC up {chg:+.2%} since 17:31 IST yesterday")
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
                                "entry_fee": fee(px, qty, price, {"taker": info[0], "cap": info[1]}, d), "high": m})
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
                leg.update(status="STOPPED", exit="Stop", exit_price=ex, exit_ist=datetime.fromtimestamp((hit or now) + 60, IST).strftime("%H:%M"),
                           exit_fee=fee(ex, t["qty"], t["price"], leg, d))
                changed = True
        return changed

    async def settle(self, t):
        d = date.fromisoformat(t["day"])
        prods = await products(d)
        settle = next((p["settle"] for p in prods.values() if p["settle"]), None)
        if not settle:
            return False
        gross = fees = 0.0
        for leg in t["legs"]:
            sd = -1 if leg["side"] == "SELL" else 1
            if leg["status"] == "OPEN":
                intr = intrinsic(leg, settle)
                leg.update(status="SETTLED", exit="Settle", exit_price=round(intr, 4), exit_fee=fee(intr, t["qty"], settle, leg, d))
            g = (leg["fill"] - leg["exit_price"]) * t["qty"] if sd < 0 else (leg["exit_price"] - leg["fill"]) * t["qty"]
            leg["gross"] = round(g, 4)
            gross += g; fees += leg["entry_fee"] + leg.get("exit_fee", 0.0)
        t.update(status="CLOSED", settle=settle, gross=gross, fees=fees, net=gross - fees, closed=iso(now_utc()),
                 exit="/".join(l["exit"] for l in t["legs"]))
        return True

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
    rows = await backtest_rows()
    per = {}
    for k in ORDER:
        tb = tables(rows.get(k, []))
        per[k] = {**tb, "trades": sorted(rows.get(k, []), key=lambda r: r["entry_ist"], reverse=True)}
    STATE["rows"] = rows
    STATE.update(data={"backtest": per}, computed_at=iso(now_utc()), error=None)


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
                today = datetime.now(IST).date()
                start = HISTORY_TO + timedelta(days=1)
                d = start
                while d <= today:
                    if time.time() >= settle_t(d) + 600:
                        await backtest_day(d)
                    d += timedelta(days=1)
                await compute()
            except Exception as e:
                STATE["error"] = str(e)[:200]
                log.exception("option picks backtest")
            last_bt = time.time()
        await asyncio.sleep(10)


def _strat_view(k):
    s = STRATS[k]
    return {"key": k, "pick": s["pick"], "code": s["code"], "name": s["name"], "enabled": s["enabled"], "verdict": s["verdict"], "grade": s["grade"],
            "entry_ist": "%02d:%02d" % s["entry"] + (" (day before)" if s["entry"] > (17, 30) else ""), "live_entry_ist": time.strftime("%H:%M", time.gmtime(
                (s["entry"][0] * 3600 + s["entry"][1] * 60 + s["bar"]))), "stop": "2x sell price (1-min mark high)" if s["kind"] == "stop" else "none (hold to 17:30 settlement)"}


async def dashboard():
    data = STATE.get("data") or {}
    live = [{k: v for k, v in x.items() if k != "_id"} | {"id": x["_id"]} async for x in db.opt_live.find().sort("created", -1).limit(400)]
    bt = data.get("backtest") or {}
    btmap = {(k, r["day"]): r for k in ORDER for r in (bt.get(k) or {}).get("trades", [])}
    cmp_rows = []
    for t in live:
        b = btmap.get((t["key"], t["day"]))
        cmp_rows.append({"day": t["day"], "key": t["key"], "live_status": t["status"], "live_reason": t.get("reason"), "live_net": t.get("net"),
                         "bt_status": ("SKIPPED" if b.get("skip") else "TAKEN") if b else "not yet", "bt_net": None if not b or b.get("skip") else b["net"],
                         "bt_reason": b.get("skip") if b else None})
    live_closed = [t for t in live if t["status"] == "CLOSED"]
    per_live = {k: stats([{**t, "entry_ist": t["entry_ist"]} for t in live_closed if t["key"] == k]) for k in ENABLED}
    now = time.time()
    today = datetime.fromtimestamp(now, IST).date()
    nxt = {}
    for k in ENABLED:
        for d in (today, today + timedelta(days=1), today + timedelta(days=2)):
            if live_entry_t(k, d) > now:
                nxt[k] = datetime.fromtimestamp(live_entry_t(k, d), IST).strftime("%d %b %H:%M IST"); break
    return {"name": NAME, "computed_at": STATE.get("computed_at"), "error": STATE.get("error"), "paper_only": True,
            "strategies": [_strat_view(k) for k in ORDER], "enabled": ENABLED, "check": history().get("check", {}),
            "backtest": {k: ({**bt[k], "trades": bt[k]["trades"][:100], "trades_total": len(bt[k]["trades"])} if bt.get(k) else None) for k in ENABLED}, "history_to": HISTORY_TO.isoformat(), "live": live, "live_stats": per_live, "compare": cmp_rows, "next_entry": nxt,
            "settings": {"capital": CAPITAL, "leverage": LEVERAGE, "notional": NOTIONAL}}


def trades_csv(key):
    """All backtest trades of one strategy as CSV (newest first)."""
    import csv
    import io
    rows = sorted((STATE.get("rows") or {}).get(key, []), key=lambda r: r["entry_ist"], reverse=True)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Strategy", "Expiry day", "Entry (IST)", "BTC price used", "Qty (BTC per leg)", "Settlement index", "Exit", "Legs", "Gross P&L ($)", "Fees + GST ($)",
                "Net P&L ($)", "Net with 5% worse fills + 2x fees ($)", "Real trades on all legs at fill", "Source"])
    for r in rows:
        legs = " ; ".join(f"{l['side']} {l['sym']} fill {l['fill']} -> {l['exit']} {l['exit_price']}" for l in r.get("legs", []))
        w.writerow([STRATS[key]["code"], r["day"], r["entry_ist"], r.get("price"), r.get("qty"), r.get("settle"), r.get("exit"), legs, round(r["gross"], 4),
                    round(r["fees"], 4), round(r["net"], 4), round(r.get("net_v2", r["net"]), 4), "Yes" if r.get("real") else "No", r.get("source", "history file")])
    return buf.getvalue()
