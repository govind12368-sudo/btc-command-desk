"""Market Intelligence – phase 1: price and technicals on 7 timeframes, spot pressure, derivatives across
exchanges, exchange-reported liquidations and correlations.

Monitoring and decision support only: nothing here places an order.

Rules this module keeps:
  * Every number carries its source and the time it was last updated.
  * A source that fails is shown as unavailable; nothing is estimated or filled in for it.
  * Each source has its own worker with retry and back-off, so one bad provider never stops the others.
  * Estimates are never mixed with exchange-reported facts (liquidations here are exchange-reported only).
All sources are free public endpoints; the ones a provider blocks from this server simply show "Unavailable".
"""
import asyncio
import json
import logging
import math
from collections import deque

import httpx

from core import db, now_utc, iso
import ta

log = logging.getLogger("intel")
UA = {"User-Agent": "Mozilla/5.0 (compatible; VantageBTC-intel/1.0)", "Accept": "application/json,text/csv,*/*"}
DELTA = "https://api.india.delta.exchange"
TF = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}
BARS = 500
LARGE_LIQ_USD = 250_000

STATE = {"candles": {}, "tf": {}, "overview": {}, "levels": {}, "pressure": None, "deriv": None, "corr": None}
SRC = {}        # name -> {label, module, ok, updated, error, fails, note}
LIQS = deque()  # exchange-reported liquidation events of the last 24 hours
_seen = set()
_client = None


def client():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=15, headers=UA, follow_redirects=True)
    return _client


async def get(url, params=None):
    r = await client().get(url, params=params)
    if r.status_code == 429:
        raise RuntimeError("rate limited by the provider (HTTP 429)")
    if r.status_code in (401, 403, 451):
        raise RuntimeError(f"provider refused this server (HTTP {r.status_code})")
    r.raise_for_status()
    return r


def reg(name, label, module, note=""):
    SRC[name] = {"label": label, "module": module, "ok": None, "updated": None, "error": None, "fails": 0, "note": note}


def mark(name, ok=True, error=None):
    s = SRC[name]
    s["ok"] = ok
    if ok:
        s.update(updated=iso(now_utc()), error=None, fails=0)
    else:
        s.update(error=str(error)[:180], fails=s["fails"] + 1)


async def worker(name, fn, every):
    """Run one source forever: retry with back-off after a failure, never die."""
    while True:
        try:
            await fn()
            mark(name)
            wait = every
        except asyncio.CancelledError:
            raise
        except Exception as e:
            mark(name, False, e)
            wait = min(every * 2 ** min(SRC[name]["fails"], 4), 900)
            log.warning("intel source %s failed: %s", name, e)
        await asyncio.sleep(wait)


