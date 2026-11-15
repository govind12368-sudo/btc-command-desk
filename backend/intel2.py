"""Market Intelligence – phase 2: US macro, spot-ETF flows, on-chain and sentiment.

Same rules as phase 1: every value carries its source and date, a failed source is shown as unavailable,
nothing is estimated, and metrics that only exist on paid plans are listed as "Needs API key".
These panels are information only: none of them changes the forecast probabilities until it has passed an
out-of-sample test.
"""
import asyncio
import csv
import io
import re
from datetime import datetime, timedelta, timezone

from core import db, now_utc, iso
import intel
from intel import get, reg, worker, fnum, STATE

FRED_API = "https://api.stlouisfed.org/fred/series/observations"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
KEY_RE = re.compile(r"^[0-9a-f]{32}$")
_fred_changed = asyncio.Event()

# key, FRED series, label, kind, unit, why it matters (plain words)
MACRO = (
    ("cpi", "CPIAUCSL", "CPI inflation", "yoy", "% a year", "Higher inflation keeps interest rates high, which usually weighs on Bitcoin."),
    ("core_cpi", "CPILFESL", "Core CPI inflation", "yoy", "% a year", "Inflation without food and energy; the Fed watches this closely."),
    ("pce", "PCEPI", "PCE inflation", "yoy", "% a year", "The Fed's preferred inflation measure."),
    ("fed_funds", "DFF", "Fed funds rate", "level", "%", "The US policy rate. Cuts usually help risk assets; hikes hurt."),
    ("y2", "DGS2", "US 2-year yield", "level", "%", "Shows where the market expects Fed rates to go."),
    ("y10", "DGS10", "US 10-year yield", "level", "%", "Rising long yields pull money away from risk assets."),
    ("usd", "DTWEXBGS", "US dollar index (broad, Fed)", "level", "index", "A stronger dollar usually weighs on Bitcoin. This is the Fed's broad index, not ICE DXY."),
    ("unemployment", "UNRATE", "Unemployment rate", "level", "%", "A rising rate pushes the Fed toward cuts but also signals a weaker economy."),
    ("nfp", "PAYEMS", "Non-farm payrolls (monthly change)", "diff", "thousand jobs", "Strong jobs growth lets the Fed keep rates high."),
    ("gdp", "A191RL1Q225SBEA", "Real GDP growth", "level", "% a year, quarterly", "How fast the US economy is growing."),
    ("fed_assets", "WALCL", "Fed balance sheet", "level", "USD million", "More Fed assets means more money in the system."),
    ("tga", "WTREGEN", "Treasury cash account (TGA)", "level", "USD million", "Cash parked at the Fed by the Treasury is out of the market."),
    ("rrp", "RRPONTSYD", "Reverse repo (RRP)", "level", "USD billion", "Cash parked at the Fed by money funds is out of the market."),
)
PAID = (("SOPR (profit or loss of coins moved)", "Glassnode or CryptoQuant paid plan"), ("Whale holdings (1,000+ BTC wallets)", "Glassnode or CryptoQuant paid plan"),
        ("Long-term and short-term holder supply", "Glassnode or CryptoQuant paid plan"), ("Miner reserves and miner selling", "CryptoQuant paid plan"))


