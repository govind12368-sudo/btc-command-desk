"""BTC Scanner – a desk-style read of BTC in two time views (today / next 24 hours, and multi-day swing).

How a professional desk works, in order: read the data → call the day type → write the plan → fix the risk →
review afterwards. This module does the first three with free data and keeps a score card for the last one.

Every factor votes +1 (supports BUY), −1 (supports SELL) or 0, and shows its own reading, source and age.
Decision: BUY when the votes add up to +2 or more, SELL at −2 or less, otherwise WAIT.
This is decision support. Nothing here places an order, and the score card below it is the only proof of
whether its calls are any good – it starts empty and fills in hour by hour.

Data: Delta Exchange India candles/trades (day type, trend, order flow), the site's own pillars (OKX money
flow, funding / open interest / long-short, macro, six-factor), Deribit public options data (DVOL, skew,
put/call) and the CFTC weekly Traders in Financial Futures report for CME Bitcoin futures.
"""
import asyncio
import logging
from datetime import datetime, timezone

import httpx

from core import db, now_utc, iso
import daytype

log = logging.getLogger("scanner")
UA = {"User-Agent": "VantageBTC-scanner/1.0", "Accept": "application/json"}
EXT = {k: {"ok": False, "updated": None, "error": "not loaded yet", "data": None} for k in ("dvol", "options", "cftc")}
SOURCES = {"dvol": "Deribit DVOL (implied volatility index)", "options": "Deribit BTC options (open interest, IV)",
           "cftc": "CFTC Traders in Financial Futures – CME Bitcoin (weekly)"}
STATE = {"result": None}


async def _get(url, params=None):
    async with httpx.AsyncClient(timeout=25, headers=UA) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        return r.json()


async def _dvol():
    now = int(now_utc().timestamp() * 1000)
    rows = (await _get("https://www.deribit.com/api/v2/public/get_volatility_index_data",
                       {"currency": "BTC", "start_timestamp": now - 8 * 86400000, "end_timestamp": now, "resolution": "3600"}))["result"]["data"]
    closes = [float(r[4]) for r in rows]
    return {"now": closes[-1], "day_ago": closes[-25] if len(closes) > 25 else closes[0], "avg_7d": sum(closes) / len(closes)}


def parse_options(rows, now=None):
    """Put/call open-interest ratio and a 5%-out-of-the-money skew for the expiry nearest to 7 days away."""
    now = now or now_utc()
    spot = next((float(r["underlying_price"]) for r in rows if r.get("underlying_price")), None)
    put_oi = call_oi = 0.0
    by_exp = {}
    for r in rows:
        p = str(r.get("instrument_name", "")).split("-")
        if len(p) != 4 or p[3] not in ("P", "C"):
            continue
        oi = float(r.get("open_interest") or 0)
        if p[3] == "P":
            put_oi += oi
        else:
            call_oi += oi
        try:
            exp = datetime.strptime(p[1], "%d%b%y").replace(tzinfo=timezone.utc, hour=8)
            by_exp.setdefault(exp, []).append((float(p[2]), p[3], float(r.get("mark_iv") or 0)))
        except ValueError:
            continue
    out = {"spot": spot, "put_call_oi": put_oi / call_oi if call_oi else None, "skew": None, "expiry": None}
    exps = [e for e in by_exp if (e - now).total_seconds() > 2 * 86400]
    if spot and exps:
        e = min(exps, key=lambda x: abs((x - now).total_seconds() - 7 * 86400))
        puts = [(k, iv) for k, t, iv in by_exp[e] if t == "P" and iv > 0]
        calls = [(k, iv) for k, t, iv in by_exp[e] if t == "C" and iv > 0]
        if puts and calls:
            pk, piv = min(puts, key=lambda x: abs(x[0] - spot * 0.95))
            ck, civ = min(calls, key=lambda x: abs(x[0] - spot * 1.05))
            out.update(skew=piv - civ, expiry=e.strftime("%d %b"), put_strike=pk, call_strike=ck, put_iv=piv, call_iv=civ)
    return out