def fnum(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ candles + technical engine
async def load_tf(tf):
    sec = TF[tf]
    now = int(now_utc().timestamp())
    r = (await get(f"{DELTA}/v2/history/candles", {"symbol": "BTCUSD", "resolution": tf, "start": now - BARS * sec, "end": now + sec})).json()
    rows = {int(c["time"]): {"t": int(c["time"]), "o": float(c["open"]), "h": float(c["high"]), "l": float(c["low"]), "c": float(c["close"]), "v": float(c.get("volume") or 0)} for c in r.get("result") or []}
    bars = [rows[t] for t in sorted(rows)]
    if len(bars) < 10:
        raise RuntimeError(f"only {len(bars)} candles returned for {tf}")
    done = [b for b in bars if b["t"] + sec <= now]
    gaps = sum(1 for a, b in zip(done, done[1:]) if b["t"] - a["t"] != sec)
    STATE["candles"][tf] = bars
    a = ta.analyze(done, sec)
    a.update(timeframe=tf, gaps=gaps, updated=iso(now_utc()), source="Delta Exchange India BTCUSD perpetual")
    STATE["tf"][tf] = a
    if tf == "1d":
        _levels(done)


def _levels(d):
    """Previous day / week high and low from completed daily candles (UTC days, Monday-start weeks)."""
    if len(d) < 8:
        return
    y = d[-1]
    wk = lambda b: (b["t"] // 86400 + 3) // 7
    this = wk({"t": y["t"] + 86400})
    prev = [b for b in d if wk(b) == this - 1]
    STATE["levels"] = {"prev_day_high": y["h"], "prev_day_low": y["l"], "prev_day_close": y["c"],
                       "prev_week_high": max(b["h"] for b in prev) if prev else None, "prev_week_low": min(b["l"] for b in prev) if prev else None,
                       "updated": iso(now_utc()), "source": "Delta daily candles (UTC days)"}


def _group(tfs):
    async def run():
        errs = []
        for tf in tfs:
            try:
                await load_tf(tf)
            except Exception as e:
                errs.append(f"{tf}: {e}")
        if errs:
            raise RuntimeError("; ".join(errs)[:170])
    return run


# ------------------------------------------------------------------ overview
async def load_ticker():
    r = (await get(f"{DELTA}/v2/tickers/BTCUSD")).json()["result"]
    STATE["overview"]["delta"] = {"price": fnum(r.get("close")), "mark": fnum(r.get("mark_price")), "index": fnum(r.get("spot_price")), "high_24h": fnum(r.get("high")), "low_24h": fnum(r.get("low")),
                                  "open_24h": fnum(r.get("open")), "turnover_24h_usd": fnum(r.get("turnover_usd")), "updated": iso(now_utc()), "source": "Delta Exchange India ticker"}


async def load_global():
    r = (await get("https://api.coingecko.com/api/v3/simple/price", {"ids": "bitcoin", "vs_currencies": "usd", "include_market_cap": "true", "include_24hr_vol": "true", "include_24hr_change": "true", "include_last_updated_at": "true"})).json()["bitcoin"]
    STATE["overview"]["global"] = {"price": fnum(r.get("usd")), "market_cap": fnum(r.get("usd_market_cap")), "volume_24h": fnum(r.get("usd_24h_vol")), "change_24h_pct": fnum(r.get("usd_24h_change")),
                                   "as_of": r.get("last_updated_at"), "updated": iso(now_utc()), "source": "CoinGecko (all exchanges)"}


# ------------------------------------------------------------------ spot buying vs selling pressure
async def load_pressure():
    """Taker (aggressive) buy vs sell volume on Binance spot BTCUSDT, from 1m and 5m candles."""
    out = {}
    try:
        k1 = (await get("https://data-api.binance.vision/api/v3/klines", {"symbol": "BTCUSDT", "interval": "1m", "limit": 61})).json()[:-1]
        k5 = (await get("https://data-api.binance.vision/api/v3/klines", {"symbol": "BTCUSDT", "interval": "5m", "limit": 289})).json()[:-1]

        def win(rows, n):
            w = rows[-n:]
            tot, buy = sum(float(x[7]) for x in w), sum(float(x[10]) for x in w)
            return {"buy_usd": buy, "sell_usd": tot - buy, "buy_share": buy / tot if tot else None}
        out = {"5m": win(k1, 5), "15m": win(k1, 15), "1h": win(k1, 60), "4h": win(k5, 48), "24h": win(k5, 288)}
        src = "Binance spot BTCUSDT (taker volume)"
    except Exception as e:
        tr = (await get("https://api.exchange.coinbase.com/products/BTC-USD/trades", {"limit": 1000})).json()
        buy = sum(float(t["price"]) * float(t["size"]) for t in tr if t["side"] == "sell")   # Coinbase reports the maker side
        sell = sum(float(t["price"]) * float(t["size"]) for t in tr if t["side"] == "buy")
        out = {"recent": {"buy_usd": buy, "sell_usd": sell, "buy_share": buy / (buy + sell) if buy + sell else None, "trades": len(tr), "from": tr[-1]["time"], "to": tr[0]["time"]}}
        src = f"Coinbase BTC-USD last {len(tr)} trades (Binance unavailable: {str(e)[:60]})"
    for w in out.values():
        s = w.get("buy_share")
        w["label"] = "—" if s is None else "BUYERS" if s >= 0.53 else "SELLERS" if s <= 0.47 else "BALANCED"
    STATE["pressure"] = {"windows": out, "updated": iso(now_utc()), "source": src}


# ------------------------------------------------------------------ derivatives across exchanges
async def _binance():
    p = (await get("https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": "BTCUSDT"})).json()
    oi = (await get("https://fapi.binance.com/fapi/v1/openInterest", {"symbol": "BTCUSDT"})).json()
    t = (await get("https://fapi.binance.com/fapi/v1/ticker/24hr", {"symbol": "BTCUSDT"})).json()
    mark = float(p["markPrice"])
    out = {"venue": "Binance BTCUSDT", "mark": mark, "index": float(p["indexPrice"]), "funding_8h": float(p["lastFundingRate"]), "next_funding": int(p["nextFundingTime"]) // 1000,
           "oi_usd": float(oi["openInterest"]) * mark, "volume_24h_usd": float(t["quoteVolume"])}
    try:
        ls = (await get("https://fapi.binance.com/futures/data/globalLongShortAccountRatio", {"symbol": "BTCUSDT", "period": "5m", "limit": 1})).json()[0]
        out["long_share"] = float(ls["longAccount"])
    except Exception:
        pass
    return out


async def _bybit():
    t = (await get("https://api.bybit.com/v5/market/tickers", {"category": "linear", "symbol": "BTCUSDT"})).json()["result"]["list"][0]
    out = {"venue": "Bybit BTCUSDT", "mark": float(t["markPrice"]), "index": float(t["indexPrice"]), "funding_8h": float(t["fundingRate"]), "next_funding": int(t["nextFundingTime"]) // 1000,
           "oi_usd": float(t["openInterestValue"]), "volume_24h_usd": float(t["turnover24h"])}
    try:
        ls = (await get("https://api.bybit.com/v5/market/account-ratio", {"category": "linear", "symbol": "BTCUSDT", "period": "5min", "limit": 1})).json()["result"]["list"][0]
        out["long_share"] = float(ls["buyRatio"])
    except Exception:
        pass
    return out


async def _okx():
    b = "https://www.okx.com/api/v5"
    oi = (await get(f"{b}/public/open-interest", {"instType": "SWAP", "instId": "BTC-USDT-SWAP"})).json()["data"][0]
    f = (await get(f"{b}/public/funding-rate", {"instId": "BTC-USDT-SWAP"})).json()["data"][0]
    m = (await get(f"{b}/public/mark-price", {"instType": "SWAP", "instId": "BTC-USDT-SWAP"})).json()["data"][0]
    ix = (await get(f"{b}/market/index-tickers", {"instId": "BTC-USDT"})).json()["data"][0]
    tk = (await get(f"{b}/market/ticker", {"instId": "BTC-USDT-SWAP"})).json()["data"][0]
    mark = float(m["markPx"])
    out = {"venue": "OKX BTC-USDT-SWAP", "mark": mark, "index": float(ix["idxPx"]), "funding_8h": float(f["fundingRate"]), "next_funding": int(f["fundingTime"]) // 1000,
           "oi_usd": fnum(oi.get("oiUsd")) or float(oi["oiCcy"]) * mark, "volume_24h_usd": float(tk["volCcy24h"]) * mark}
    try:
        ls = (await get(f"{b}/rubik/stat/contracts/long-short-account-ratio", {"ccy": "BTC", "period": "5m"})).json()["data"][0]
        ratio = float(ls[1])
        out["long_share"] = ratio / (1 + ratio)
    except Exception:
        pass
    return out


async def _deribit():
    r = (await get("https://www.deribit.com/api/v2/public/ticker", {"instrument_name": "BTC-PERPETUAL"})).json()["result"]
    return {"venue": "Deribit BTC-PERPETUAL", "mark": float(r["mark_price"]), "index": float(r["index_price"]), "funding_8h": float(r["funding_8h"]), "oi_usd": float(r["open_interest"]),
            "volume_24h_usd": fnum((r.get("stats") or {}).get("volume_usd"))}


async def _delta():
    r = (await get(f"{DELTA}/v2/tickers/BTCUSD")).json()["result"]
    return {"venue": "Delta India BTCUSD", "mark": float(r["mark_price"]), "index": float(r["spot_price"]), "funding_8h": float(r["funding_rate"]) / 100, "oi_usd": float(r["oi_value_usd"]),
            "volume_24h_usd": fnum(r.get("turnover_usd"))}


VENUES = (("deriv_binance", _binance), ("deriv_bybit", _bybit), ("deriv_okx", _okx), ("deriv_deribit", _deribit), ("deriv_delta", _delta))


def classify_derivs(d):
    """Positioning read from several independent facts. Returns label, votes and the detections that are active."""
    votes, flags = [], []
    f, prem, ls = d.get("funding_8h"), d.get("premium_pct"), d.get("long_share")
    ch = (d.get("change") or {}).get("4h") or {}
    oi, px = ch.get("oi_pct"), ch.get("price_pct")
    if f is not None:
        votes.append({"name": "Funding rate", "vote": 1 if 0 < f <= 0.0003 else -1 if f < -0.0001 or f > 0.0005 else 0,
                      "text": f"{f * 100:+.4f}% per 8h: " + ("longs pay, normal" if 0 < f <= 0.0003 else "very high, longs crowded" if f > 0.0005 else "shorts pay" if f < 0 else "high")})
        if f > 0.0005:
            flags.append({"kind": "crowded_longs", "side": "bear", "text": f"Funding {f * 100:.3f}% per 8h is extreme: leveraged longs are crowded (long-squeeze risk)."})
        if f < -0.0003:
            flags.append({"kind": "crowded_shorts", "side": "bull", "text": f"Funding {f * 100:.3f}% per 8h is very negative: shorts are crowded (short-squeeze risk)."})
    if prem is not None:
        votes.append({"name": "Perpetual premium", "vote": 1 if 0 < prem < 0.1 else -1 if prem < -0.05 else 0, "text": f"perpetuals {prem:+.3f}% vs index"})
    if ls is not None:
        votes.append({"name": "Long/short accounts", "vote": -1 if ls > 0.65 else 1 if ls < 0.42 else 0, "text": f"{ls * 100:.0f}% of accounts long" + (" (crowded, contrarian negative)" if ls > 0.65 else " (crowded short, contrarian positive)" if ls < 0.42 else "")})
    if oi is not None and px is not None:
        if oi > 1 and px > 0.3:
            votes.append({"name": "OI vs price (4h)", "vote": 1, "text": f"OI {oi:+.1f}% with price {px:+.1f}%: new longs entering"})
        elif oi > 1 and px < -0.3:
            votes.append({"name": "OI vs price (4h)", "vote": -1, "text": f"OI {oi:+.1f}% with price {px:+.1f}%: new shorts entering"})
        elif oi < -1 and px < -0.3:
            votes.append({"name": "OI vs price (4h)", "vote": 0, "text": f"OI {oi:+.1f}% with price {px:+.1f}%: longs closing or liquidated"})
            flags.append({"kind": "long_unwind", "side": "bear", "text": "Open interest is falling with price: long positions are being closed or liquidated."})
        elif oi < -1 and px > 0.3:
            votes.append({"name": "OI vs price (4h)", "vote": 0, "text": f"OI {oi:+.1f}% with price {px:+.1f}%: shorts covering"})
            flags.append({"kind": "short_cover", "side": "bull", "text": "Open interest is falling while price rises: shorts are covering."})
        else:
            votes.append({"name": "OI vs price (4h)", "vote": 0, "text": f"OI {oi:+.1f}%, price {px:+.1f}%: no clear build-up"})
        if oi > 5:
            flags.append({"kind": "leverage_build", "side": "bear" if (f or 0) > 0.0003 else "neutral", "text": f"Open interest jumped {oi:.1f}% in 4 hours: leverage is building quickly."})
    s = sum(v["vote"] for v in votes)
    return {"label": "INSUFFICIENT DATA" if len(votes) < 2 else "BULLISH" if s >= 2 else "BEARISH" if s <= -2 else "NEUTRAL", "score": s, "votes": votes, "flags": flags}


async def load_derivs():
    rows = []
    res = await asyncio.gather(*(fn() for _, fn in VENUES), return_exceptions=True)
    for (name, _), r in zip(VENUES, res):
        if isinstance(r, Exception):
            mark(name, False, r)
        else:
            mark(name)
            r["premium_pct"] = (r["mark"] / r["index"] - 1) * 100 if r.get("index") else None
            rows.append(r)
    if not rows:
        raise RuntimeError("no derivatives venue answered")
    tot = sum(r["oi_usd"] for r in rows)
    d = {"venues": rows, "oi_usd": tot, "funding_8h": sum(r["funding_8h"] * r["oi_usd"] for r in rows) / tot if tot else None,
         "premium_pct": sum((r["premium_pct"] or 0) * r["oi_usd"] for r in rows) / tot if tot else None,
         "volume_24h_usd": sum(r.get("volume_24h_usd") or 0 for r in rows),
         "long_share": (lambda x: sum(x) / len(x) if x else None)([r["long_share"] for r in rows if r.get("long_share") is not None]),
         "price": next((r["index"] for r in rows if r.get("index")), None), "venue_set": sorted(r["venue"] for r in rows),
         "cme_basis": {"status": "UNAVAILABLE", "reason": "CME futures quotes need a paid market-data feed"},
         "updated": iso(now_utc())}
    now = int(now_utc().timestamp())
    slot = now - now % 300
    await db.intel_deriv.update_one({"_id": slot}, {"$setOnInsert": {"oi_usd": tot, "price": d["price"], "funding_8h": d["funding_8h"], "venues": d["venue_set"]}}, upsert=True)
    ch = {}
    for label, sec in (("1h", 3600), ("4h", 14400), ("24h", 86400)):
        old = await db.intel_deriv.find_one({"_id": {"$lte": slot - sec, "$gte": slot - sec - 900}}, sort=[("_id", -1)])
        if old and old.get("venues") == d["venue_set"] and old.get("oi_usd") and old.get("price"):
            ch[label] = {"oi_pct": (tot / old["oi_usd"] - 1) * 100, "price_pct": (d["price"] / old["price"] - 1) * 100}
        else:
            ch[label] = None
    d["change"] = ch
    d["change_note"] = None if all(ch.values()) else "Open-interest change is measured from this server's own 5-minute records; a window shows once that much history exists with the same exchanges."
    d["positioning"] = classify_derivs(d)
    STATE["deriv"] = d


# ------------------------------------------------------------------ liquidations (exchange-reported only)
async def add_liq(ex, uid, ts, side, usd, price):
    """side = the position that was liquidated: 'long' or 'short'."""
    key = f"{ex}:{uid}"
    if key in _seen or not usd or usd <= 0 or int(ts) < int(now_utc().timestamp()) - 86400:
        return
    _seen.add(key)
    ev = {"_id": key, "t": int(ts), "ex": ex, "side": side, "usd": float(usd), "price": float(price)}
    LIQS.append(ev)
    try:
        await db.intel_liqs.update_one({"_id": key}, {"$setOnInsert": {k: v for k, v in ev.items() if k != "_id"}}, upsert=True)
    except Exception:
        pass


def _prune():
    cut = int(now_utc().timestamp()) - 86400
    while LIQS and LIQS[0]["t"] < cut:
        _seen.discard(LIQS.popleft()["_id"])


def _okx_side(x):
    """Which position was liquidated: a forced SELL closes a long, a forced BUY closes a short (posSide confirms when given)."""
    if x.get("side") in ("sell", "buy"):
        return "long" if x["side"] == "sell" else "short"
    return "long" if x.get("posSide") == "long" else "short"


async def okx_liqs():
    r = (await get("https://www.okx.com/api/v5/public/liquidation-orders", {"instType": "SWAP", "instFamily": "BTC-USDT", "state": "filled", "limit": "100"})).json()["data"]
    for grp in r:
        for x in grp.get("details") or []:
            px, sz = float(x["bkPx"]), float(x["sz"])
            await add_liq("OKX", f"{x['ts']}-{x['posSide']}-{x['bkPx']}-{x['sz']}", int(x["ts"]) // 1000, _okx_side(x), sz * 0.01 * px, px)
    _prune()


async def ws_liqs(name, url, sub, parse):
    """One exchange liquidation stream with automatic reconnection."""
    import websockets
    back = 2
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=2 ** 20) as ws:
                if sub:
                    await ws.send(json.dumps(sub))
                mark(name)
                back = 2
                while True:
                    try:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=120))
                    except asyncio.TimeoutError:
                        await ws.send(json.dumps({"op": "ping"})) if sub else await ws.ping()
                        mark(name)
                        continue
                    for ev in parse(msg) or []:
                        await add_liq(*ev)
                    mark(name)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            mark(name, False, e)
            await asyncio.sleep(back)
            back = min(back * 2, 300)


