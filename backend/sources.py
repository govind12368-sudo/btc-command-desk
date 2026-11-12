import asyncio
import json
import os
import re
import hashlib
import logging
import email.utils
import xml.etree.ElementTree as ET
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo
from datetime import datetime, timezone, timedelta, date
import httpx
import websockets
from core import db, now_utc, iso, parse_iso, age_sec

log = logging.getLogger("sources")
DELTA = "https://api.india.delta.exchange"
DELTA_WS = "wss://socket.india.delta.exchange"
OKX = "https://www.okx.com"
UA = {"User-Agent": "VantageBTC/2.0 (+server-side data adapter)"}
SKEW_SEC = 5
EXTRA = {}
_sem = asyncio.Semaphore(6)
_client = None


def client():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=15, headers=UA, follow_redirects=True)
    return _client


class AccessError(Exception):
    pass


class RateLimited(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__(f"rate limited by provider, retry after {retry_after}s")


async def http_get(url, headers=None, params=None):
    async with _sem:
        r = await client().get(url, headers=headers, params=params)
    if r.status_code == 429:
        ra = r.headers.get("Retry-After")
        raise RateLimited(int(ra) if ra and ra.isdigit() else 60)
    if r.status_code in (401, 403):
        raise AccessError(f"HTTP {r.status_code}: provider refused automated access")
    r.raise_for_status()
    return r


class Source:
    def __init__(self, key, family, name, instrument, unit, url, interval, freshness, fetch, page_only=False):
        self.key, self.family, self.name, self.instrument, self.unit, self.url = key, family, name, instrument, unit, url
        self.interval, self.freshness, self.fetch, self.page_only = interval, freshness, fetch, page_only
        self.status, self.error, self.data = "checking", None, None
        self.last_attempt = self.last_success = None
        self.fail_count, self.next_run, self.retry_after = 0, now_utc(), None
        self.active_until = None

    def health(self):
        if self.status in ("error", "access_needed", "rate_limited", "checking"):
            return self.status
        if self.last_success and age_sec(self.last_success) > self.freshness:
            return "stale"
        if self.family == "calendar" and self.data and self.data.get("coverage_complete") is False:
            return "partial"
        if self.family == "news" and self.data and self.data.get("accepted", 0) > 0 and self.data.get("time_errors", 0) >= self.data["accepted"]:
            return "partial"
        return "ok"

    def public(self):
        return {"key": self.key, "family": self.family, "name": self.name, "instrument": self.instrument,
                "unit": self.unit, "url": self.url, "interval_sec": self.interval, "freshness_sec": self.freshness,
                "status": self.health(), "error": self.error or ("Official calendar provider coverage is incomplete" if self.family == "calendar" and self.health() == "partial" else "No usable publication timestamps in this news retrieval" if self.family == "news" and self.health() == "partial" else None), "last_attempt": iso(self.last_attempt),
                "last_success": iso(self.last_success), "fail_count": self.fail_count,
                "next_retry": iso(self.next_run) if self.fail_count else None,
                **({"coverage_complete": self.data.get("coverage_complete"),
                    "providers": self.data.get("providers", []), "coverage": self.data.get("coverage")}
                   if self.family == "calendar" and self.data else {})}

    async def run(self):
        self.last_attempt = now_utc()
        try:
            data = await self.fetch()
            self.data, self.error, self.fail_count, self.status = data, None, 0, "ok"
            self.last_success = now_utc()
            self.next_run = now_utc() + timedelta(seconds=self.interval)
            await db.source_cache.update_one({"_id": self.key}, {"$set": {"data": json.loads(json.dumps(data, default=str)), "last_success": iso(self.last_success)}}, upsert=True)
            return True
        except RateLimited as e:
            self.status, self.error = "rate_limited", str(e)
            self.fail_count += 1
            self.next_run = now_utc() + timedelta(seconds=max(e.retry_after, self.interval))
        except AccessError as e:
            self.status, self.error = "access_needed", str(e)
            self.fail_count += 1
            self.next_run = now_utc() + timedelta(seconds=900)
        except Exception as e:
            self.status, self.error = "error", f"{type(e).__name__}: {str(e)[:200]}"
            self.fail_count += 1
            self.next_run = now_utc() + timedelta(seconds=min(900, self.interval * 2 ** min(self.fail_count, 8)))
        return False


# ---------------- Delta Exchange India (execution market) ----------------
async def delta_product():
    r = (await http_get(f"{DELTA}/v2/products/BTCUSD")).json()["result"]
    return {"symbol": r["symbol"], "product_id": r["id"], "state": r["state"], "contract_type": r["contract_type"],
            "tick_size": float(r["tick_size"]), "contract_value": float(r["contract_value"]),
            "contract_unit_currency": r.get("contract_unit_currency"),
            "underlying": (r.get("underlying_asset") or {}).get("symbol"),
            "quoting": (r.get("quoting_asset") or {}).get("symbol"), "settling": (r.get("settling_asset") or {}).get("symbol"),
            "taker_fee": float(r.get("taker_commission_rate") or 0), "maker_fee": float(r.get("maker_commission_rate") or 0),
            "funding_interval_sec": (r.get("product_specs") or {}).get("rate_exchange_interval"),
            "position_size_limit": r.get("position_size_limit")}


async def delta_ticker():
    r = (await http_get(f"{DELTA}/v2/tickers/BTCUSD")).json()["result"]
    q = r.get("quotes") or {}
    ev = datetime.fromtimestamp(int(r["timestamp"]) / 1e6, timezone.utc)
    b = (await http_get(f"{DELTA}/v2/l2orderbook/BTCUSD", params={"depth": 50})).json()["result"]
    return {"bid": float(q["best_bid"]) if q.get("best_bid") else None, "ask": float(q["best_ask"]) if q.get("best_ask") else None,
            "last": float(r["close"]), "mark": float(r["mark_price"]), "spot_index": float(r["spot_price"]),
            "funding_rate_pct_8h": float(r["funding_rate"]), "oi_btc": float(r["oi"]), "oi_usd": float(r["oi_value_usd"]),
            "oi_contracts": float(r["oi_contracts"]), "mark_basis": float(r.get("mark_basis") or 0),
            "volume_24h_contracts": r.get("volume"), "turnover_24h_usd": r.get("turnover_usd"),
            "high_24h": r.get("high"), "low_24h": r.get("low"), "open_24h": r.get("open"),
            "event_time": iso(ev),
            "book": {"bids": [[float(x["price"]), float(x["size"])] for x in b["buy"]],
                     "asks": [[float(x["price"]), float(x["size"])] for x in b["sell"]],
                     "event_time": iso(datetime.fromtimestamp(int(b["last_updated_at"]) / 1e6, timezone.utc))}}


RES = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def _complete(rows, res):
    now = now_utc().timestamp()
    bars, rejected = {}, 0
    for x in rows:
        t = int(x["time"])
        if t > now + SKEW_SEC:
            rejected += 1
            continue
        if t + res <= now:
            bars[t] = {"t": t, "o": float(x["open"]), "h": float(x["high"]), "l": float(x["low"]), "c": float(x["close"]), "v": float(x["volume"] or 0)}
    return [bars[k] for k in sorted(bars)], rejected


async def delta_candles_tf(tf, n=220, start=None, end=None):
    res = RES[tf]
    end = end or int(now_utc().timestamp())
    start = start or end - res * n
    r = (await http_get(f"{DELTA}/v2/history/candles", params={"symbol": "BTCUSD", "resolution": tf, "start": start, "end": end})).json()
    return r.get("result") or []


async def delta_candles():
    out = {}
    for tf in ("5m", "15m", "1h", "4h", "1d"):
        rows = await delta_candles_tf(tf, 220 if tf != "1d" else 120)
        bars, rej = _complete(rows, RES[tf])
        out[tf] = {"bars": bars, "future_rejected": rej}
    return out


# Delta websocket live state
WS = {"status": "disconnected", "connected_at": None, "last_msg": None, "last_heartbeat": None, "reconnects": 0,
      "quote": None, "book": None, "trades": [], "error": None}


async def delta_ws_loop():
    backoff = 1
    while True:
        try:
            WS["status"] = "connecting"
            async with websockets.connect(DELTA_WS, ping_interval=20, ping_timeout=20, max_size=2**22) as ws:
                await ws.send(json.dumps({"type": "subscribe", "payload": {"channels": [
                    {"name": "v2/ticker", "symbols": ["BTCUSD"]}, {"name": "l2_orderbook", "symbols": ["BTCUSD"]},
                    {"name": "all_trades", "symbols": ["BTCUSD"]}]}}))
                await ws.send(json.dumps({"type": "enable_heartbeat"}))
                WS.update(status="connected", connected_at=iso(now_utc()), error=None)
                backoff = 1
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=45))
                    WS["last_msg"] = iso(now_utc())
                    t = msg.get("type")
                    if t == "heartbeat":
                        WS["last_heartbeat"] = iso(now_utc())
                    elif t == "v2/ticker":
                        q = msg.get("quotes") or {}
                        WS["quote"] = {"bid": float(q["best_bid"]) if q.get("best_bid") else None,
                                       "ask": float(q["best_ask"]) if q.get("best_ask") else None,
                                       "last": float(msg.get("close") or 0), "mark": float(msg.get("mark_price") or 0),
                                       "event_time": iso(datetime.fromtimestamp(int(msg["timestamp"]) / 1e6, timezone.utc)),
                                       "received": iso(now_utc())}
                    elif t == "l2_orderbook":
                        WS["book"] = {"bids": [[float(x["limit_price"]), float(x["size"])] for x in msg.get("buy", [])],
                                      "asks": [[float(x["limit_price"]), float(x["size"])] for x in msg.get("sell", [])],
                                      "event_time": iso(datetime.fromtimestamp(int(msg.get("timestamp", msg.get("last_updated_at", 0))) / 1e6, timezone.utc)) if msg.get("timestamp") or msg.get("last_updated_at") else None,
                                      "received": iso(now_utc())}
                    elif t in ("all_trades", "all_trades_snapshot"):
                        items = msg.get("trades") if t == "all_trades_snapshot" else [msg]
                        for x in items:
                            WS["trades"].append({"ts": int(x["timestamp"]) / 1e6, "price": float(x["price"]), "size": float(x["size"]),
                                                 "side": "buy" if x.get("buyer_role") == "taker" else "sell"})
                        # Prune at most once per second (rebuilding a 20k list on every trade wastes CPU on small servers).
                        nowts = now_utc().timestamp()
                        if nowts - WS.get("_pruned", 0) >= 1 or len(WS["trades"]) > 25000:
                            WS["_pruned"] = nowts
                            WS["trades"] = [x for x in WS["trades"] if x["ts"] >= nowts - 900][-20000:]
        except asyncio.CancelledError:
            WS["status"] = "disconnected"
            raise
        except Exception as e:
            WS.update(status="reconnecting", error=f"{type(e).__name__}: {str(e)[:150]}")
            WS["reconnects"] += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


