"""Market Intelligence – phase 3: crash-risk score, recovery score, estimated liquidation zones and the backtest.

How the scores are kept honest:
  * Eight groups of evidence, each from a different kind of data. Chart signals are worth at most 20 of 100 points,
    so a falling price alone can never produce a high score.
  * A score above 40 needs 2 groups to agree, above 60 needs 4 and above 80 needs 5.
  * A group whose data is missing is left out and the score is taken over what is available; nothing is estimated.
  * Every rule and weight below was fixed before the backtest was run. The only numbers learned from data are the
    "top fifth / bottom fifth" cut-offs, and those come from the training years alone.
  * The backtest uses, for each past day, only what had been published by that day's close (publication delays are
    applied to ETF, macro and on-chain data). The newest fifth of the days is a test period nothing was fitted on.
The liquidation map is a model (labelled as estimated everywhere); exchange-reported liquidations stay in phase 1.
"""
import asyncio
import bisect
import logging
from datetime import date

from core import db, now_utc, iso
import intel
import intel2
import ta
from intel import get, reg, worker, fnum, STATE

log = logging.getLogger("intel3")
EPOCH = 719163                                           # date(1970, 1, 1).toordinal()
BANDS = ((20, "LOW"), (40, "MODERATE"), (60, "ELEVATED"), (80, "HIGH"), (100, "EXTREME"))
GROUPS = (("technical", "Chart support and trend", 20, True), ("spot", "Spot demand", 10, True), ("etf", "ETF flows", 15, True), ("macro", "US macro", 15, True),
          ("onchain", "On-chain", 15, True), ("sentiment", "Sentiment", 10, True), ("funding", "Funding (cost of leverage)", 5, True),
          ("live", "Live leverage and liquidations", 10, False))
QUANT = ("taker7", "y2_chg20", "usd_chg20", "liq_chg4w", "netflow7", "stable_chg30", "fund3")
MOVE, HORIZONS, MAIN_H, EMBARGO = 0.10, (3, 7, 14), 7, 14
HIST = {}                                                # history cache, refreshed every few hours


def band(score):
    return next(name for top, name in BANDS if score <= top)


def _o(d):
    return date.fromisoformat(d).toordinal()


def _d(o):
    return date.fromordinal(o).isoformat()


class AsOf:
    """A published series. get(day) gives the latest value that was already public on that day, or None if it is too old."""

    def __init__(self, obs, lag=0, max_age=7):
        obs = sorted(((o, v) for o, v in obs or [] if v is not None), key=lambda x: x[0])
        self.k, self.v, self.max_age = [o + lag for o, _ in obs], [v for _, v in obs], max_age

    def get(self, day):
        i = bisect.bisect_right(self.k, day) - 1
        return self.v[i] if i >= 0 and day - self.k[i] <= self.max_age else None


# ------------------------------------------------------------------ features (one row per day, no look-ahead)
def etf_feats(tot):
    days = sorted(tot or {})
    v, out, streak = [tot[d] for d in days], [], 0
    for j, d in enumerate(days):
        s = (v[j] > 0) - (v[j] < 0)
        streak = 0 if s == 0 else streak + s if streak * s > 0 else s
        out.append((_o(d), {"streak": streak, "s5": sum(v[j - 4:j + 1]) if j >= 4 else None, "s20": sum(v[j - 19:j + 1]) if j >= 19 else None}))
    return out


def change(obs, k, pct=False):
    """Change over k observations, dated at the later one."""
    obs = [(o, v) for o, v in obs or [] if v is not None]
    return [(obs[i][0], (obs[i][1] / obs[i - k][1] - 1) * 100 if pct else obs[i][1] - obs[i - k][1]) for i in range(k, len(obs)) if not pct or obs[i - k][1]]


def core_trend(obs):
    """Monthly core CPI index -> (month, +1 / 0 / -1): did year-on-year inflation rise or fall against the month before."""
    m = {d[:7]: v for d, v in obs or [] if v}
    num = lambda ym: int(ym[:4]) * 12 + int(ym[5:7])
    yoy = lambda ym: (m[ym] / m[f"{int(ym[:4]) - 1}{ym[4:]}"] - 1) * 100 if f"{int(ym[:4]) - 1}{ym[4:]}" in m else None
    out, months = [], sorted(m)
    for p, q in zip(months, months[1:]):
        a, b = yoy(p), yoy(q)
        if a is not None and b is not None and num(q) - num(p) == 1:
            out.append((_o(q + "-01"), 1 if b > a + 0.05 else -1 if b < a - 0.05 else 0))
    return out


def rolling(obs, k, fn=sum):
    obs = [(o, v) for o, v in obs or [] if v is not None]
    return [(obs[i][0], fn([x[1] for x in obs[i - k + 1:i + 1]])) for i in range(k - 1, len(obs))]


def _series(H):
    io = lambda key: [(_o(d), v) for d, v in H.get(key) or []]
    fin, fout = dict(io("flow_in")), dict(io("flow_out"))
    net = sorted((o, fin[o] - fout[o]) for o in set(fin) & set(fout) if fin[o] is not None and fout[o] is not None)
    return {"fng": AsOf(H.get("fng"), 0, 3), "etf": AsOf(etf_feats(H.get("etf")), 1, 6), "core": AsOf(core_trend(H.get("core")), 45, 50),
            "y2": AsOf(change(io("y2"), 20), 1, 6), "usd": AsOf(change(io("usd"), 20, True), 7, 10), "liq": AsOf(change(io("liq"), 4), 2, 10),
            "netflow7": AsOf(rolling(net, 7), 1, 4), "mvrv": AsOf(io("mvrv"), 1, 4), "stable": AsOf(change(io("stables"), 30, True), 1, 4),
            "fund3": AsOf(rolling(H.get("funding"), 3, lambda x: sum(x) / len(x)), 0, 2)}