def parse_bybit(m):
    out = []
    if str(m.get("topic", "")).startswith("allLiquidation"):
        for x in m.get("data") or []:
            px, v = float(x["p"]), float(x["v"])
            out.append(("Bybit", f"{x['T']}-{x['S']}-{x['p']}-{x['v']}", int(x["T"]) // 1000, "long" if x["S"] == "Buy" else "short", px * v, px))
    return out


def parse_binance(m):
    o = m.get("o") if m.get("e") == "forceOrder" else None
    if not o:
        return []
    px, q = float(o.get("ap") or o["p"]), float(o.get("z") or o["q"])
    return [("Binance", f"{o['T']}-{o['S']}-{o['p']}-{o['q']}", int(o["T"]) // 1000, "long" if o["S"] == "SELL" else "short", px * q, px)]


def liq_summary():
    _prune()
    now = int(now_utc().timestamp())
    ev = [e for e in LIQS if e["t"] >= now - 86400]
    win = {}
    for label, sec in (("5m", 300), ("15m", 900), ("1h", 3600), ("4h", 14400), ("24h", 86400)):
        w = [e for e in ev if e["t"] >= now - sec]
        lg, sh = sum(e["usd"] for e in w if e["side"] == "long"), sum(e["usd"] for e in w if e["side"] == "short")
        win[label] = {"long_usd": lg, "short_usd": sh, "count": len(w)}
    buckets = [{"t": now - now % 3600 - k * 3600, "long_usd": 0.0, "short_usd": 0.0} for k in range(23, -1, -1)]
    idx = {b["t"]: b for b in buckets}
    for e in ev:
        b = idx.get(e["t"] - e["t"] % 3600)
        if b:
            b["long_usd" if e["side"] == "long" else "short_usd"] += e["usd"]
    large = sorted([e for e in ev if e["usd"] >= LARGE_LIQ_USD], key=lambda e: -e["t"])[:25]
    h1 = win["1h"]
    day_avg = (win["24h"]["long_usd"] + win["24h"]["short_usd"]) / 24
    cascade = None
    if h1["long_usd"] + h1["short_usd"] > max(3 * day_avg, 5_000_000):
        side = "long" if h1["long_usd"] > h1["short_usd"] else "short"
        cascade = {"side": side, "text": f"{side.title()} liquidations in the last hour are {((h1['long_usd'] + h1['short_usd']) / day_avg if day_avg else 0):.1f}x the 24-hour hourly average: a liquidation cascade may be under way."}
    feeds = {k: v for k, v in SRC.items() if v["module"] == "liquidations"}
    since = min((e["t"] for e in ev), default=None)
    by_ex = {}
    for e in ev:
        b = by_ex.setdefault(e["ex"], {"long_usd": 0.0, "short_usd": 0.0, "count": 0})
        b["long_usd" if e["side"] == "long" else "short_usd"] += e["usd"]
        b["count"] += 1
    return {"windows": win, "hourly": buckets, "large": [{k: e[k] for k in ("t", "ex", "side", "usd", "price")} for e in large], "large_threshold_usd": LARGE_LIQ_USD,
            "cascade": cascade, "events_24h": len(ev), "by_exchange": by_ex, "collecting_since": since,
            "feeds_ok": sorted(v["label"] for v in feeds.values() if v["ok"]), "feeds_down": sorted(v["label"] for v in feeds.values() if v["ok"] is False),
            "note": "Exchange-reported liquidations only, from the feeds listed. Exchanges publish a sample, not every order, so real totals are higher. Totals cover the time since this server started collecting."}


# ------------------------------------------------------------------ correlations (daily closes)
YAHOO = {"Nasdaq 100": "^NDX", "S&P 500": "^GSPC", "Gold": "GC=F", "US Dollar Index (DXY)": "DX-Y.NYB", "US 10-year yield": "^TNX"}
STOOQ = {"Nasdaq 100": "^ndx", "S&P 500": "^spx", "Gold": "xauusd", "US Dollar Index (DXY)": "dx.f", "US 10-year yield": "10usy.b"}


async def _daily(name):
    """{YYYY-MM-DD: close} for one traditional-market series; Yahoo first, Stooq second."""
    try:
        j = (await get(f"https://query1.finance.yahoo.com/v8/finance/chart/{YAHOO[name]}", {"range": "1y", "interval": "1d"})).json()["chart"]["result"][0]
        cl = j["indicators"]["quote"][0]["close"]
        out = {iso_day(t): c for t, c in zip(j["timestamp"], cl) if c is not None}
        if len(out) > 60:
            return out, "Yahoo Finance daily close"
    except Exception:
        pass
    txt = (await get("https://stooq.com/q/d/l/", {"s": STOOQ[name], "i": "d"})).text
    out = {}
    for line in txt.splitlines()[1:]:
        p = line.split(",")
        if len(p) >= 5 and fnum(p[4]) is not None:
            out[p[0]] = float(p[4])
    if len(out) < 60:
        raise RuntimeError("no daily history returned")
    return dict(list(sorted(out.items()))[-260:]), "Stooq daily close"


def iso_day(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")


def corr(a, b):
    n = len(a)
    if n < 5:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb) if va > 0 and vb > 0 else None


def rolling_corr(btc, other, windows=(7, 30, 90)):
    """Correlation of daily returns on the days both series have a close (weekends drop out for stocks)."""
    days = [d for d in sorted(set(btc) & set(other)) if btc[d] > 0 and other[d] > 0]
    ra = [math.log(btc[d1] / btc[d0]) for d0, d1 in zip(days, days[1:])]
    rb = [math.log(other[d1] / other[d0]) for d0, d1 in zip(days, days[1:])]
    return {str(w): (corr(ra[-w:], rb[-w:]) if len(ra) >= w else None) for w in windows}, len(days), days[-1] if days else None


async def load_corr():
    async def crypto(sym):
        k = (await get("https://data-api.binance.vision/api/v3/klines", {"symbol": sym, "interval": "1d", "limit": 260})).json()[:-1]
        return {iso_day(int(x[0]) // 1000): float(x[4]) for x in k}
    try:
        btc = await crypto("BTCUSDT")
        btc_src = "Binance spot daily close"
    except Exception:
        d = [b for b in STATE["candles"].get("1d", [])][:-1]
        btc = {iso_day(b["t"]): b["c"] for b in d}
        btc_src = "Delta daily close"
    if len(btc) < 30:
        raise RuntimeError("no BTC daily history for correlations")
    rows = []
    jobs = {n: _daily(n) for n in YAHOO}
    res = dict(zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)))
    try:
        res["Ethereum"] = (await crypto("ETHUSDT"), "Binance spot daily close")
    except Exception as e:
        res["Ethereum"] = e
    prev = {r["name"]: r for r in (STATE["corr"] or {}).get("rows", [])}
    for name, r in res.items():
        if isinstance(r, Exception):
            rows.append({"name": name, "status": "UNAVAILABLE", "reason": str(r)[:120]})
            continue
        series, src = r
        c, n, last = rolling_corr(btc, series)
        row = {"name": name, "status": "OK", "corr": c, "days": n, "last_day": last, "source": src}
        d30, d90 = c.get("30"), c.get("90")
        row["shift"] = None if d30 is None or d90 is None else "Relationship is changing: 30-day correlation differs from 90-day by %.2f" % (d30 - d90) if abs(d30 - d90) >= 0.3 else None
        rows.append(row)
    rows.append({"name": "Total crypto market cap", "status": "UNAVAILABLE", "reason": "History needs a paid data plan (CoinGecko Pro / CoinMarketCap)"})
    ok = [r for r in rows if r["status"] == "OK"]
    if not ok and prev:
        raise RuntimeError("no correlation series could be loaded")
    STATE["corr"] = {"rows": rows, "btc_source": btc_src, "updated": iso(now_utc()), "method": "Correlation of daily returns over the last 7, 30 and 90 common trading days. 7-day values are very noisy."}


# ------------------------------------------------------------------ public views
def overview():
    from sources import WS
    o = dict(STATE["overview"])
    q = WS.get("quote") or {}
    d1 = STATE["tf"].get("1d") or {}
    h4 = STATE["tf"].get("4h") or {}
    live = q.get("last") or (o.get("delta") or {}).get("price")
    dl = o.get("delta") or {}
    chg = (live / dl["open_24h"] - 1) * 100 if live and dl.get("open_24h") else None
    return {**o, "price": live, "price_source": "Delta Exchange India live stream" if q.get("last") else "Delta Exchange India ticker", "price_time": WS.get("last_msg") or dl.get("updated"),
            "change_24h_pct": chg, "trend": {"daily": d1.get("bias"), "h4": h4.get("bias"), "structure_daily": (d1.get("structure") or {}).get("label")},
            "volatility": {"atr_1h_pct": (STATE["tf"].get("1h") or {}).get("atr_pct"), "atr_1d_pct": d1.get("atr_pct")}, "levels": STATE["levels"]}


def health():
    now = now_utc()
    from core import parse_iso
    out = []
    for k, s in SRC.items():
        age = (now - parse_iso(s["updated"])).total_seconds() if s["updated"] else None
        out.append({"key": k, "label": s["label"], "module": s["module"], "status": "WAITING" if s["ok"] is None else "OK" if s["ok"] else "UNAVAILABLE", "updated": s["updated"],
                    "age_sec": round(age) if age is not None else None, "error": s["error"], "note": s["note"]})
    return out


def public():
    tf = {k: {x: y for x, y in v.items() if x != "volume_profile"} for k, v in STATE["tf"].items()}
    return {"server_time": iso(now_utc()), "overview": overview(), "timeframes": tf, "order": list(TF), "pressure": STATE["pressure"], "derivatives": STATE["deriv"],
            "liquidations": liq_summary(), "correlations": STATE["corr"], "sources": health(),
            "disclaimer": "Monitoring and decision support only. It does not place trades and it is not a prediction."}


def chart(tf):
    bars = STATE["candles"].get(tf) or []
    a = STATE["tf"].get(tf) or {}
    return {"timeframe": tf, "bars": bars[-400:], "overlays": {k: v[-400:] for k, v in ta.overlays(bars).items()} if len(bars) >= 20 else {},
            "support": a.get("support"), "resistance": a.get("resistance"), "volume_profile": a.get("volume_profile"), "fib": a.get("fib"), "vwap": a.get("vwap"),
            "updated": a.get("updated"), "source": a.get("source")}


# ------------------------------------------------------------------ start-up
def register():
    reg("candles_fast", "Delta candles 1m, 5m", "price")
    reg("candles_mid", "Delta candles 15m, 30m", "price")
    reg("candles_slow", "Delta candles 1h, 4h, 1d", "price")
    reg("ticker", "Delta ticker (24h high, low, turnover)", "price")
    reg("global", "CoinGecko market cap and global volume", "price", "Free tier: refreshed every 2 minutes")
    reg("pressure", "Spot buy vs sell pressure (Binance, Coinbase backup)", "price")
    reg("derivs", "Derivatives summary (all exchanges below)", "derivatives")
    for k, label in (("deriv_binance", "Binance futures"), ("deriv_bybit", "Bybit futures"), ("deriv_okx", "OKX swap"), ("deriv_deribit", "Deribit perpetual"), ("deriv_delta", "Delta India perpetual")):
        reg(k, label, "derivatives")
    reg("liq_okx", "OKX liquidation orders", "liquidations")
    reg("liq_bybit", "Bybit liquidation stream", "liquidations")
    reg("liq_binance", "Binance liquidation stream", "liquidations", "Binance publishes at most one liquidation per second")
    reg("corr", "Correlations (Yahoo / Stooq / Binance daily closes)", "correlations", "Refreshed every 3 hours")


async def start():
    register()
    try:
        await db.intel_liqs.create_index("t", expireAfterSeconds=8 * 86400)
        cut = int(now_utc().timestamp()) - 86400
        async for e in db.intel_liqs.find({"t": {"$gte": cut}}).sort("t", 1):
            LIQS.append(e)
            _seen.add(e["_id"])
        await db.intel_deriv.delete_many({"_id": {"$lt": cut - 40 * 86400}})
    except Exception as e:
        log.warning("intel start-up: %s", e)
    jobs = [worker("candles_fast", _group(("1m", "5m")), 20), worker("candles_mid", _group(("15m", "30m")), 60), worker("candles_slow", _group(("1h", "4h", "1d")), 180),
            worker("ticker", load_ticker, 30), worker("global", load_global, 120), worker("pressure", load_pressure, 30), worker("derivs", load_derivs, 60),
            worker("liq_okx", okx_liqs, 20), worker("corr", load_corr, 3 * 3600),
            ws_liqs("liq_bybit", "wss://stream.bybit.com/v5/public/linear", {"op": "subscribe", "args": ["allLiquidation.BTCUSDT"]}, parse_bybit),
            ws_liqs("liq_binance", "wss://fstream.binance.com/ws/btcusdt@forceOrder", None, parse_binance)]
    for j in jobs:
        asyncio.create_task(j)