# ---------------- OKX BTC-USDT-SWAP (five-pillar derivatives context) ----------------
async def okx_get(path, **params):
    r = (await http_get(f"{OKX}{path}", params=params)).json()
    if r.get("code") != "0":
        raise RuntimeError(f"OKX error {r.get('code')}: {r.get('msg')}")
    return r["data"]


async def okx_snapshot():
    inst = (await okx_get("/api/v5/public/instruments", instType="SWAP", instId="BTC-USDT-SWAP"))[0]
    ct = float(inst["ctVal"])
    tk = (await okx_get("/api/v5/market/ticker", instId="BTC-USDT-SWAP"))[0]
    bk = (await okx_get("/api/v5/market/books", instId="BTC-USDT-SWAP", sz="400"))[0]
    tr = await okx_get("/api/v5/market/trades", instId="BTC-USDT-SWAP", limit="500")
    oi = (await okx_get("/api/v5/public/open-interest", instType="SWAP", instId="BTC-USDT-SWAP"))[0]
    fr = (await okx_get("/api/v5/public/funding-rate", instId="BTC-USDT-SWAP"))[0]
    fh = await okx_get("/api/v5/public/funding-rate-history", instId="BTC-USDT-SWAP", limit="30")
    c15 = await okx_get("/api/v5/market/candles", instId="BTC-USDT-SWAP", bar="15m", limit="200")
    try:
        lsr = await okx_get("/api/v5/rubik/stat/contracts/long-short-account-ratio", ccy="BTC", period="1H")
    except Exception:
        lsr = []
    ms = lambda v: iso(datetime.fromtimestamp(int(v) / 1000, timezone.utc))
    bars = sorted([{"t": int(x[0]) // 1000, "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[6])}
                   for x in c15 if x[8] == "1" and int(x[0]) / 1000 <= now_utc().timestamp() + SKEW_SEC], key=lambda b: b["t"])
    return {"instrument": "BTC-USDT-SWAP", "ct_val_btc": ct, "settle": inst.get("settleCcy"), "tick": float(inst["tickSz"]),
            "last": float(tk["last"]), "bid": float(tk["bidPx"]), "ask": float(tk["askPx"]), "ticker_time": ms(tk["ts"]),
            "book": {"bids": [[float(x[0]), float(x[1]) * ct] for x in bk["bids"]], "asks": [[float(x[0]), float(x[1]) * ct] for x in bk["asks"]], "event_time": ms(bk["ts"])},
            "trades": [{"ts": int(x["ts"]) / 1000, "price": float(x["px"]), "btc": float(x["sz"]) * ct, "side": x["side"]} for x in tr],
            "oi_btc": float(oi["oiCcy"]), "oi_usd": float(oi["oiUsd"]), "oi_time": ms(oi["ts"]),
            "funding_fraction": float(fr["fundingRate"]), "next_funding_time": ms(fr["fundingTime"]) if fr.get("fundingTime") else None,
            "funding_time_retrieved": iso(now_utc()),
            "funding_history": [{"time": ms(x["fundingTime"]), "realized_fraction": float(x.get("realizedRate") or x["fundingRate"])} for x in fh],
            "candles_15m": bars,
            "long_short_account_ratio": [{"time": ms(x[0]), "ratio": float(x[1])} for x in lsr[:24]]}


# ---------------- Macro: Fed H.10, H.15, EIA Brent ----------------
FED_DDP = "https://www.federalreserve.gov/datadownload/Output.aspx"


def _ddp_parse(text, col):
    lines = [l for l in text.splitlines() if l.strip()]
    hdr = next(i for i, l in enumerate(lines) if l.startswith('"Time Period"'))
    cols = [c.strip('"') for c in lines[hdr].split(",")]
    idx = cols.index(col)
    obs = []
    for l in lines[hdr + 1:]:
        p = l.split(",")
        if p[idx] not in ("ND", "NC", ""):
            obs.append({"date": p[0], "value": float(p[idx])})
    return obs


def _check_obs(obs):
    today = now_utc().date()
    fut = [o for o in obs if date.fromisoformat(o["date"]) > today]
    if fut:
        raise RuntimeError(f"rejected future-dated observation {fut[-1]['date']}")
    if not obs:
        raise RuntimeError("no numeric observations returned")
    return obs


async def fed_h10():
    t = (await http_get(FED_DDP, params={"rel": "H10", "series": "122e3bcb627e8e53f1bf72a1a09cfb81", "lastobs": "45", "from": "", "to": "",
                                         "filetype": "csv", "label": "include", "layout": "seriescolumn", "type": "package"})).text
    return {"series": "JRXWTFB_N.B (Nominal Broad Dollar Index, FRED DTWEXBGS equivalent)", "unit": "index (Fed H.10 published units)",
            "obs": _check_obs(_ddp_parse(t, "JRXWTFB_N.B"))}


async def fed_h15():
    t = (await http_get(FED_DDP, params={"rel": "H15", "series": "bf17364827e38702b42a58cf8eaa3f78", "lastobs": "30", "from": "", "to": "",
                                         "filetype": "csv", "label": "include", "layout": "seriescolumn", "type": "package"})).text
    return {"series": "RIFLGFCY02_N.B (2-year Treasury constant maturity, FRED DGS2 equivalent)", "unit": "percent per year",
            "obs": _check_obs(_ddp_parse(t, "RIFLGFCY02_N.B"))}


MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


async def _eia_page():
    return (await http_get("https://www.eia.gov/dnav/pet/hist/RBRTED.htm")).text


async def eia_brent():
    s = await _eia_page()
    obs = []
    for row in re.findall(r"<tr>(.*?)</tr>", s, re.S)[-260:]:
        m = re.search(r"class='B6'>(?:&nbsp;)*\s*(\d{4}) (\w{3})-(\d{2}) to", row)
        if not m:
            continue
        start = date(int(m.group(1)), MONTHS[m.group(2)], int(m.group(3)))
        vals = re.findall(r"class='B3'>([^<]*)<", row)
        for i, v in enumerate(vals[:5]):
            v = v.strip().replace(",", "")
            if v:
                obs.append({"date": (start + timedelta(days=i)).isoformat(), "value": float(v)})
    return {"series": "RBRTE daily (Europe Brent Spot Price FOB, FRED DCOILBRENTEU equivalent)", "unit": "USD per barrel", "obs": _check_obs(obs)}


def _parse_long_date(s):
    return datetime.strptime(s, "%B %d, %Y").date()


async def fed_h10_meta():
    t = (await http_get("https://www.federalreserve.gov/releases/h10/current/default.htm")).text
    m = re.search(r"([A-Z][a-z]+ \d{1,2}, 20\d{2})", t)
    if not m:
        raise RuntimeError("release date not found in H.10 page")
    return {"release_date": _parse_long_date(m.group(1)).isoformat(), "schedule": "weekly, Monday (prior-week daily observations)", "next_inferred": True}


async def fed_h15_meta():
    t = (await http_get("https://www.federalreserve.gov/releases/h15/")).text
    m = re.search(r"([A-Z][a-z]+ \d{1,2}, 20\d{2})", t)
    if not m:
        raise RuntimeError("release date not found in H.15 page")
    return {"release_date": _parse_long_date(m.group(1)).isoformat(), "schedule": "daily on business days", "next_inferred": True}


async def eia_meta():
    s = await _eia_page()
    r = re.search(r"Release Date: (\d{2}/\d{2}/\d{4})", s)
    n = re.search(r"Next Release Date: (\d{2}/\d{2}/\d{4})", s)
    if not r:
        raise RuntimeError("release metadata not found on EIA page")
    f = lambda x: datetime.strptime(x, "%m/%d/%Y").date().isoformat()
    return {"release_date": f(r.group(1)), "next_release_date": f(n.group(1)) if n else None,
            "schedule": "weekly batches of daily observations", "next_inferred": False}


# ---------------- Sentiment / ETF / all-exchange funding / calendar ----------------
async def fear_greed():
    d = (await http_get("https://api.alternative.me/fng/", params={"limit": "10"})).json()["data"]
    return {"obs": [{"value": int(x["value"]), "label": x["value_classification"],
                     "date": iso(datetime.fromtimestamp(int(x["timestamp"]), timezone.utc))} for x in d]}


async def bitbo_etf():
    t = (await http_get("https://bitbo.io/treasuries/etf-flows/", headers={"User-Agent": "Mozilla/5.0 (VantageBTC server adapter)"})).text
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S)
    cells = lambda r: [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", r, re.S)]
    hdr = cells(rows[0])
    if not hdr or hdr[0] != "Date" or hdr[-1] != "Totals":
        raise RuntimeError("ETF flow table layout changed; refusing to guess")
    out = []
    for r in rows[1:]:
        c = cells(r)
        try:
            d = datetime.strptime(c[0], "%b %d, %Y").date()
            tot = float(c[-1].replace(",", ""))
        except (ValueError, IndexError):
            continue
        if d > now_utc().date():
            raise RuntimeError(f"rejected future-dated ETF row {d}")
        out.append({"date": d.isoformat(), "total_musd": tot, "funds": dict(zip(hdr[1:-1], c[1:-1]))})
    if not out:
        raise RuntimeError("no ETF flow rows parsed")
    return {"latest": out[0], "rows": out[:10], "unit": "USD million net daily flow", "source_note": "Bitbo ETF flow table (compiled from issuer data)"}


async def funding_multi():
    venues = []
    try:
        o = await okx_get("/api/v5/public/funding-rate", instId="BTC-USDT-SWAP")
        oi = await okx_get("/api/v5/public/open-interest", instType="SWAP", instId="BTC-USDT-SWAP")
        venues.append({"venue": "OKX BTC-USDT-SWAP", "fraction_8h": float(o[0]["fundingRate"]), "oi_usd": float(oi[0]["oiUsd"])})
    except Exception as e:
        venues.append({"venue": "OKX", "error": str(e)[:80]})
    try:
        r = (await http_get("https://www.deribit.com/api/v2/public/ticker", params={"instrument_name": "BTC-PERPETUAL"})).json()["result"]
        venues.append({"venue": "Deribit BTC-PERPETUAL", "fraction_8h": float(r["funding_8h"]), "oi_usd": float(r["open_interest"])})
    except Exception as e:
        venues.append({"venue": "Deribit", "error": str(e)[:80]})
    try:
        f = (await http_get("https://api.bitget.com/api/v2/mix/market/current-fund-rate", params={"symbol": "BTCUSDT", "productType": "USDT-FUTURES"})).json()["data"][0]
        oi = (await http_get("https://api.bitget.com/api/v2/mix/market/open-interest", params={"symbol": "BTCUSDT", "productType": "USDT-FUTURES"})).json()["data"]["openInterestList"][0]
        tk = (await http_get("https://api.bitget.com/api/v2/mix/market/ticker", params={"symbol": "BTCUSDT", "productType": "USDT-FUTURES"})).json()["data"][0]
        venues.append({"venue": "Bitget BTCUSDT", "fraction_8h": float(f["fundingRate"]) * 8 / float(f.get("fundingRateInterval") or 8), "oi_usd": float(oi["size"]) * float(tk["lastPr"])})
    except Exception as e:
        venues.append({"venue": "Bitget", "error": str(e)[:80]})
    try:
        g = (await http_get("https://api.gateio.ws/api/v4/futures/usdt/contracts/BTC_USDT")).json()
        venues.append({"venue": "Gate BTC_USDT", "fraction_8h": float(g["funding_rate"]) * 28800 / float(g["funding_interval"]), "oi_usd": float(g["position_size"]) * float(g["quanto_multiplier"]) * float(g["mark_price"])})
    except Exception as e:
        venues.append({"venue": "Gate", "error": str(e)[:80]})
    try:
        d = (await http_get(f"{DELTA}/v2/tickers/BTCUSD")).json()["result"]
        venues.append({"venue": "Delta India BTCUSD", "fraction_8h": float(d["funding_rate"]) / 100, "oi_usd": float(d["oi_value_usd"])})
    except Exception as e:
        venues.append({"venue": "Delta", "error": str(e)[:80]})
    ok = [v for v in venues if "error" not in v and v["oi_usd"] > 0]
    if len(ok) < 4:
        raise RuntimeError(f"only {len(ok)} venues available (need ≥4 for the multi-exchange proxy)")
    w = sum(v["oi_usd"] for v in ok)
    f8 = sum(v["fraction_8h"] * v["oi_usd"] for v in ok) / w
    return {"oi_weighted_fraction_8h": f8, "annualized_pct": f8 * 3 * 365 * 100, "venues": venues, "venues_used": len(ok), "oi_usd_total": w,
            "observation_time": iso(now_utc()),
            "definition": "OI-weighted 8h funding across available major venues, annualized ×1095. Proxy for all-exchange average; Binance and Bybit are geo-blocked from this server and excluded."}


async def coinglass_funding():
    key = os.environ.get("COINGLASS_API_KEY")
    if not key:
        raise AccessError("COINGLASS_API_KEY not configured")
    r = (await http_get("https://open-api-v4.coinglass.com/api/futures/funding-rate/oi-weight-history",
                        headers={"CG-API-KEY": key, "accept": "application/json"},
                        params={"symbol": "BTC", "interval": "8h", "limit": "3"})).json()
    if str(r.get("code")) != "0":
        msg = r.get("msg") or "unknown"
        if "key" in msg.lower() or "upgrade" in msg.lower() or "plan" in msg.lower():
            raise AccessError(f"CoinGlass refused the key: {msg}")
        raise RuntimeError(f"CoinGlass error: {msg}")
    d = r["data"][-1]
    pct_8h = float(d["close"])
    return {"oi_weighted_pct_8h": pct_8h, "annualized_pct": pct_8h * 3 * 365,
            "observation_time": iso(datetime.fromtimestamp(int(d["time"]) / 1000, timezone.utc)),
            "definition": "OI-weighted funding across exchanges, 8h, annualized ×1095"}


BLS_CALENDAR_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
BEA_CALENDAR_URL = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"
FED_CALENDAR_URL = "https://www.federalreserve.gov/newsevents/calendar.htm"
MAX_FEED_BYTES = 2 * 1024 * 1024
CALENDAR_DAYS = 45
MAJOR_EVENT = re.compile(r"\b(Consumer Price Index|Employment Situation|Producer Price Index|Job Openings|Employment Cost Index|Gross Domestic Product|GDP|Personal Income and Outlays|FOMC)\b", re.I)


def _bounded_feed(value):
    if len(value.encode("utf-8") if isinstance(value, str) else value) > MAX_FEED_BYTES:
        raise ValueError("provider feed exceeds the 2 MB safety limit")
    return value


def _calendar_event(title, when, publisher, source_url, retrieved_at, uid=None):
    title = _strip(title)
    return {"id": hashlib.sha256(f"{publisher}|{uid or title}|{iso(when)}".encode()).hexdigest(),
            "title": title, "time": iso(when), "publisher": publisher, "source_url": source_url,
            "retrieved_at": iso(retrieved_at), "timezone": "UTC", "time_precision": "minute", "date_only": False,
            "impact": "HIGH" if MAJOR_EVENT.search(title) else "MEDIUM",
            "impact_basis": "dashboard event category; not a provider forecast"}


def _in_calendar_window(when, now):
    return now - timedelta(hours=2) <= when <= now + timedelta(days=CALENDAR_DAYS)


def _ics_text(value):
    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1).lower() == "n" else m.group(1), value)