def build_days(H):
    """One feature row per daily candle. A row uses only that day's and earlier candles, and only series values already
    published on that day (see the delays in _series)."""
    K = H["klines"]
    c, hi, lo, op, qv, tb = ([b[k] for b in K] for k in ("c", "h", "l", "o", "qv", "tb"))
    e50, e200, rsi = ta.ema(c, 50), ta.ema(c, 200), ta.rsi(c)
    S = _series(H)
    low20 = [min(lo[i - 20:i]) if i >= 20 else None for i in range(len(K))]
    high20 = [max(hi[i - 20:i]) if i >= 20 else None for i in range(len(K))]
    volr = [qv[i] / (sum(qv[i - 20:i]) / 20) if i >= 20 and sum(qv[i - 20:i]) > 0 else None for i in range(len(K))]
    rows = []
    for i, b in enumerate(K):
        day = b["d"]
        f = {"i": i, "d": day, "date": _d(day), "c": c[i], "h": hi[i], "l": lo[i], "partial": bool(b.get("partial")), "low20": low20[i], "ema50": e50[i], "ema200": e200[i],
             "ema50_prev5": e50[i - 5] if i >= 5 else None}
        if i >= 30:
            f["brk"] = next(({"level": low20[j], "vol": None if K[j].get("partial") else volr[j], "ago": i - j} for j in (i, i - 1, i - 2) if c[j] < low20[j] and c[i] < low20[j]), None)
            w = lo[i - 29:i + 1]
            j = i - 29 + w.index(min(w))
            f["low30"], f["low30_held"] = lo[j], i - j
            r, p = min(range(i - 4, i + 1), key=lambda x: c[x]), min(range(i - 30, i - 5), key=lambda x: c[x])
            f["div"] = None if rsi[r] is None or rsi[p] is None else bool(c[r] < c[p] and rsi[r] > rsi[p] + 3 and rsi[p] < 40)
            f["rsi"] = rsi[i]
            s7 = sum(qv[i - 6:i + 1])
            f["taker7"] = sum(tb[i - 6:i + 1]) / s7 * 100 if s7 > 0 else None
            s10 = sum(qv[i - 9:i + 1])
            f["upvol10"] = sum(qv[j] for j in range(i - 9, i + 1) if c[j] > op[j]) / s10 * 100 if s10 > 0 else None
            f["low10"] = min(lo[i - 9:i + 1])
            f["broke_out"] = any(c[j] > high20[j] for j in range(i - 9, i + 1))
        f["fng"], f["fng_3"], f["fng_7"] = S["fng"].get(day), S["fng"].get(day - 3), S["fng"].get(day - 7)
        e = S["etf"].get(day) or {}
        f["etf_streak"], f["etf5"], f["etf20"] = e.get("streak"), e.get("s5"), e.get("s20")
        f["core"], f["y2_chg20"], f["usd_chg20"], f["liq_chg4w"] = S["core"].get(day), S["y2"].get(day), S["usd"].get(day), S["liq"].get(day)
        f["netflow7"], f["mvrv"], f["stable_chg30"], f["fund3"] = S["netflow7"].get(day), S["mvrv"].get(day), S["stable"].get(day), S["fund3"].get(day)
        rows.append(f)
    return rows


def fit_thresholds(rows):
    """Bottom-fifth and top-fifth cut-offs of each measure, from the training rows only."""
    out = {}
    for k in QUANT:
        v = sorted(r[k] for r in rows if r.get(k) is not None)
        if len(v) >= 250:
            out[k] = [v[int(0.2 * (len(v) - 1))], v[int(0.8 * (len(v) - 1))]]
    return out


# ------------------------------------------------------------------ rules (fixed in advance)
def I(name, mx, on, text):
    return {"name": name, "max": mx, "on": None if on is None else bool(on), "text": text if on is not None else "No data"}


def Q(name, mx, v, thr, key, high, fmt, what):
    """On when the value is in the top (high) or bottom fifth of the training years."""
    if v is None or key not in thr:
        return I(name, mx, None, None)
    cut = thr[key][1 if high else 0]
    return I(name, mx, v >= cut if high else v <= cut, f"{what} {fmt(v)} ({'top' if high else 'bottom'} fifth of the training years starts at {fmt(cut)})")


p2 = lambda v: f"{v:.2f}%"
p1 = lambda v: f"{v:.1f}%"
sg = lambda v: f"{v:+.2f}"
btc = lambda v: f"{v:+,.0f} BTC"
bn = lambda v: f"{'+' if v >= 0 else '−'}${abs(v) / 1000:,.0f}B"
fr = lambda v: f"{v * 100:+.4f}%"
musd = lambda v: f"{'+' if v >= 0 else '−'}${abs(v):,.0f}M"