async def _options():
    return parse_options((await _get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency", {"currency": "BTC", "kind": "option"}))["result"])


async def _cftc():
    rows = await _get("https://publicreporting.cftc.gov/resource/gpe5-46if.json",
                      {"$where": "upper(market_and_exchange_names) like 'BITCOIN - CHICAGO MERCANTILE%'", "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": "2"})
    f = lambda r, k: float(r.get(k) or 0)
    net = lambda r, p: f(r, p + "_positions_long") - f(r, p + "_positions_short")
    cur, prev = rows[0], rows[1] if len(rows) > 1 else rows[0]
    return {"report_date": str(cur.get("report_date_as_yyyy_mm_dd", ""))[:10], "asset_mgr_net": net(cur, "asset_mgr"), "asset_mgr_net_prev": net(prev, "asset_mgr"),
            "lev_money_net": net(cur, "lev_money"), "lev_money_net_prev": net(prev, "lev_money"), "open_interest": f(cur, "open_interest_all")}


async def _refresh(name, fn):
    try:
        EXT[name] = {"ok": True, "updated": iso(now_utc()), "error": None, "data": await fn()}
    except Exception as e:
        EXT[name] = {**EXT[name], "ok": False, "error": str(e)[:160]}
        log.warning("scanner source %s failed: %s", name, e)


def _ema(vals, n):
    k, e = 2 / (n + 1), vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def decide(factors):
    s = sum(f["vote"] for f in factors)
    return {"score": s, "decision": "BUY" if s >= 2 else "SELL" if s <= -2 else "WAIT", "factors": factors,
            "for_buy": sum(1 for f in factors if f["vote"] > 0), "for_sell": sum(1 for f in factors if f["vote"] < 0), "neutral": sum(1 for f in factors if f["vote"] == 0)}


def compute(st):
    """st = engine.STATE (pillars, flow, six_factor). Returns the scanner result; pure apart from reading module state."""
    day = daytype.public()
    bars = daytype.STATE["bars"]
    intr, swing = [], []

    def add(lst, group, name, vote, reading, source, at=None):
        lst.append({"group": group, "name": name, "vote": int(vote), "reading": reading, "source": source, "at": at})

    # --- market structure
    t = day.get("type")
    add(intr, "Market structure", "Day type (last 12 hours)", 1 if t == "TREND_UP" else -1 if t == "TREND_DOWN" else 0,
        f"{day.get('label')}: {day.get('score', 0)} of 5 trend measures agree", "Delta 5-minute candles", day.get("at"))
    levels = {}
    if len(bars) > 600:
        hourly = [bars[i]["c"] for i in range(len(bars) - 1, -1, -12)][::-1]
        c, e20, e50 = hourly[-1], _ema(hourly[-120:], 20), _ema(hourly[-200:], 50)
        add(intr, "Market structure", "1-hour trend (EMA20 vs EMA50)", 1 if c > e20 > e50 else -1 if c < e20 < e50 else 0,
            f"price {c:,.0f}, EMA20 {e20:,.0f}, EMA50 {e50:,.0f}", "Delta 5-minute candles", day.get("at"))
        dr = daytype.daily_ranges(bars)
        days = sorted(dr)
        closes = [dr[d][4] for d in days]
        if len(closes) >= 21:
            ma20, wk = sum(closes[-21:-1]) / 20, closes[-8]
            add(swing, "Market structure", "Daily trend (20-day average, 7-day change)", 1 if c > ma20 and c > wk else -1 if c < ma20 and c < wk else 0,
                f"price {c:,.0f} vs 20-day average {ma20:,.0f}; 7 days ago {wk:,.0f} ({(c / wk - 1) * 100:+.1f}%)", "Delta 5-minute candles", day.get("at"))
        y = dr.get(days[-2]) if len(days) > 1 else None
        levels = {"high_12h": day.get("high"), "low_12h": day.get("low"), "vwap_12h": day.get("vwap"), "adr": day.get("adr"),
                  "today_open": dr[days[-1]][3], "yesterday_high": y[0] if y else None, "yesterday_low": y[1] if y else None}
    # --- order flow, leverage, macro: the site's own live pillars
    fl = st.get("flow") or {}
    if fl.get("valid"):
        im = fl.get("imbalance") or 0
        add(intr, "Order flow", "Delta buy vs sell volume (last 5 minutes)", 1 if im > 0.10 else -1 if im < -0.10 else 0,
            f"{fl.get('label', '')}: buyers {fl.get('buy_usd', 0):,.0f} USD, sellers {fl.get('sell_usd', 0):,.0f} USD ({im * 100:+.0f}%)", "Delta live trades", fl.get("window_end"))
    pil = st.get("pillars") or {}
    for key, lst, group, name in (("money_flow", intr, "Order flow", "Aggressive buying vs selling (OKX perpetual)"),
                                  ("positioning", intr, "Leverage", "Funding, open interest and long/short (OKX perpetual)"),
                                  ("macro", swing, "Macro and events", "US dollar and 2-year yield")):
        p = pil.get(key) or {}
        if p.get("valid"):
            add(lst, group, name, max(-1, min(1, round(p.get("score") or 0))), str(p.get("detail") or "")[:220], "site pillar", p.get("observed_at"))
    six = st.get("six_factor") or {}
    if six.get("complete"):
        add(swing, "Site model", "Six-factor direction", 1 if six.get("direction") == "LONG" else -1 if six.get("direction") == "SHORT" else 0,
            f"{six.get('direction')} ({six.get('strength')}, score {six.get('score', 0):+.2f})", "site model", st.get("computed_at"))
        ff = (six.get("contributions") or {}).get("four_factor")
        if ff is not None:
            add(swing, "Institutional money", "Four-factor (ETF flows, sentiment, macro, technical)", 1 if ff > 0.02 else -1 if ff < -0.02 else 0, f"contribution {ff:+.2f}", "site model", st.get("computed_at"))
    # --- options (Deribit)
    o, dv = EXT["options"], EXT["dvol"]
    if o["ok"] and o["data"].get("skew") is not None:
        d = o["data"]
        sk = d["skew"]
        extra = f"; DVOL {dv['data']['now']:.1f} (24h ago {dv['data']['day_ago']:.1f}, 7-day average {dv['data']['avg_7d']:.1f})" if dv["ok"] else ""
        add(intr, "Options", f"Put vs call price, {d['expiry']} expiry", -1 if sk > 3 else 1 if sk < -3 else 0,
            f"5% lower put IV {d['put_iv']:.1f} vs 5% higher call IV {d['call_iv']:.1f} (skew {sk:+.1f}){extra}", SOURCES["options"], o["updated"])
        pc = d.get("put_call_oi")
        if pc:
            add(swing, "Options", "Put/call open interest", -1 if pc > 1.0 else 1 if pc < 0.6 else 0, f"put/call ratio {pc:.2f} across all expiries", SOURCES["options"], o["updated"])
    cf = EXT["cftc"]
    if cf["ok"]:
        d = cf["data"]
        ch = d["asset_mgr_net"] - d["asset_mgr_net_prev"]
        add(swing, "Institutional money", "CME asset managers, change in net position", 1 if ch > 0 else -1 if ch < 0 else 0,
            f"net {d['asset_mgr_net']:+,.0f} contracts ({ch:+,.0f} on the week); leveraged funds net {d['lev_money_net']:+,.0f} (mostly hedges) – report of {d['report_date']}", SOURCES["cftc"], cf["updated"])
    plan = {"TREND_UP": "Trend up: trade with it – follow LONG signals, ignore SHORT signals.",
            "TREND_DOWN": "Trend down: trade with it – follow SHORT signals, ignore LONG signals.",
            "RANGE": "Range: fade the moves – Plan A (reverse the signal).",
            "MIXED": "Unclear: stand aside until the picture is one or the other.", "NA": "Waiting for candle data."}[t or "NA"]
    return {"computed_at": iso(now_utc()), "day_type": day, "playbook": plan, "intraday": decide(intr), "swing": decide(swing), "levels": levels,
            "sources": [{"name": "Delta Exchange India candles", "ok": bool(bars) and not daytype.STATE["error"], "updated": daytype.STATE["updated"], "error": daytype.STATE["error"]}] +
                       [{"name": SOURCES[k], "ok": v["ok"], "updated": v["updated"], "error": v["error"]} for k, v in EXT.items()]}


async def scorecard():
    """Were past calls right? BUY/SELL is right when price is higher/lower 24 hours (today view) or 5 days (swing view) later."""
    logs = {d["_id"]: d async for d in db.scanner_log.find().sort("_id", -1).limit(24 * 60)}
    out = {}
    for view, ahead in (("intraday", 86400), ("swing", 5 * 86400)):
        n = ok = 0
        for t, d in logs.items():
            later = logs.get(t + ahead)
            if not later or d.get(view) not in ("BUY", "SELL"):
                continue
            n += 1
            ok += (later["price"] > d["price"]) == (d[view] == "BUY")
        out[view] = {"checked": n, "right": ok, "pct": round(100 * ok / n) if n else None}
    tr = [d async for d in db.daytype_log.find({"type": {"$in": ["TREND_UP", "TREND_DOWN"]}}).sort("_id", -1).limit(2000)]
    n = ok = 0
    for d in tr:
        later = await db.daytype_log.find_one({"_id": d["_id"] + 6 * 3600})
        if later:
            n += 1
            ok += (later["price"] > d["price"]) == (d["type"] == "TREND_UP")
    out["trend_continued_6h"] = {"checked": n, "right": ok, "pct": round(100 * ok / n) if n else None}
    out["hours_logged"] = len(logs)
    return out


async def public(st):
    res = STATE["result"] or compute(st)
    return {**res, "scorecard": await scorecard(), "evidence": daytype.evidence()}


async def loop(get_state):
    n = 0
    while True:
        try:
            if n % 10 == 0:
                await asyncio.gather(_refresh("dvol", _dvol), _refresh("options", _options))
            if n % 360 == 0:
                await _refresh("cftc", _cftc)
            res = STATE["result"] = compute(get_state())
            price = res["day_type"].get("price")
            if price:
                now = int(now_utc().timestamp())
                await db.scanner_log.update_one({"_id": now - now % 3600}, {"$setOnInsert": {"price": price, "intraday": res["intraday"]["decision"], "swing": res["swing"]["decision"],
                                                                                           "day_type": res["day_type"]["type"]}}, upsert=True)
        except Exception as e:
            log.warning("scanner loop failed: %s", e)
        n += 1
        await asyncio.sleep(60)
