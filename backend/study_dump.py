"""Research data dump (one-off, not deployed): long candle history to ./dump/*.npz for offline study."""
import asyncio, os, sys, time, json
import httpx, numpy as np

OUT = "dump"; os.makedirs(OUT, exist_ok=True)
DELTA = "https://api.india.delta.exchange"
NOW = int(time.time()); NOW -= NOW % 300
META = {"now": NOW}


async def get(c, url, params, tries=5):
    for k in range(tries):
        try:
            r = await c.get(url, params=params)
            if r.status_code == 429:
                await asyncio.sleep(2 + k * 2); continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            err = e; await asyncio.sleep(1 + k)
    raise err


async def delta(c, symbol, res, sec, days, sem):
    start = NOW - days * 86400
    spans = [(s, min(NOW, s + 1900 * sec)) for s in range(start, NOW, 1900 * sec)]
    out = {}

    async def one(s, e):
        async with sem:
            try:
                j = await get(c, f"{DELTA}/v2/history/candles", {"symbol": symbol, "resolution": res, "start": s, "end": e})
            except Exception as ex:
                META.setdefault("errors", []).append(f"{symbol} {res} {s}: {str(ex)[:80]}"); return
            for x in j.get("result") or []:
                out[int(x["time"])] = (float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"]), float(x.get("volume") or 0))
    await asyncio.gather(*(one(s, e) for s, e in spans))
    ts = sorted(out)
    return np.array(ts, dtype=np.int64), np.array([out[t] for t in ts], dtype=np.float64).reshape(-1, 5)


async def binance(c, days, sem):
    step = 300 * 1000
    start = (NOW - days * 86400) * 1000
    spans = list(range(start, NOW * 1000, 1000 * step))
    out = {}
    base = None
    for b in ("https://data-api.binance.vision", "https://api.binance.com", "https://api.binance.us"):
        try:
            j = await get(c, f"{b}/api/v3/klines", {"symbol": "BTCUSDT", "interval": "5m", "limit": 5}, tries=2)
            if isinstance(j, list) and j: base = b; break
        except Exception as e:
            META.setdefault("errors", []).append(f"binance {b}: {str(e)[:80]}")
    META["binance_base"] = base
    if not base:
        return np.array([], dtype=np.int64), np.zeros((0, 5))

    async def one(s):
        async with sem:
            try:
                j = await get(c, f"{base}/api/v3/klines", {"symbol": "BTCUSDT", "interval": "5m", "startTime": s, "limit": 1000})
            except Exception as ex:
                META.setdefault("errors", []).append(f"binance {s}: {str(ex)[:80]}"); return
            for x in j:
                out[int(x[0]) // 1000] = (float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5]))
    await asyncio.gather(*(one(s) for s in spans))
    ts = sorted(t for t in out if t + 300 <= NOW)
    return np.array(ts, dtype=np.int64), np.array([out[t] for t in ts], dtype=np.float64).reshape(-1, 5)


async def main():
    sem = asyncio.Semaphore(6)
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "vantage-research/1.0"}) as c:
        jobs = {"delta_5m": delta(c, "BTCUSD", "5m", 300, 800, sem), "delta_1m": delta(c, "BTCUSD", "1m", 60, 340, sem),
                "delta_mark_1h": delta(c, "MARK:BTCUSD", "1h", 3600, 800, sem), "delta_funding_1h": delta(c, "FUNDING:BTCUSD", "1h", 3600, 800, sem),
                "delta_oi_1h": delta(c, "OI:BTCUSD", "1h", 3600, 800, sem), "binance_5m": binance(c, 3 * 365 + 40, sem)}
        res = dict(zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)))
    for k, v in res.items():
        if isinstance(v, Exception):
            META[k] = f"ERROR {str(v)[:120]}"; continue
        t, a = v
        np.savez_compressed(f"{OUT}/{k}.npz", t=t, a=a.astype(np.float32) if "1m" in k else a)
        META[k] = {"n": int(len(t)), "from": time.strftime("%Y-%m-%d", time.gmtime(int(t[0]))) if len(t) else None, "to": time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(t[-1]))) if len(t) else None,
                   "gaps": int((np.diff(t) != (300 if "5m" in k else 60 if "1m" in k else 3600)).sum()) if len(t) > 1 else 0}
    json.dump(META, open(f"{OUT}/meta.json", "w"), indent=1)
    print("DUMP_META " + json.dumps(META))


asyncio.run(main())