def _live_items(live, crash):
    if live is None:
        return []
    ls, c24, c4, w, feeds = live.get("long_share"), live.get("ch24"), live.get("ch4"), live.get("liq") or {}, live.get("feeds_ok")
    h1, d1 = w.get("1h") or {}, w.get("24h") or {}
    per_h = (d1.get("long_usd") or 0) / 24
    casc = live.get("cascade")
    if crash:
        return [I("Crowded longs", 3, None if ls is None else ls >= 0.65, f"{(ls or 0) * 100:.0f}% of futures accounts are long (crowded at 65% or more)"),
                I("Leverage building", 3, None if not c24 else c24["oi_pct"] >= 5 and c24["price_pct"] <= 1, f"Open interest {(c24 or {}).get('oi_pct', 0):+.1f}% in 24h with price {(c24 or {}).get('price_pct', 0):+.1f}%"),
                I("Long liquidation cascade", 4, None if not feeds else bool(casc and casc["side"] == "long"), f"Longs liquidated in the last hour: ${(h1.get('long_usd') or 0) / 1e6:.1f}M (24h average ${per_h / 1e6:.1f}M an hour)")]
    heavy = (d1.get("long_usd") or 0) >= 50e6 and (d1.get("long_usd") or 0) >= 2 * (d1.get("short_usd") or 0)
    return [I("Longs no longer crowded", 3, None if ls is None else ls <= 0.50, f"{(ls or 0) * 100:.0f}% of futures accounts are long (washed out at 50% or less)"),
            I("Open interest stabilising", 3, None if not c24 or not c4 else c24["oi_pct"] <= -5 and abs(c4["oi_pct"]) < 1, f"Open interest {(c24 or {}).get('oi_pct', 0):+.1f}% in 24h, {(c4 or {}).get('oi_pct', 0):+.1f}% in the last 4h"),
            I("Liquidation cascade ending", 4, None if not feeds else heavy and (h1.get("long_usd") or 0) <= 0.5 * per_h,
              f"Longs liquidated: ${(d1.get('long_usd') or 0) / 1e6:.0f}M in 24h, ${(h1.get('long_usd') or 0) / 1e6:.1f}M in the last hour")]


def crash_items(f, thr, live=None):
    c, brk, ok, st = f["c"], f.get("brk"), "brk" in f, f.get("etf_streak")
    below = f.get("ema200") is not None and c < f["ema200"]
    falling = f.get("ema50_prev5") is not None and f["ema50"] is not None and f["ema50"] < f["ema50_prev5"]
    d7 = None if f.get("fng") is None or f.get("fng_7") is None else f["fng"] - f["fng_7"]
    return {
        "technical": [I("Support broken", 10, brk is not None if ok else None, f"Price {c:,.0f} closed below the 20-day low {brk['level']:,.0f}" if brk else f"Price {c:,.0f} is holding above the 20-day low {f.get('low20') or 0:,.0f}"),
                      I("Break on heavy volume", 5, None if not ok or (brk and brk["vol"] is None) else bool(brk and brk["vol"] >= 1.3), f"The break came on {brk['vol']:.1f}x normal volume" if brk and brk["vol"] else "No support break on heavy volume"),
                      I("Long trend down", 5, None if f.get("ema200") is None or f.get("ema50_prev5") is None else below and falling,
                        f"Price is {'below' if below else 'above'} the 200-day average {f.get('ema200') or 0:,.0f}; the 50-day average is {'falling' if falling else 'rising'}")],
        "spot": [Q("Weak buying", 6, f.get("taker7"), thr, "taker7", False, p1, "Buyers took"),
                 I("Volume on down days", 4, None if f.get("upvol10") is None else f["upvol10"] <= 40, f"{f.get('upvol10') or 0:.0f}% of the last 10 days' volume came on up days (weak at 40% or less)")],
        "etf": [I("Outflow run", 6, None if st is None else st <= -3, f"{abs(st or 0)} {'outflow' if (st or 0) < 0 else 'inflow'} day{'s' if abs(st or 0) != 1 else ''} in a row (3 outflow days counts)"),
                I("Heavy 5-day outflow", 6, None if f.get("etf5") is None else f["etf5"] <= -500, f"5-day net flow {musd(f.get('etf5') or 0)} (heavy at −$500M)"),
                I("20-day flow negative", 3, None if f.get("etf20") is None else f["etf20"] < 0, f"20-day net flow {musd(f.get('etf20') or 0)}")],
        "macro": [I("Core inflation rising", 4, None if f.get("core") is None else f["core"] > 0, "Core inflation " + {1: "rose", 0: "was flat", -1: "fell"}.get(f.get("core"), "") + " in the latest published month"),
                  Q("2-year yield rising fast", 4, f.get("y2_chg20"), thr, "y2_chg20", True, sg, "2-year yield change over a month"),
                  Q("Dollar rising fast", 3, f.get("usd_chg20"), thr, "usd_chg20", True, p2, "Dollar index change over a month"),
                  Q("Liquidity draining", 4, f.get("liq_chg4w"), thr, "liq_chg4w", False, bn, "Net dollar liquidity over 4 weeks")],
        "onchain": [Q("Coins moving to exchanges", 6, f.get("netflow7"), thr, "netflow7", True, btc, "7-day exchange net flow"),
                    I("Holders in large profit", 5, None if f.get("mvrv") is None else f["mvrv"] >= 2.4, f"MVRV {f.get('mvrv') or 0:.2f} (elevated at 2.4 or more)"),
                    I("Stablecoin supply shrinking", 4, None if f.get("stable_chg30") is None else f["stable_chg30"] < 0, f"Stablecoin supply {f.get('stable_chg30') or 0:+.1f}% in 30 days")],
        "sentiment": [I("Mood worsening fast", 6, None if d7 is None else d7 <= -15, f"Fear & Greed {f.get('fng')} now, {d7 or 0:+d} in a week (a 15-point drop counts)"),
                      I("Extreme greed", 4, None if f.get("fng") is None else f["fng"] >= 75, f"Fear & Greed {f.get('fng')} (extreme greed at 75 or more)")],
        "funding": [Q("Longs paying up", 5, f.get("fund3"), thr, "fund3", True, fr, "3-day funding per 8h")],
        "live": _live_items(live, True),
    }