def _ics_datetime(value, tzid=None):
    value = value.strip()
    # All-day dates cannot safely be interpreted as timed economic releases.
    if not re.fullmatch(r"\d{8}T\d{4}(?:\d{2})?Z?", value):
        raise ValueError("calendar event has no supported, explicit release time")
    utc = value.endswith("Z")
    raw = value.rstrip("Z")
    when = datetime.strptime(raw, "%Y%m%dT%H%M%S" if len(raw) == 15 else "%Y%m%dT%H%M")
    zone = timezone.utc if utc else ZoneInfo({"US-Eastern": "America/New_York", "Eastern Standard Time": "America/New_York"}.get(tzid, tzid) or "America/New_York")
    return when.replace(tzinfo=zone).astimezone(timezone.utc)


def parse_ics_calendar(text, publisher, source_url, now=None):
    """Parse the official BLS/BEA timed calendars without evaluating feed content."""
    now = now or now_utc()
    text = _bounded_feed(text)
    if "BEGIN:VCALENDAR" not in text or "END:VCALENDAR" not in text:
        raise ValueError("provider returned no complete iCalendar document")
    # RFC 5545 line folding may split SUMMARY or DTSTART across physical lines.
    lines = re.sub(r"\r?\n[ \t]", "", text).splitlines()
    events, fields, parsed, rejected, nested = [], None, 0, 0, 0
    for line in lines:
        if line.strip() == "BEGIN:VEVENT":
            fields, nested = {}, 0
        elif line.strip() == "END:VEVENT" and fields is not None:
            if fields.get("STATUS", (None, ""))[1] != "CANCELLED":
                try:
                    params, value = fields["DTSTART"]
                    tz = re.search(r'(?:^|;)TZID=(?:"([^"]+)"|([^;]+))', params)
                    when = _ics_datetime(value, (tz.group(1) or tz.group(2)) if tz else None)
                    title = _ics_text(fields["SUMMARY"][1]).strip()
                    if not title or "RRULE" in fields:
                        raise ValueError("missing summary or unsupported recurrence")
                    parsed += 1
                    if _in_calendar_window(when, now):
                        events.append(_calendar_event(title, when, publisher, source_url, now, fields.get("UID", (None, None))[1]))
                except (KeyError, ValueError, LookupError):
                    rejected += 1
            fields = None
        elif fields is not None and line.startswith("BEGIN:"):
            nested += 1
        elif fields is not None and line.startswith("END:") and nested:
            nested -= 1
        elif fields is not None and not nested and ":" in line:
            name_params, value = line.split(":", 1)
            name, _, params = name_params.partition(";")
            fields[name.upper()] = (params, value)
    if not parsed:
        raise ValueError("no valid timed calendar events parsed; refusing an empty success")
    if fields is not None:
        rejected += 1
    return {"events": events, "parsed": parsed, "rejected": rejected, "complete": rejected == 0}