def _day(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")


# ------------------------------------------------------------------ macro (FRED)
async def fred_key():
    d = await db.intel_cfg.find_one({"_id": "fred"})
    return (d or {}).get("key")


async def fred_status():
    d = await db.intel_cfg.find_one({"_id": "fred"}) or {}
    return {"set": bool(d.get("key")), "saved_at": d.get("saved_at")}


def parse_fred_json(j):
    return [(o["date"], float(o["value"])) for o in j.get("observations", []) if fnum(o.get("value")) is not None]


def parse_fred_csv(text):
    out = []
    for r in list(csv.reader(io.StringIO(text)))[1:]:
        if len(r) >= 2 and fnum(r[1]) is not None and re.match(r"\d{4}-\d{2}-\d{2}$", r[0]):
            out.append((r[0], float(r[1])))
    return out


async def fred_series(sid, key, days=800):
    """Observations oldest first. With a key the official API is used; without one the public chart download."""
    start = (now_utc() - timedelta(days=days)).strftime("%Y-%m-%d")
    if key:
        r = await intel.client().get(FRED_API, params={"series_id": sid, "api_key": key, "file_type": "json", "observation_start": start})
        if r.status_code == 400:
            raise RuntimeError("FRED rejected the API key")
        r.raise_for_status()
        obs = parse_fred_json(r.json())
    else:
        obs = parse_fred_csv((await get(FRED_CSV, {"id": sid, "cosd": start})).text)
    if not obs:
        raise RuntimeError("no observations returned")
    return sorted(obs)


async def save_fred_key(key):
    key = (key or "").strip().lower()
    if not key:
        await db.intel_cfg.delete_one({"_id": "fred"})
        _fred_changed.set()
        return {"set": False}
    if not KEY_RE.match(key):
        raise ValueError("A FRED key is 32 characters: digits 0-9 and letters a-f only.")
    await fred_series("DGS10", key)                      # proves the key works before it is stored
    await db.intel_cfg.update_one({"_id": "fred"}, {"$set": {"key": key, "saved_at": iso(now_utc())}}, upsert=True)
    _fred_changed.set()
    return {"set": True}


def macro_row(key, sid, label, kind, unit, why, obs):
    """Turn raw observations into one row: latest value, the one before it and the change."""
    if kind == "yoy":
        vals = [(d, (v / obs[i - 12][1] - 1) * 100) for i, (d, v) in enumerate(obs) if i >= 12 and obs[i - 12][1]]
    elif kind == "diff":
        vals = [(d, v - obs[i - 1][1]) for i, (d, v) in enumerate(obs) if i >= 1]
    else:
        vals = obs
    if len(vals) < 2:
        raise RuntimeError("not enough history")
    (d1, v1), (d0, v0) = vals[-1], vals[-2]
    gaps = sorted((datetime.strptime(b[0], "%Y-%m-%d") - datetime.strptime(a[0], "%Y-%m-%d")).days for a, b in zip(obs[-7:], obs[-6:]))
    gap = gaps[len(gaps) // 2] if gaps else 1
    return {"freq": "Q" if gap >= 80 else "M" if gap >= 25 else "W" if gap >= 6 else "D", "key": key, "series": sid, "label": label, "unit": unit, "why": why, "status": "OK", "value": v1, "date": d1, "previous": v0, "previous_date": d0, "change": v1 - v0,
            "history": [{"d": d, "v": v} for d, v in vals[-60:]]}


def macro_summary(rows):
    """Rule-based read of the macro backdrop. Each line states the fact it rests on; nothing is predicted."""
    r = {x["key"]: x for x in rows if x.get("status") == "OK"}
    out = []

    def add(k, up_text, down_text, up_is_headwind=True, eps=0.0):
        x = r.get(k)
        if not x or abs(x["change"]) <= eps:
            return
        up = x["change"] > 0
        out.append({"key": k, "side": "HEADWIND" if up == up_is_headwind else "TAILWIND", "text": up_text if up else down_text})

    add("core_cpi", "Core inflation rose in the latest month", "Core inflation fell in the latest month", eps=0.005)
    add("fed_funds", "The Fed funds rate went up", "The Fed funds rate went down")
    for k, name in (("y10", "10-year yield"), ("usd", "dollar index")):
        x = r.get(k)
        if x and len(x["history"]) >= 21:
            ch = x["value"] - x["history"][-21]["v"]
            if abs(ch) > (0.1 if k == "y10" else 0.5):
                out.append({"key": k, "side": "HEADWIND" if ch > 0 else "TAILWIND", "text": f"The {name} is {'up' if ch > 0 else 'down'} {abs(ch):.2f} over about a month"})
    liq = r.get("net_liquidity")
    if liq and len(liq["history"]) >= 5:
        ch = liq["value"] - liq["history"][-5]["v"]
        if abs(ch) > 20_000:
            out.append({"key": "net_liquidity", "side": "TAILWIND" if ch > 0 else "HEADWIND", "text": f"Net dollar liquidity is {'up' if ch > 0 else 'down'} ${abs(ch) / 1000:,.0f} billion over about a month"})
    h, t = sum(x["side"] == "HEADWIND" for x in out), sum(x["side"] == "TAILWIND" for x in out)
    label = "No clear macro lean" if not out or h == t else "Macro backdrop leans against Bitcoin" if h > t else "Macro backdrop leans in favour of Bitcoin"
    return {"label": label, "headwinds": h, "tailwinds": t, "points": out, "note": "Counted from the facts listed. A slow backdrop, not a trade signal."}


def net_liquidity(series):
    """Fed assets minus the Treasury account minus reverse repo, on the weekly dates of the first two (USD million).
    Reverse repo is daily and has no value on holidays, so the last value within a week before the date is used."""
    a, t = (dict(series.get(k) or []) for k in ("fed_assets", "tga"))
    rr = sorted(series.get("rrp") or [])
    out = []
    for d in sorted(set(a) & set(t)):
        prior = [(x, v) for x, v in rr if x <= d]
        if prior and (datetime.strptime(d, "%Y-%m-%d") - datetime.strptime(prior[-1][0], "%Y-%m-%d")).days <= 7:
            out.append((d, a[d] - t[d] - prior[-1][1] * 1000))
    return out


async def load_macro():
    key = await fred_key()
    res = await asyncio.gather(*[fred_series(m[1], key) for m in MACRO], return_exceptions=True)
    rows, raw = [], {}
    for m, r in zip(MACRO, res):
        if isinstance(r, Exception):
            rows.append({"key": m[0], "series": m[1], "label": m[2], "unit": m[4], "why": m[5], "status": "UNAVAILABLE", "reason": str(r)[:140]})
            continue
        if m[0] == "tga":                                 # FRED publishes this account in billions on some series and millions on others
            r = [(d, v * 1000 if v < 10_000 else v) for d, v in r]
        raw[m[0]] = r
        try:
            rows.append(macro_row(*m, r))
        except Exception as e:
            rows.append({"key": m[0], "series": m[1], "label": m[2], "unit": m[4], "why": m[5], "status": "UNAVAILABLE", "reason": str(e)[:140]})
    ok = {x["key"]: x for x in rows if x["status"] == "OK"}
    if "y10" in ok and "y2" in ok:
        v = ok["y10"]["value"] - ok["y2"]["value"]
        p = ok["y10"]["previous"] - ok["y2"]["previous"]
        rows.append({"key": "curve", "series": "DGS10 - DGS2", "label": "Yield curve (10-year minus 2-year)", "unit": "% points", "status": "OK", "value": v, "previous": p, "change": v - p,
                     "date": min(ok["y10"]["date"], ok["y2"]["date"]), "why": "Below zero has often come before recessions. Calculated here from the two yields.", "history": []})
    nl = net_liquidity(raw)
    if len(nl) >= 2:
        rows.append({"key": "net_liquidity", "series": "WALCL - WTREGEN - RRPONTSYD", "label": "Net dollar liquidity", "unit": "USD million", "status": "OK", "value": nl[-1][1], "previous": nl[-2][1],
                     "change": nl[-1][1] - nl[-2][1], "date": nl[-1][0], "why": "Fed assets minus the Treasury account and reverse repo. Rising liquidity has tended to help Bitcoin. Calculated here.",
                     "history": [{"d": d, "v": v} for d, v in nl[-60:]]})
    if not ok:
        first = next((x["reason"] for x in rows if x.get("reason")), "no series loaded")
        STATE["macro"] = {"rows": rows, "summary": None, "key_set": bool(key), "source": None, "updated": iso(now_utc())}
        raise RuntimeError(first)
    STATE["macro"] = {"rows": rows, "summary": macro_summary(rows), "key_set": bool(key), "updated": iso(now_utc()),
                      "source": "FRED API (St. Louis Fed), your key" if key else "FRED public chart download (no key). Add a key in Settings for the official API."}


async def macro_loop():
    """Refresh every hour, and at once when the key is saved or removed."""
    async def tick():
        await load_macro()
    async def waker():
        while True:
            await _fred_changed.wait()
            _fred_changed.clear()
            try:
                await load_macro()
                intel.mark("macro")
            except Exception as e:
                intel.mark("macro", False, e)
    asyncio.create_task(waker())
    await worker("macro", tick, 3600)


# ------------------------------------------------------------------ ETF flows
def _fs_num(x):
    """Farside number: '1,234.5' is an inflow, '(95.0)' an outflow; '-' or blank means not published."""
    m = re.match(r"^\(?(-?[\d,]+\.?\d*)\)?$", x or "")
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return -abs(v) if x.startswith("(") else v


def parse_farside(html):
    """Farside daily table -> ({day: total}, {day: {fund: flow}}), USD million. First cell '11 Jan 2024', last cell the total."""
    html = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    totals, funds, head = {}, {}, None
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        c = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", x)).replace("&nbsp;", " ").strip() for x in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)]
        if len(c) < 3:
            continue
        if head is None and "IBIT" in c and c[-1].lower().startswith("total"):
            head = c
            continue
        try:
            d = datetime.strptime(c[0], "%d %b %Y").date()
        except ValueError:
            continue
        v = _fs_num(c[-1])
        if v is None or d > now_utc().date():
            continue
        totals[d.isoformat()] = v
        if head and len(head) == len(c):
            funds[d.isoformat()] = {k: x for k, x in ((k, _fs_num(x)) for k, x in zip(head[1:-1], c[1:-1])) if x}
    if len(totals) < 3:
        raise RuntimeError("Farside table not readable (layout changed or automated access blocked)")
    return totals, funds


def etf_summary(rows):
    """rows: newest first [{date, total_musd}]. Sums and the current run of inflow or outflow days."""
    if not rows:
        return None
    tot = [r["total_musd"] for r in rows]
    streak, sign = 0, (tot[0] > 0) - (tot[0] < 0)
    for v in tot:
        if sign and ((v > 0) - (v < 0)) == sign:
            streak += 1
        else:
            break
    s5 = sum(tot[:5]) if len(tot) >= 5 else None
    label = "No clear flow" if s5 is None or abs(s5) < 100 else "Steady buying by ETF investors" if s5 > 0 else "Steady selling by ETF investors"
    return {"latest": rows[0], "sum_5d": s5, "sum_10d": sum(tot[:10]) if len(tot) >= 10 else None, "sum_20d": sum(tot[:20]) if len(tot) >= 20 else None, "days": len(tot),
            "streak": {"days": streak, "side": "INFLOW" if sign > 0 else "OUTFLOW" if sign < 0 else "FLAT"}, "label": label}


def etf_compare(primary, second, a="Farside", b="Bitbo", days=10):
    """Compare the two tables on the latest days both publish. A gap above $5M and 3% counts as a mismatch."""
    both = [(d, primary[d], second[d]) for d in sorted(set(primary) & set(second), reverse=True)[:days]]
    bad = [{"date": d, "primary": x, "second": y} for d, x, y in both if abs(x - y) > max(5.0, 0.03 * abs(x))]
    text = "No common days to compare yet" if not both else f"{b} agrees with {a} on all {len(both)} latest common days" if not bad else \
        f"{b} differs from {a} on {len(bad)} of the {len(both)} latest common days. {b} fills in fund by fund and is often incomplete for recent days, so {a} is the one shown."
    return {"status": "OK", "days_compared": len(both), "mismatches": bad[:5], "text": text}


async def load_etf():
    import sources
    fs, bb = await asyncio.gather(get("https://farside.co.uk/bitcoin-etf-flow-all-data/"), sources.bitbo_etf(), return_exceptions=True)
    try:
        if isinstance(fs, Exception):
            raise fs
        fs_tot, fs_funds = parse_farside(fs.text)
        STATE["etf_all"] = fs_tot                         # full daily history, reused by the backtest
        intel.mark("etf_farside")
    except Exception as e:
        fs_tot, fs_funds = None, {}
        intel.mark("etf_farside", False, e)
    bb_tot = None if isinstance(bb, Exception) else {r["date"]: r["total_musd"] for r in bb["rows"]}
    intel.mark("etf_bitbo", bb_tot is not None, bb if bb_tot is None else None)
    if fs_tot:
        for d in sorted(fs_tot)[-90:]:
            await db.intel_etf.update_one({"_id": d}, {"$set": {"total_musd": fs_tot[d], "funds": fs_funds.get(d, {}), "source": "farside"}}, upsert=True)
        src, name = "Farside Investors daily flow table", "farside"
        check = etf_compare(fs_tot, bb_tot) if bb_tot else {"status": "UNAVAILABLE", "reason": "Bitbo could not be read: " + str(bb)[:100]}
    elif bb_tot:
        for r in bb["rows"]:                              # never overwrite a day Farside already supplied
            cur = await db.intel_etf.find_one({"_id": r["date"]})
            if not cur or cur.get("source") != "farside":
                await db.intel_etf.update_one({"_id": r["date"]}, {"$set": {"total_musd": r["total_musd"], "funds": {}, "source": "bitbo"}}, upsert=True)
        src, name = "Bitbo ETF flow table (backup source; recent days can be incomplete)", "bitbo"
        check = {"status": "UNAVAILABLE", "reason": "Farside could not be read, so these figures rest on Bitbo alone and recent days may be incomplete"}
    else:
        raise RuntimeError("neither Farside nor Bitbo could be read")
    rows = [{"date": x["_id"], "total_musd": x["total_musd"], "funds": x.get("funds") or {}, "source": x.get("source")} async for x in db.intel_etf.find({}).sort("_id", -1).limit(60)]
    if name == "bitbo":
        rows = [r for r in rows if r["date"] in bb_tot] or rows
    STATE["etf"] = {**etf_summary(rows), "history": [{"date": r["date"], "total_musd": r["total_musd"]} for r in rows[::-1]], "unit": "USD million net flow per day (US spot Bitcoin ETFs)",
                    "source": src, "primary": name, "cross_check": check, "updated": iso(now_utc()),
                    "note": "Flows are published after the US market closes, so the latest row is the last finished US trading day that has been published."}


# ------------------------------------------------------------------ on-chain
def series_row(label, vals, unit, source, why, digits=0):
    """vals: [(day, value)] oldest first -> latest, 7-day and 30-day averages and how the week compares with the month."""
    vals = [(d, v) for d, v in vals if v is not None]
    if len(vals) < 8:
        raise RuntimeError("not enough history")
    v = [x[1] for x in vals]
    a7 = sum(v[-7:]) / 7
    a30 = sum(v[-30:]) / len(v[-30:])
    return {"label": label, "status": "OK", "value": v[-1], "date": vals[-1][0], "avg_7d": a7, "avg_30d": a30, "week_vs_month_pct": (a7 / a30 - 1) * 100 if a30 else None,
            "unit": unit, "source": source, "why": why, "digits": digits, "history": [{"d": d, "v": x} for d, x in vals[-90:]]}


async def _bc_chart(name):
    j = (await get(f"https://api.blockchain.info/charts/{name}", {"timespan": "120days", "format": "json", "sampled": "false"})).json()
    return [(_day(p["x"]), fnum(p["y"])) for p in j["values"]]


async def _coinmetrics(metric):
    j = (await get("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics", {"assets": "btc", "metrics": metric, "frequency": "1d", "page_size": 120, "paging_from": "end"})).json()
    return sorted((x["time"][:10], fnum(x.get(metric))) for x in j.get("data", []))


async def _stablecoins(n=120):
    j = (await get("https://stablecoins.llama.fi/stablecoincharts/all")).json()
    return [(_day(x["date"]), fnum((x.get("totalCirculatingUSD") or {}).get("peggedUSD"))) for x in j[-n:]]


async def _mempool():
    base = "https://mempool.space/api"
    fees, mp, da = [(await get(base + p)).json() for p in ("/v1/fees/recommended", "/mempool", "/v1/difficulty-adjustment")]
    return {"status": "OK", "fee_fast": fees.get("fastestFee"), "fee_hour": fees.get("hourFee"), "fee_economy": fees.get("economyFee"), "waiting_tx": mp.get("count"),
            "waiting_vmb": (mp.get("vsize") or 0) / 1e6, "difficulty_change_pct": da.get("difficultyChange"), "retarget_progress_pct": da.get("progressPercent"),
            "retarget_date": iso(datetime.fromtimestamp(da["estimatedRetargetDate"] / 1000, timezone.utc)) if da.get("estimatedRetargetDate") else None,
            "source": "mempool.space", "updated": iso(now_utc())}


def valuation(mvrv, price):
    """From MVRV (market value / realized value): realized price and NUPL follow exactly, so they are shown as calculated."""
    if not mvrv or mvrv <= 0:
        return None
    zone = "Deep value zone (price below what holders paid on average)" if mvrv < 1 else "Normal range" if mvrv < 2.4 else "Elevated (holders sit on large profits)" if mvrv < 3.5 else "Historically overheated"
    return {"mvrv": mvrv, "nupl": 1 - 1 / mvrv, "realized_price": price / mvrv if price else None, "zone": zone,
            "note": "Zones are the commonly used MVRV bands (below 1, 1 to 2.4, 2.4 to 3.5, above 3.5). They describe past cycles; they are not a timing tool."}


async def load_onchain():
    jobs = {
        "addresses": (_bc_chart("n-unique-addresses"), "Active addresses", "addresses a day", "blockchain.com", "More active addresses means more real use of the network."),
        "tx": (_bc_chart("n-transactions"), "Transactions", "a day", "blockchain.com", "Network activity."),
        "hashrate": (_bc_chart("hash-rate"), "Hash rate", "EH/s", "blockchain.com", "Miner computing power. A sharp fall can mean miners are under stress."),
        "miner_rev": (_bc_chart("miners-revenue"), "Miner revenue", "USD a day", "blockchain.com", "Falling revenue can force miners to sell coins."),
        "volume": (_bc_chart("estimated-transaction-volume-usd"), "On-chain transfer volume", "USD a day", "blockchain.com (estimate by blockchain.com)", "Value moved on the chain."),
        "stables": (_stablecoins(), "Stablecoin supply (all chains)", "USD", "DefiLlama", "Growing stablecoin supply is new buying power for crypto."),
        "ex_in": (_coinmetrics("FlowInExNtv"), "Exchange inflow", "BTC a day", "Coin Metrics community data", "Coins sent to exchanges, often to be sold."),
        "ex_out": (_coinmetrics("FlowOutExNtv"), "Exchange outflow", "BTC a day", "Coin Metrics community data", "Coins withdrawn from exchanges, often to be held."),
        "ex_supply": (_coinmetrics("SplyExNtv"), "Exchange reserves", "BTC", "Coin Metrics community data", "Coins held on exchanges. A falling reserve means less ready supply to sell."),
        "mvrv": (_coinmetrics("CapMVRVCur"), "MVRV", "ratio", "Coin Metrics community data", "Market value divided by what holders paid."),
    }
    res = dict(zip(jobs, await asyncio.gather(*[j[0] for j in jobs.values()], return_exceptions=True)))
    rows, ok = [], {}
    for k, (_, label, unit, src, why) in jobs.items():
        r = res[k]
        try:
            if isinstance(r, Exception):
                raise r
            if k == "hashrate":                              # blockchain.com reports TH/s; 1 EH/s = 1,000,000 TH/s
                r = [(d, v / 1e6 if v is not None else None) for d, v in r]
            row = series_row(label, r, unit, src, why, digits=2 if k == "mvrv" else 0)
            ok[k] = row
        except Exception as e:
            msg = str(e)[:140]
            need = any(s in msg for s in ("HTTP 401", "HTTP 403", "400")) and src.startswith("Coin Metrics")
            row = {"label": label, "status": "NEEDS_KEY" if need else "UNAVAILABLE", "reason": "Not in the free Coin Metrics data; needs a paid plan" if need else msg, "source": src, "why": why}
        rows.append({"key": k, **row})
    if "ex_in" in ok and "ex_out" in ok and ok["ex_in"]["date"] == ok["ex_out"]["date"]:
        net = ok["ex_in"]["value"] - ok["ex_out"]["value"]
        net7 = (ok["ex_in"]["avg_7d"] - ok["ex_out"]["avg_7d"]) * 7
        rows.append({"key": "ex_net", "label": "Exchange net flow", "status": "OK", "value": net, "date": ok["ex_in"]["date"], "sum_7d": net7, "unit": "BTC a day", "digits": 0,
                     "source": "Calculated here: inflow minus outflow (Coin Metrics community data)", "why": "Above zero: more coins arriving than leaving (selling pressure). Below zero: coins leaving exchanges."})
    rows += [{"key": "paid_%d" % i, "label": n, "status": "NEEDS_KEY", "reason": "Needs API key: " + p, "source": p} for i, (n, p) in enumerate(PAID)]
    price = (STATE["overview"].get("delta") or {}).get("price")
    try:
        mem = await _mempool()
    except Exception as e:
        mem = {"status": "UNAVAILABLE", "reason": str(e)[:140], "source": "mempool.space"}
    if not ok and mem.get("status") != "OK":
        raise RuntimeError("no on-chain source could be loaded")
    STATE["onchain"] = {"rows": rows, "mempool": mem, "valuation": valuation(ok["mvrv"]["value"], price) if "mvrv" in ok else None, "updated": iso(now_utc()),
                        "note": "Daily on-chain figures arrive one day late: the latest row is the last finished UTC day."}


# ------------------------------------------------------------------ sentiment
def fng_summary(obs):
    """obs newest first [{value, label, date}]."""
    if not obs:
        return None
    v = [o["value"] for o in obs]
    avg = lambda n: sum(v[:n]) / n if len(v) >= n else None
    now = v[0]
    read = ("Extreme fear. In past cycles this was more often near lows than highs, but it can last for weeks." if now <= 25 else "Fear." if now < 45 else "Neutral." if now <= 55 else
            "Greed." if now < 75 else "Extreme greed. In past cycles this was more often near highs than lows, but it can last for weeks.")
    return {"value": now, "label": obs[0]["label"], "date": obs[0]["date"], "yesterday": v[1] if len(v) > 1 else None, "week_ago": v[7] if len(v) > 7 else None,
            "avg_7d": avg(7), "avg_30d": avg(30), "read": read, "history": [{"d": o["date"][:10], "v": o["value"]} for o in obs[:60]][::-1]}


def news_summary(items):
    hi = [x for x in items if x.get("impact") in ("CRITICAL", "HIGH")]
    b = sum(x.get("keyword_direction") == "BULLISH KEYWORDS" for x in items)
    s = sum(x.get("keyword_direction") == "BEARISH KEYWORDS" for x in items)
    cats = {}
    for x in items:
        for c in x.get("categories") or []:
            cats[c] = cats.get(c, 0) + 1
    return {"total": len(items), "high_impact": len(hi), "bullish_words": b, "bearish_words": s, "neutral": len(items) - b - s,
            "tone": "No clear tone" if abs(b - s) < max(3, 0.1 * len(items)) else "More positive than negative headlines" if b > s else "More negative than positive headlines",
            "categories": sorted(cats.items(), key=lambda kv: -kv[1])[:6],
            "top": [{"title": x["title"], "publisher": x.get("publisher"), "link": x.get("link"), "time": x.get("published_at"), "impact": x.get("impact"), "direction": x.get("keyword_direction")} for x in hi[:8]],
            "note": "Tone counts simple positive and negative words in headlines. It does not understand context, so treat it as a rough gauge."}


async def load_sentiment():
    d = (await get("https://api.alternative.me/fng/", {"limit": "60"})).json()["data"]
    obs = [{"value": int(x["value"]), "label": x["value_classification"], "date": iso(datetime.fromtimestamp(int(x["timestamp"]), timezone.utc))} for x in d]
    cut = iso(now_utc() - timedelta(hours=24))
    items = [x async for x in db.news_items.find({"published_at": {"$gte": cut}, "time_error": None}).sort("published_at", -1).limit(400)]
    STATE["sentiment"] = {"fng": fng_summary(obs), "news": news_summary(items), "updated": iso(now_utc()), "fng_source": "Alternative.me Fear & Greed Index (daily)",
                          "news_source": "Headlines already collected by this site's news feeds, last 24 hours",
                          "unavailable": [{"label": "Social media sentiment (X, Reddit)", "reason": "Needs API key: paid social data plan (LunarCrush, Santiment)"},
                                          {"label": "Google search interest", "reason": "No free official API"}]}


# ------------------------------------------------------------------ wiring
def public():
    return {"macro": STATE.get("macro"), "etf": STATE.get("etf"), "onchain": STATE.get("onchain"), "sentiment": STATE.get("sentiment")}


def register():
    reg("macro", "US macro data (FRED, St. Louis Fed)", "macro", "Refreshed every hour; most series change daily, weekly or monthly")
    reg("etf", "US spot Bitcoin ETF flows (summary)", "etf", "Published after the US market closes")
    reg("etf_farside", "Farside ETF flow table (main source)", "etf")
    reg("etf_bitbo", "Bitbo ETF flow table (second source)", "etf", "Used to cross-check Farside, and as backup")
    reg("onchain", "On-chain data (blockchain.com, mempool.space, Coin Metrics, DefiLlama)", "onchain", "Daily figures, one day late")
    reg("sentiment", "Sentiment (Fear & Greed, news tone)", "sentiment")


def jobs():
    register()
    return [macro_loop(), worker("etf", load_etf, 1800), worker("onchain", load_onchain, 1800), worker("sentiment", load_sentiment, 600)]