def recovery_items(f, thr, live=None):
    c, ok, st = f["c"], "low30" in f, f.get("etf_streak")
    above = f.get("ema50") is not None and c > f["ema50"]
    rising = f.get("ema50_prev5") is not None and f["ema50"] is not None and f["ema50"] >= f["ema50_prev5"]
    held = ok and 3 <= f["low30_held"] <= 10 and c > f["low30"] * 1.03
    d7 = None if f.get("fng") is None or f.get("fng_7") is None else f["fng"] - f["fng_7"]
    lift = f.get("fng") is not None and f.get("fng_3") is not None and f["fng"] <= 25 and f["fng"] > f["fng_3"]
    return {
        "technical": [I("Support holding", 8, held if ok else None, f"The 30-day low {f.get('low30') or 0:,.0f} was set {f.get('low30_held')} day(s) ago and price is {(c / f['low30'] - 1) * 100 if ok else 0:+.1f}% from it (needs 3 to 10 days and +3%)"),
                      I("Bullish RSI divergence", 6, f.get("div") if ok else None, "Price made a lower low while RSI made a higher low" if f.get("div") else "No bullish RSI divergence on the daily chart"),
                      I("Back above a rising 50-day average", 6, None if f.get("ema50_prev5") is None else above and rising, f"Price is {'above' if above else 'below'} the 50-day average {f.get('ema50') or 0:,.0f}, which is {'rising' if rising else 'falling'}")],
        "spot": [Q("Strong buying", 6, f.get("taker7"), thr, "taker7", True, p1, "Buyers took"),
                 I("Volume on up days", 4, None if f.get("upvol10") is None else f["upvol10"] >= 60, f"{f.get('upvol10') or 0:.0f}% of the last 10 days' volume came on up days (strong at 60% or more)")],
        "etf": [I("Inflows returning", 6, None if st is None else st >= 2, f"{abs(st or 0)} {'outflow' if (st or 0) < 0 else 'inflow'} day{'s' if abs(st or 0) != 1 else ''} in a row (2 inflow days counts)"),
                I("Heavy 5-day inflow", 6, None if f.get("etf5") is None else f["etf5"] >= 500, f"5-day net flow {musd(f.get('etf5') or 0)} (heavy at +$500M)"),
                I("20-day flow positive", 3, None if f.get("etf20") is None else f["etf20"] > 0, f"20-day net flow {musd(f.get('etf20') or 0)}")],
        "macro": [I("Core inflation falling", 4, None if f.get("core") is None else f["core"] < 0, "Core inflation " + {1: "rose", 0: "was flat", -1: "fell"}.get(f.get("core"), "") + " in the latest published month"),
                  Q("2-year yield falling fast", 4, f.get("y2_chg20"), thr, "y2_chg20", False, sg, "2-year yield change over a month"),
                  Q("Dollar falling fast", 3, f.get("usd_chg20"), thr, "usd_chg20", False, p2, "Dollar index change over a month"),
                  Q("Liquidity growing", 4, f.get("liq_chg4w"), thr, "liq_chg4w", True, bn, "Net dollar liquidity over 4 weeks")],
        "onchain": [Q("Coins leaving exchanges", 6, f.get("netflow7"), thr, "netflow7", False, btc, "7-day exchange net flow"),
                    I("Deep value", 5, None if f.get("mvrv") is None else f["mvrv"] < 1.0, f"MVRV {f.get('mvrv') or 0:.2f} (deep value below 1.0)"),
                    Q("Stablecoin supply growing fast", 4, f.get("stable_chg30"), thr, "stable_chg30", True, p1, "Stablecoin supply change in 30 days")],
        "sentiment": [I("Extreme fear starting to lift", 6, None if f.get("fng") is None or f.get("fng_3") is None else lift, f"Fear & Greed {f.get('fng')} now, {f.get('fng_3')} three days ago (needs 25 or less and rising)"),
                      I("Mood improving fast", 4, None if d7 is None else d7 >= 10, f"Fear & Greed {d7 or 0:+d} in a week (a 10-point rise counts)")],
        "funding": [Q("Funding reset", 5, f.get("fund3"), thr, "fund3", False, fr, "3-day funding per 8h")],
        "live": _live_items(live, False),
    }


def total(items, tested_only=False):
    """Score 0-100 over the groups that have data, with the agreement rule applied."""
    groups, earned, avail = [], 0, 0
    for key, label, weight, tested in GROUPS:
        its = items.get(key) or []
        if tested_only and not tested:
            continue
        a, p = sum(x["max"] for x in its if x["on"] is not None), sum(x["max"] for x in its if x["on"])
        groups.append({"key": key, "label": label, "weight": weight, "tested": tested, "available": a, "points": p, "items": its,
                       "status": "UNAVAILABLE" if a == 0 else "CONFIRMING" if p >= 0.4 * a else "QUIET"})
        earned, avail = earned + p, avail + a
    n = sum(g["status"] == "CONFIRMING" for g in groups)
    cap = 40 if n < 2 else 60 if n < 4 else 80 if n < 5 else 100
    raw = 100 * earned / avail if avail else None
    return {"score": None if raw is None else round(min(raw, cap)), "raw": None if raw is None else round(raw), "capped": raw is not None and raw > cap, "confirming": n,
            "available": avail, "groups": groups}