class _CalendarHTML(HTMLParser):
    """A small inert tree for the Federal Reserve's documented no-JS calendar."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = {"tag": "root", "attrs": {}, "children": []}
        self.stack, self.count = [self.root], 0
        self.feed(_bounded_feed(text))

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 20000 or len(self.stack) > 100:
            raise ValueError("calendar HTML exceeds parser limits")
        node = {"tag": tag, "attrs": dict(attrs), "children": []}
        self.stack[-1]["children"].append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1]["children"].append(data)


def _html_nodes(node, tag=None, css=None):
    for child in node["children"]:
        if isinstance(child, dict):
            if (tag is None or child["tag"] == tag) and (css is None or css in child["attrs"].get("class", "").split()):
                yield child
            yield from _html_nodes(child, tag, css)


def _html_text(node):
    if node["tag"] in ("script", "style"):
        return ""
    return " ".join(x if isinstance(x, str) else _html_text(x) for x in node["children"])


def parse_fed_calendar(text, year, month, source_url, now=None):
    now = now or now_utc()
    root = _CalendarHTML(text).root
    title = " ".join(_html_text(n) for n in _html_nodes(root, "title"))
    expected = datetime(year, month, 1).strftime("%B %Y")
    if expected.lower() not in title.lower() or not any(n["attrs"].get("id") == "article" for n in _html_nodes(root)):
        raise ValueError(f"Federal Reserve calendar page identity changed ({expected})")
    events, rows = [], 0
    for panel in _html_nodes(root, "div", "panel-body"):
        for row in panel["children"]:
            if not isinstance(row, dict) or "row" not in row["attrs"].get("class", "").split():
                continue
            cells = {css: next(iter(_html_nodes(row, "div", css)), None) for css in ("col-xs-2", "col-xs-7", "col-xs-3")}
            if any(v is None for v in cells.values()):
                continue
            rows += 1
            desc = " ".join(_html_text(cells["col-xs-7"]).split())
            event_title = re.search(r"\bFOMC (Meeting|Minutes|Press Conference)\b", desc, re.I)
            if not event_title:
                continue
            clock = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*", _html_text(cells["col-xs-2"]), re.I)
            day = re.fullmatch(r"\s*(\d{1,2})\s*", _html_text(cells["col-xs-3"]))
            if not clock or not day:
                raise ValueError("FOMC release time/day missing; refusing to infer it")
            hour = int(clock.group(1)) % 12 + (12 if clock.group(3).lower() == "p" else 0)
            when = datetime(year, month, int(day.group(1)), hour, int(clock.group(2) or 0), tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
            if _in_calendar_window(when, now):
                events.append(_calendar_event(event_title.group(0), when, "Federal Reserve", source_url, now))
    if not rows:
        raise ValueError("Federal Reserve calendar layout changed; no release rows parsed")
    return events


async def _bls_calendar():
    return parse_ics_calendar((await http_get(BLS_CALENDAR_URL)).text, "BLS", BLS_CALENDAR_URL)


async def bea_calendar():
    # The official schedule page links this public ICS feed; no API key is needed.
    return parse_ics_calendar((await http_get(BEA_CALENDAR_URL)).text, "BEA", BEA_CALENDAR_URL)


async def fed_calendar():
    now = now_utc()
    index = _bounded_feed((await http_get(FED_CALENDAR_URL)).text)
    published = set(re.findall(r'/newsevents/(\d{4}-[a-z]+\.htm)', index, re.I))
    cursor, end = now.date().replace(day=1), (now + timedelta(days=CALENDAR_DAYS)).date()
    events, errors, pages = [], [], []
    while cursor <= end:
        page = f"{cursor.year}-{cursor.strftime('%B').lower()}.htm"
        url = f"https://www.federalreserve.gov/newsevents/{page}"
        if page not in published:
            errors.append(f"{page}: monthly calendar not published in official index")
        else:
            try:
                result = await http_get(url)
                events.extend(parse_fed_calendar(result.text, cursor.year, cursor.month, url, now))
                pages.append(url)
            except Exception as exc:
                errors.append(f"{page}: {type(exc).__name__}: {str(exc)[:120]}")
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return {"events": events, "complete": not errors, "errors": errors, "pages": pages}


async def bls_calendar():
    """Legacy registry interface; now combines three official, free US calendars."""
    now = now_utc()
    definitions = [("BLS", BLS_CALENDAR_URL, _bls_calendar), ("BEA", BEA_CALENDAR_URL, bea_calendar),
                   ("Federal Reserve", FED_CALENDAR_URL, fed_calendar)]
    results = await asyncio.gather(*(fn() for _, _, fn in definitions), return_exceptions=True)
    events, providers = {}, []
    for (name, url, _), result in zip(definitions, results):
        if isinstance(result, BaseException):
            providers.append({"publisher": name, "source_url": url, "status": "error", "error": f"{type(result).__name__}: {str(result)[:200]}", "retrieved_at": None})
            continue
        complete = result.get("complete", True)
        providers.append({"publisher": name, "source_url": url, "status": "ok" if complete else "partial", "retrieved_at": iso(now),
                          "events_in_window": len(result["events"]), "rejected": result.get("rejected", 0), "errors": result.get("errors", [])})
        for event in result["events"]:
            events[event["id"]] = event
    if all(p["status"] == "error" for p in providers):
        raise RuntimeError("; ".join(f"{p['publisher']}: {p['error']}" for p in providers))
    return {"events": sorted(events.values(), key=lambda e: (e["time"], e["title"])), "providers": providers,
            "coverage_complete": all(p["status"] == "ok" for p in providers), "retrieved_at": iso(now),
            "coverage": "Official US BLS, BEA and Federal Reserve schedules; US-only, not a global calendar",
            "window_start": iso(now - timedelta(hours=2)), "window_end": iso(now + timedelta(days=CALENDAR_DAYS)),
            "timezone_note": "Official UTC or US Eastern release times converted to UTC with daylight-saving rules",
            "source_note": "Scheduled times only; no analyst forecasts. Partial/failed feeds cannot establish that there are no events."}


# ---------------- News RSS ----------------
FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "Federal Reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
    "BBC World": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "BBC Business": "https://feeds.bbci.co.uk/news/business/rss.xml",
}

CATS = {
    "Macro & central banks": r"\b(fed|fomc|federal reserve|powell|rate cut|rate hike|interest rates?|inflation|cpi|ppi|jobs report|payrolls|ecb|boj|treasury yields?|recession|gdp)\b",
    "Geopolitical risk": r"\b(war|wars|invasion|missile|missiles|airstrike|sanctions?|conflict|military|tariffs?|ceasefire|nuclear|attack)\b",
    "Regulation & legal": r"\b(sec|cftc|regulators?|regulation|lawsuit|court|ban|bans|banned|legislation|bill|senate|congress|compliance|indicted|charges)\b",
    "ETF & institutional": r"\b(etf|etfs|blackrock|fidelity|inflows?|outflows?|institutional|treasury company|microstrategy|strategy inc)\b",
    "Security & exchanges": r"\b(hack|hacked|exploit|breach|stolen|drained|outage|insolvency|bankruptcy|withdrawals? halted|exchange)\b",
    "Bitcoin": r"\b(bitcoin|btc|crypto|cryptocurrency|halving|miners?)\b",
}
CRITICAL = r"\b(war|invasion|nuclear|emergency rate|hacked|exploit|bankruptcy|insolvency|withdrawals? halted|default)\b"
HIGH = r"\b(fomc|rate cut|rate hike|cpi|payrolls|sec|etf|etfs|tariffs?|sanctions?|missiles?|airstrike|hack|lawsuit|ban|bans)\b"
BULL = r"\b(approves?|approved|approval|inflows?|surges?|rall(y|ies)|record high|rate cut|adopts?|buys|accumulat\w*|bullish)\b"
BEAR = r"\b(hack|hacked|outflows?|bans?|banned|crash\w*|plunges?|lawsuit|sues|rate hike|sell-off|selloff|bearish|exploit|liquidat\w*)\b"


def classify(title, summary=""):
    text = f"{title} {summary}".lower()
    cats = [c for c, rx in CATS.items() if re.search(rx, text)]
    impact = "CRITICAL" if re.search(CRITICAL, text) else "HIGH" if re.search(HIGH, text) else "MEDIUM" if cats else "LOW"
    b, s = len(re.findall(BULL, text)), len(re.findall(BEAR, text))
    direction = "BULLISH KEYWORDS" if b > s else "BEARISH KEYWORDS" if s > b else "NEUTRAL"
    return cats or ["Business & global"], impact, direction


def _strip(s):
    value = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", "", s or "", flags=re.I | re.S)
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", value)).split())[:400]


def _article_url(value):
    """Keep public article links inert and deduplicate common tracking variants."""
    try:
        value = (value or "").strip()
        parts = urlsplit(value)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            return ""
        if any(ord(c) < 32 for c in value):
            return ""
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")]
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(query), ""))[:2000]
    except ValueError:
        return ""


def _xml_name(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _entry_text(entry, *names):
    for name in names:
        for child in entry:
            if _xml_name(child.tag) == name:
                return "".join(child.itertext()).strip()
    return ""


def _publication_time(value, now):
    if not value:
        return None, "missing publication time"
    try:
        try:
            result = email.utils.parsedate_to_datetime(value)
        except (ValueError, TypeError):
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("publication time is missing its timezone")
        result = result.astimezone(timezone.utc)
        if result > now + timedelta(seconds=SKEW_SEC * 12):
            return None, f"future publication time {iso(result)}"
        return result, None
    except (ValueError, TypeError, OverflowError):
        return None, f"unparseable publication time {value[:100]!r}"


def parse_news_feed(content, publisher, url, now=None):
    now = now or now_utc()
    content = _bounded_feed(content)
    declaration_check = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else content
    if re.search(r"<!\s*(DOCTYPE|ENTITY)\b", declaration_check.replace("\x00", ""), re.I):
        raise ValueError("RSS/Atom DTD and entity declarations are not accepted")
    root = ET.fromstring(content)
    if _xml_name(root.tag) not in ("rss", "rdf", "feed"):
        raise ValueError("provider returned no RSS/Atom feed")
    entries = [node for node in root.iter() if _xml_name(node.tag) in ("item", "entry")]
    out = []
    for entry in entries[:40]:
        title = _strip(_entry_text(entry, "title"))
        if not title:
            continue
        link = _entry_text(entry, "link")
        if not link:
            link = next((child.get("href", "") for child in entry if _xml_name(child.tag) == "link" and child.get("rel", "alternate") == "alternate"), "")
        link = _article_url(link)
        if not link:
            continue
        summary = _strip(_entry_text(entry, "description", "summary", "content"))
        published_raw = _entry_text(entry, "pubdate", "published", "date", "updated")
        published, error = _publication_time(published_raw, now)
        cats, impact, direction = classify(title, summary)
        out.append({"title": title, "link": link, "canonical_url": link, "summary": summary, "publisher": publisher,
                    "published_at": iso(published), "published_raw": published_raw[:150], "time_error": error,
                    "categories": cats, "impact": impact, "keyword_direction": direction,
                    "provider_guid": _entry_text(entry, "guid", "id")[:300], "source_url": url,
                    "retrieved_at": iso(now), "last_seen_at": iso(now),
                    "content_hash": hashlib.sha256(f"{title}|{summary}".encode()).hexdigest()})
    if not out:
        raise ValueError("no valid linked headlines parsed; refusing an empty success")
    return out, len(entries)


def news_fetcher(publisher, url):
    async def f():
        items, total = parse_news_feed((await http_get(url)).content, publisher, url)
        new = 0
        for item in items:
            key = hashlib.sha256(item["canonical_url"].encode()).hexdigest()
            legacy_key = hashlib.sha1(re.sub(r"[^a-z0-9]", "", item["title"].lower()).encode()).hexdigest()
            # Preserve existing review decisions and old title-hash records during rollout.
            res = await db.news_items.update_one({"$or": [{"_id": key}, {"canonical_url": item["canonical_url"]}, {"_id": legacy_key, "publisher": publisher}]},
                {"$set": item, "$setOnInsert": {"_id": key, "first_retrieved_at": item["retrieved_at"], "review_status": "UNREVIEWED"}}, upsert=True)
            new += 1 if res.upserted_id else 0
        return {"items": total, "accepted": len(items), "new": new, "time_errors": sum(bool(x["time_error"]) for x in items),
                "publisher": publisher, "source_url": url, "retrieved_at": iso(now_utc())}
    return f


# ---------------- Registry ----------------
def build_registry():
    S = Source
    reg = [
        S("delta_product", "delta", "Delta Exchange India contract spec", "BTCUSD perpetual", "tick USD, contract BTC", f"{DELTA}/v2/products/BTCUSD", 900, 7200, delta_product),
        S("delta_ticker", "delta", "Delta REST ticker + L2 book (WebSocket backup)", "BTCUSD perpetual", "USD", f"{DELTA}/v2/tickers/BTCUSD", 5, 120, delta_ticker),
        S("delta_candles", "delta", "Delta REST completed candles 5m/15m/1h/4h/1d", "BTCUSD perpetual", "USD, volume in contracts", f"{DELTA}/v2/history/candles", 15, 120, delta_candles),
        S("okx", "okx", "OKX public snapshot (ticker, book, trades, OI, funding, 15m candles)", "BTC-USDT-SWAP", "USDT", f"{OKX}/api/v5", 15, 120, okx_snapshot),
        S("fed_h10", "macro_usd", "Federal Reserve H.10 Nominal Broad Dollar Index", "JRXWTFB_N.B", "index", "https://www.federalreserve.gov/releases/h10/", 900, 3600, fed_h10),
        S("fed_h10_meta", "macro_usd", "Federal Reserve H.10 release metadata", "H.10 release page", "date", "https://www.federalreserve.gov/releases/h10/current/", 900, 3600, fed_h10_meta),
        S("fed_h15", "macro_y2", "Federal Reserve H.15 2-year Treasury yield", "RIFLGFCY02_N.B", "% per year", "https://www.federalreserve.gov/releases/h15/", 900, 3600, fed_h15),
        S("fed_h15_meta", "macro_y2", "Federal Reserve H.15 release metadata", "H.15 release page", "date", "https://www.federalreserve.gov/releases/h15/", 900, 3600, fed_h15_meta),
        S("eia_brent", "macro_brent", "EIA Europe Brent spot (daily)", "RBRTE", "USD/bbl", "https://www.eia.gov/dnav/pet/hist/RBRTED.htm", 900, 3600, eia_brent),
        S("eia_meta", "macro_brent", "EIA Brent release metadata", "RBRTED page", "date", "https://www.eia.gov/dnav/pet/hist/RBRTED.htm", 900, 3600, eia_meta),
        S("fng", "sentiment", "Alternative.me Fear & Greed Index", "Crypto F&G", "0-100", "https://api.alternative.me/fng/", 300, 90000, fear_greed),
        S("etf_bitbo", "etf", "Bitbo US spot Bitcoin ETF flows", "US spot BTC ETFs", "USD million", "https://bitbo.io/treasuries/etf-flows/", 900, 345600, bitbo_etf),
        S("funding_multi", "funding_all", "Multi-exchange OI-weighted funding (OKX, Deribit, Bitget, Gate, Delta)", "BTC perpetuals", "fraction / 8h → annualized %", "public exchange APIs", 300, 1800, funding_multi),
        S("bls", "calendar", "Official US release calendars: BLS, BEA and Federal Reserve", "US economic releases and FOMC", "event time", BLS_CALENDAR_URL, 1800, 21600, bls_calendar),
    ]
    for pub, url in FEEDS.items():
        reg.append(S("news_" + re.sub(r"\W", "", pub.lower()), "news", f"{pub} RSS", "headlines", "text", url, 300, 1800, news_fetcher(pub, url)))
    return {s.key: s for s in reg}


REGISTRY = build_registry()