def stage(f):
    """Relief rally or confirmed reversal, from the daily chart only."""
    if f.get("ema50_prev5") is None or "low10" not in f:
        return {"key": "UNKNOWN", "text": "Not enough daily history"}
    c, up = f["c"], f["ema50"] >= f["ema50_prev5"]
    bounce = (c / f["low10"] - 1) * 100
    if c > f["ema50"] and up and f["broke_out"]:
        return {"key": "CONFIRMED", "text": "Confirmed trend reversal: price is above a rising 50-day average and has closed above a 20-day high in the last 10 days."}
    if c > f["ema50"] and up:
        return {"key": "UPTREND", "text": "Daily trend is up, with no fresh 20-day high in the last 10 days."}
    if bounce >= 5:
        return {"key": "RELIEF", "text": f"Relief rally only: price is {bounce:.1f}% above the 10-day low, but it is still {'below' if c <= f['ema50'] else 'above'} a {'falling' if not up else 'rising'} 50-day average. Not a confirmed reversal."}
    return {"key": "DOWN", "text": "No recovery yet: the daily trend is down and price has not bounced 5% from the 10-day low."}


# ------------------------------------------------------------------ backtest
def _metrics(pairs, cut, base):
    """pairs: (score, event, move) in date order. A warning is a day with score >= cut."""
    tp = sum(1 for s, e, _ in pairs if s >= cut and e)
    fp = sum(1 for s, e, _ in pairs if s >= cut and not e)
    fn = sum(1 for s, e, _ in pairs if s < cut and e)
    tn = len(pairs) - tp - fp - fn
    ep, hit, run, run_hit, gap = 0, 0, False, False, 0
    for s, e, _ in pairs:                                 # episodes: runs of warning days no more than 3 days apart
        if s >= cut:
            if not run:
                ep, run, run_hit = ep + 1, True, False
            if e and not run_hit:
                hit, run_hit = hit + 1, True
            gap = 0
        elif run:
            gap += 1
            run = gap <= 3
    prec = tp / (tp + fp) if tp + fp else None
    low = None
    if prec is not None and ep:                           # neighbouring warning days are not independent, so the evidence is counted in separate warning periods
        z = 1.645
        low = (prec + z * z / (2 * ep) - z * ((prec * (1 - prec) / ep + z * z / (4 * ep * ep)) ** 0.5)) / (1 + z * z / ep)
    return {"precision_low": low, "proven": bool(low is not None and base is not None and ep >= 8 and low > base), "warnings": tp + fp, "hits": tp, "false_alarms": fp, "missed": fn, "precision": prec, "recall": tp / (tp + fn) if tp + fn else None, "fpr": fp / (fp + tn) if fp + tn else None,
            "fnr": fn / (tp + fn) if tp + fn else None, "accuracy": (tp + tn) / len(pairs) if pairs else None, "lift": prec / base if prec is not None and base else None,
            "episodes": ep, "episodes_hit": hit}


def _split_report(pairs, bands_p, base_train, crash):
    n, ev = len(pairs), sum(1 for _, e, _ in pairs if e)
    base = ev / n if n else None
    warn = [m for s, _, m in pairs if s >= 41]
    brier = sum((bands_p[band(s)] - e) ** 2 for s, e, _ in pairs) / n if n else None
    return {"days": n, "events": ev, "base_rate": base, "warn41": _metrics(pairs, 41, base), "warn61": _metrics(pairs, 61, base), "brier": brier,
            "brier_base": sum((base_train - e) ** 2 for _, e, _ in pairs) / n if n else None,
            "avg_move_all": sum(m for _, _, m in pairs) / n if n else None, "avg_move_after_warning": sum(warn) / len(warn) if warn else None,
            "worst_move": (min if crash else max)((m for _, _, m in pairs), default=None),
            "bands": [{"band": name, "days": sum(1 for s, _, _ in pairs if band(s) == name), "events": sum(1 for s, e, _ in pairs if band(s) == name and e)} for _, name in BANDS]}


def _verdict(kind, t):
    w, word = t["warn41"], "fall" if kind == "crash" else "rise"
    if not t["days"]:
        return "No test days yet."
    if w["warnings"] < 20 or w["episodes"] < 8:
        return f"In the test period the score reached 41 or more on {w['warnings']} of {t['days']} days, in {w['episodes']} separate warning periods: too few to judge. Treat the score as unproven."
    msg = f"In the unseen test period a score of 41 or more was followed by a 10% {word} within 7 days on {w['precision'] * 100:.0f}% of days, against {t['base_rate'] * 100:.0f}% on an ordinary day"
    if w["proven"]:
        return msg + f" ({w['lift']:.1f} times the normal rate). With {w['episodes']} separate warning periods the gap is larger than chance alone would explain." + (" Most warnings were still false alarms." if w["precision"] < 0.5 else "")
    return msg + f". With {w['episodes']} separate warning periods that gap is within what chance alone can produce, so treat the score as unproven."


def run_backtest(H):
    """Score every past day with the rules above and compare with what happened next. Returns (report, thresholds)."""
    rows = build_days(H)
    done = [r["i"] for r in rows if not r["partial"]]
    V = [i for i in done if rows[i].get("ema200") is not None and rows[i].get("fng") is not None and "brk" in rows[i]]
    n = len(V)
    if n < 600:
        raise RuntimeError(f"only {n} usable days of history; the backtest needs 600")
    last, a, b = done[-1], int(n * 0.6), int(n * 0.8)
    thr = fit_thresholds([rows[i] for i in V[:a]])
    sc = {}
    for i in V:
        cr, rc = total(crash_items(rows[i], thr), True), total(recovery_items(rows[i], thr), True)
        if cr["available"] >= 50:
            sc[i] = (cr["score"], rc["score"])
    lo, hi = [r["l"] for r in rows], [r["h"] for r in rows]
    parts = {"train": V[:max(a - EMBARGO, 0)], "validation": V[a:max(b - EMBARGO, a)], "test": V[b:]}
    out = {"crash": {}, "recovery": {}}
    for kind, k in (("crash", 0), ("recovery", 1)):
        for h in HORIZONS:
            def pairs(idx):
                res = []
                for i in idx:
                    if i in sc and i + h <= last:
                        m = (min(lo[i + 1:i + h + 1]) if k == 0 else max(hi[i + 1:i + h + 1])) / rows[i]["c"] - 1
                        res.append((sc[i][k], m <= -MOVE if k == 0 else m >= MOVE, m))
                return res
            tr = pairs(parts["train"])
            base = sum(1 for _, e, _ in tr if e) / len(tr) if tr else 0
            bp = {}
            for _, name in BANDS:                         # probability per band from the training years, pulled toward the normal rate when a band is thin
                d = [e for s, e, _ in tr if band(s) == name]
                bp[name] = (sum(d) + 20 * base) / (len(d) + 20)
            out[kind][str(h)] = {"band_prob": bp, **{name: _split_report(pairs(idx), bp, base, k == 0) for name, idx in parts.items()}}
        out[kind]["verdict"] = _verdict(kind, out[kind][str(MAIN_H)]["test"])
    span = lambda idx: {"from": rows[idx[0]]["date"], "to": rows[idx[-1]]["date"], "days": len(idx)} if idx else {"from": None, "to": None, "days": 0}
    have = lambda k: any(r.get(k) is not None for r in rows[-400:])
    missing = [label for label, k in (("ETF flows", "etf5"), ("Core inflation", "core"), ("2-year yield", "y2_chg20"), ("Dollar index", "usd_chg20"), ("Net liquidity", "liq_chg4w"),
                                      ("Exchange flows", "netflow7"), ("MVRV", "mvrv"), ("Stablecoin supply", "stable_chg30"), ("Funding", "fund3")) if not have(k)]
    report = {"generated": iso(now_utc()), "days": n, "scored_days": len(sc), "move_pct": MOVE * 100, "main_horizon": MAIN_H, "horizons": list(HORIZONS), "embargo_days": EMBARGO,
              "splits": {k: span(v) for k, v in parts.items()}, "thresholds": thr, "missing": missing, **out,
              "series": [{"d": rows[i]["date"], "crash": sc[i][0], "recovery": sc[i][1], "c": rows[i]["c"]} for i in V[-120:] if i in sc]}
    return report, thr


# ------------------------------------------------------------------ live scores
def _confidence(res, test):
    cover = res["available"] / 100
    w = (test or {}).get("warn41") or {}
    if cover < 0.6:
        return "LOW", f"Only {cover * 100:.0f}% of the evidence has data right now."
    if (w.get("warnings") or 0) < 20 or (w.get("episodes") or 0) < 8:
        return "LOW", "The test period had too few warnings to prove the score."
    if not w.get("proven"):
        return "LOW", "In the test period the score was not clearly better than chance."
    if w["lift"] >= 2 and w["episodes"] >= 20 and cover >= 0.85:
        return "HIGH", f"In the test period warnings were followed by the move {w['lift']:.1f} times as often as normal."
    return "MEDIUM", f"In the test period warnings were followed by the move {w['lift']:.1f} times as often as normal."


def risk_view(rows, thr, report, live, prev=None):
    f = rows[-1]
    out = {"updated": iso(now_utc()), "price": f["c"], "day": f["date"], "day_complete": not f["partial"], "bands": [{"to": t, "name": nm} for t, nm in BANDS],
           "rule": "Chart signals are worth at most 20 of 100 points. Above 40 needs 2 groups to agree, above 60 needs 4, above 80 needs 5."}
    for kind, fn in (("crash", crash_items), ("recovery", recovery_items)):
        items = fn(f, thr, live)
        res, tested = total(items), total(items, True)
        if res["score"] is None:
            out[kind] = None
            continue
        h = ((report or {}).get(kind) or {}).get(str(MAIN_H)) or {}
        conf, why = _confidence(res, h.get("test"))
        bname = band(tested["score"])
        row = lambda part: next((x for x in ((h.get(part) or {}).get("bands") or []) if x["band"] == bname), None)
        reasons = sorted(({"group": g["label"], "text": x["text"], "points": x["max"], "tested": g["tested"]} for g in res["groups"] for x in g["items"] if x["on"]), key=lambda x: -x["points"])
        p = (prev or {}).get(kind)
        out[kind] = {"score": res["score"], "band": band(res["score"]), "raw": res["raw"], "capped": res["capped"], "confirming": res["confirming"], "groups_with_data": sum(g["status"] != "UNAVAILABLE" for g in res["groups"]),
                     "coverage_pct": res["available"], "tested_score": tested["score"], "tested_band": bname, "confidence": conf, "confidence_why": why, "reasons": reasons, "groups": res["groups"],
                     "previous": p, "change": None if not p or p.get("score") is None else res["score"] - p["score"],
                     "history_rate": {"band": bname, "prob_train": (h.get("band_prob") or {}).get(bname), "train": row("train"), "validation": row("validation"), "test": row("test")} if h else None,
                     "verdict": ((report or {}).get(kind) or {}).get("verdict")}
    if out.get("recovery"):
        out["recovery"]["stage"] = stage(f)
    return out


def live_inputs():
    d, l = STATE.get("deriv") or {}, intel.liq_summary()
    ch = d.get("change") or {}
    return {"long_share": d.get("long_share"), "ch24": ch.get("24h"), "ch4": ch.get("4h"), "liq": l["windows"], "cascade": l["cascade"], "feeds_ok": bool(l["feeds_ok"])}


# ------------------------------------------------------------------ history loaders
async def f_klines(pages=5):
    by, end = {}, None
    for _ in range(pages):
        p = {"symbol": "BTCUSDT", "interval": "1d", "limit": 1000}
        if end:
            p["endTime"] = end
        k = (await get("https://data-api.binance.vision/api/v3/klines", p)).json()
        for x in k:
            by[int(x[0]) // 86400000] = x
        if len(k) < 1000:
            break
        end = int(k[0][0]) - 1
    return by


def _kl(by):
    today = int(now_utc().timestamp()) // 86400
    return [{"d": EPOCH + d, "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "qv": float(x[7]), "tb": float(x[10]), "partial": d >= today} for d, x in sorted(by.items())]


async def f_fng():
    d = (await get("https://api.alternative.me/fng/", {"limit": "0"})).json()["data"]
    return sorted((EPOCH + int(x["timestamp"]) // 86400, int(x["value"])) for x in d)


async def f_funding():
    by, start = {}, 1567296000000                        # 2019-09-01
    for _ in range(14):
        k = (await get("https://fapi.binance.com/fapi/v1/fundingRate", {"symbol": "BTCUSDT", "startTime": start, "limit": 1000})).json()
        for x in k:
            by.setdefault(int(x["fundingTime"]) // 86400000, []).append(float(x["fundingRate"]))
        if len(k) < 1000:
            break
        start = int(k[-1]["fundingTime"]) + 1
    return sorted((EPOCH + d, sum(v) / len(v)) for d, v in by.items())


async def f_cm(metric):
    url, params, out = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics", {"assets": "btc", "metrics": metric, "frequency": "1d", "page_size": 10000, "start_time": "2017-06-01"}, []
    for _ in range(4):
        j = (await get(url, params)).json()
        out += [(x["time"][:10], fnum(x.get(metric))) for x in j.get("data", [])]
        url, params = j.get("next_page_url"), None
        if not url:
            break
    return sorted(out)


async def f_etf():
    if STATE.get("etf_all"):
        return STATE["etf_all"]
    return intel2.parse_farside((await get("https://farside.co.uk/bitcoin-etf-flow-all-data/")).text)[0]


async def load_history():
    """Fetch the long histories, run the backtest and refresh the live scores."""
    key = await intel2.fred_key()
    fred = lambda sid: intel2.fred_series(sid, key, 3700)
    names = ("fng", "etf", "core", "y2", "usd", "walcl", "tga", "rrp", "flow_in", "flow_out", "mvrv", "stables", "funding")
    by = await f_klines()
    res = await asyncio.gather(f_fng(), f_etf(), fred("CPILFESL"), fred("DGS2"), fred("DTWEXBGS"), fred("WALCL"), fred("WTREGEN"), fred("RRPONTSYD"),
                               f_cm("FlowInExNtv"), f_cm("FlowOutExNtv"), f_cm("CapMVRVCur"), intel2._stablecoins(4000), f_funding(), return_exceptions=True)
    got = {}
    for nm, r in zip(names, res):
        if isinstance(r, Exception):
            log.warning("risk history %s unavailable: %s", nm, r)
        else:
            got[nm] = r
    if all(k in got for k in ("walcl", "tga", "rrp")):
        tga = [(d, v * 1000 if v < 10_000 else v) for d, v in got["tga"]]
        got["liq"] = await asyncio.to_thread(intel2.net_liquidity, {"fed_assets": got["walcl"], "tga": tga, "rrp": got["rrp"]})
    HIST.clear()
    HIST.update(got, kl_by=by, klines=_kl(by), failed=[nm for nm, r in zip(names, res) if isinstance(r, Exception)])
    report, thr = await asyncio.to_thread(run_backtest, HIST)
    report["sources_failed"] = HIST["failed"]
    STATE["backtest"] = report
    await db.intel_cfg.update_one({"_id": "backtest"}, {"$set": {"report": report}}, upsert=True)
    await risk_tick(fresh=False)
    intel.mark("risk")


async def risk_tick(fresh=True):
    report = STATE.get("backtest")
    if "kl_by" not in HIST or not report:
        raise RuntimeError("history is still loading")
    if fresh:
        HIST["kl_by"].update(await f_klines(1))
        HIST["klines"] = _kl(HIST["kl_by"])
    rows = await asyncio.to_thread(build_days, HIST)
    now = int(now_utc().timestamp())
    old = await db.intel_risk.find_one({"_id": {"$lte": now - 86400, "$gte": now - 86400 - 3600}}, sort=[("_id", -1)])
    if old:
        prev = {k: {"score": old.get(k), "label": "24 hours ago"} for k in ("crash", "recovery")}
    else:
        y = next((s for s in reversed(report.get("series") or []) if s["d"] < rows[-1]["date"]), None)
        prev = {k: {"score": y[k], "label": f"at the close of {y['d']} (tested groups only)"} for k in ("crash", "recovery")} if y else None
    view = risk_view(rows, report["thresholds"], report, live_inputs(), prev)
    STATE["risk"] = view
    await db.intel_risk.update_one({"_id": now - now % 900}, {"$set": {"at": now_utc(), "crash": (view.get("crash") or {}).get("score"), "recovery": (view.get("recovery") or {}).get("score")}}, upsert=True)


# ------------------------------------------------------------------ estimated liquidation zones (a model, not exchange data)
TIERS = ((5, 0.25), (10, 0.30), (25, 0.25), (50, 0.15), (100, 0.05))       # assumed leverage mix of new positions
MMR = 0.005                                                                 # assumed maintenance margin


def liq_map(bars, oi, price, span=15):
    """bars: {t: (high, low, close)} hourly; oi: [(t, open interest in BTC)] hourly, oldest first.
    Each rise in open interest is treated as new longs and new shorts opened at that hour's average price, split over the
    leverage mix above. A level is removed once price has traded through it; when open interest falls, all remaining
    positions are scaled down by the same share."""
    levels, scale = [], 1.0                              # [liq price, size / scale at creation, side]
    ts = sorted(bars)
    for (t0, a), (t1, b) in zip(oi, oi[1:]):
        if t0 not in bars or a <= 0:
            continue
        h, l, c = bars[t0]
        levels = [x for x in levels if (x[0] < l if x[2] == "long" else x[0] > h)]
        if b < a:
            scale *= b / a
        elif b > a:
            entry, usd = (h + l + c) / 3, (b - a) * (h + l + c) / 3
            for lev, w in TIERS:
                levels.append([entry * (1 - 1 / lev + MMR), usd * w / scale, "long"])
                levels.append([entry * (1 + 1 / lev - MMR), usd * w / scale, "short"])
    if len(oi) >= 2:
        for t in (t for t in ts if t >= oi[-1][0]):       # hours after the last open-interest reading, including the running one
            h, l, _ = bars[t]
            levels = [x for x in levels if (x[0] < l if x[2] == "long" else x[0] > h)]
    levels = [x for x in levels if (x[0] < price if x[2] == "long" else x[0] > price)]
    bins = [{"from_pct": k, "to_pct": k + 1, "price_from": price * (1 + k / 100), "price_to": price * (1 + (k + 1) / 100), "long_usd": 0.0, "short_usd": 0.0} for k in range(-span, span)]
    for p, u, side in levels:
        k = int((p / price - 1) * 100 // 1) + span
        if 0 <= k < len(bins):
            bins[k]["long_usd" if side == "long" else "short_usd"] += u * scale
    within = lambda pct: {"pct": pct, "long_usd": sum(x[1] * scale for x in levels if x[2] == "long" and x[0] >= price * (1 - pct / 100)),
                          "short_usd": sum(x[1] * scale for x in levels if x[2] == "short" and x[0] <= price * (1 + pct / 100))}
    top = lambda key: [{"price_from": x["price_from"], "price_to": x["price_to"], "usd": x[key], "from_pct": x["from_pct"], "to_pct": x["to_pct"]} for x in sorted(bins, key=lambda x: -x[key])[:3] if x[key] > 0]
    w5 = within(5)
    lean = "BALANCED" if max(w5["long_usd"], w5["short_usd"]) == 0 or 0.67 <= (w5["long_usd"] or 1) / (w5["short_usd"] or 1) <= 1.5 else "LONGS" if w5["long_usd"] > w5["short_usd"] else "SHORTS"
    return {"price": price, "bins": bins, "within": [within(2), w5, within(10)], "clusters": {"long": top("long_usd"), "short": top("short_usd")}, "lean": lean,
            "hours": len(oi), "from": oi[0][0] if oi else None}


async def load_liq_est():
    k = (await get("https://fapi.binance.com/fapi/v1/klines", {"symbol": "BTCUSDT", "interval": "1h", "limit": 720})).json()
    o = (await get("https://fapi.binance.com/futures/data/openInterestHist", {"symbol": "BTCUSDT", "period": "1h", "limit": 500})).json()
    bars = {int(x[0]) // 1000: (float(x[2]), float(x[3]), float(x[4])) for x in k}
    oi = sorted((int(x["timestamp"]) // 1000, float(x["sumOpenInterest"])) for x in o)
    if len(oi) < 48 or not bars:
        raise RuntimeError("Binance returned too little open-interest history")
    price = bars[max(bars)][2]
    m = liq_map(bars, oi, price)
    STATE["liq_est"] = {**m, "updated": iso(now_utc()), "source": "Model on Binance BTCUSDT futures: hourly open interest and prices",
                        "assumptions": [f"New positions use this leverage mix: {', '.join(f'{lev}x {int(w * 100)}%' for lev, w in TIERS)}.", f"Maintenance margin {MMR * 100:.1f}%.",
                                        "Every rise in open interest is counted as equal new longs and new shorts at that hour's average price.",
                                        f"Only positions opened in the last {len(oi) // 24} days on Binance are modelled. Older positions and other exchanges are not."],
                        "note": "ESTIMATED. Exchanges do not publish traders' real liquidation prices, so these zones are a model built on the assumptions listed. Use them as a rough map, not as fact."}


# ------------------------------------------------------------------ wiring
def public():
    return {"risk": STATE.get("risk"), "backtest": STATE.get("backtest"), "liq_est": STATE.get("liq_est")}


def register():
    reg("risk_history", "Risk history and backtest (Binance, FRED, Farside, Coin Metrics, DefiLlama, Alternative.me)", "risk", "Re-run every 6 hours")
    reg("risk", "Crash-risk and recovery scores (calculated on this server)", "risk", "Refreshed every 10 minutes")
    reg("liq_est", "Estimated liquidation zones (model on Binance open interest)", "liquidations (estimated)", "A model, not exchange data")


async def _restore():
    try:
        await db.intel_risk.create_index("at", expireAfterSeconds=40 * 86400)
        d = await db.intel_cfg.find_one({"_id": "backtest"})
        if d and d.get("report"):
            STATE["backtest"] = d["report"]
    except Exception as e:
        log.warning("risk restore: %s", e)


def jobs():
    register()

    async def history():
        await _restore()
        await asyncio.sleep(45)                           # let phase 1 and 2 load first (the ETF table is shared)
        await worker("risk_history", load_history, 6 * 3600)

    async def risk():
        await asyncio.sleep(400)
        await worker("risk", risk_tick, 600)
    return [history(), risk(), worker("liq_est", load_liq_est, 300)]
