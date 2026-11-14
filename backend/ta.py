"""Technical analysis engine (pure Python, completed candles only).

analyze(bars) returns every indicator, the detected events and a plain summary for ONE timeframe.
bars: list of {"t","o","h","l","c","v"} oldest first. Nothing here looks ahead: each value uses
only bars up to and including the last completed one. A bias needs several independent signals
to agree; no single indicator decides it.
"""
import math

MIN_BARS = 60


def sma(x, n):
    out, s = [None] * len(x), 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(x, n):
    out = [None] * len(x)
    if len(x) < n:
        return out
    k, e = 2 / (n + 1), sum(x[:n]) / n
    out[n - 1] = e
    for i in range(n, len(x)):
        e = x[i] * k + e * (1 - k)
        out[i] = e
    return out


def rsi(c, n=14):
    out = [None] * len(c)
    if len(c) <= n:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        g += max(d, 0)
        l += max(-d, 0)
    g, l = g / n, l / n
    out[n] = 100 - 100 / (1 + g / l) if l else 100.0
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
        out[i] = 100 - 100 / (1 + g / l) if l else 100.0
    return out


def macd(c, fast=12, slow=26, sig=9):
    ef, es = ema(c, fast), ema(c, slow)
    line = [a - b if a is not None and b is not None else None for a, b in zip(ef, es)]
    start = next((i for i, v in enumerate(line) if v is not None), len(c))
    s = [None] * start + ema(line[start:], sig)
    hist = [a - b if a is not None and b is not None else None for a, b in zip(line, s)]
    return line, s, hist


def true_range(b):
    return [b[0]["h"] - b[0]["l"]] + [max(x["h"] - x["l"], abs(x["h"] - p["c"]), abs(x["l"] - p["c"])) for p, x in zip(b, b[1:])]


def wilder(x, n):
    out = [None] * len(x)
    if len(x) < n + 1:
        return out
    a = sum(x[1:n + 1]) / n
    out[n] = a
    for i in range(n + 1, len(x)):
        a = (a * (n - 1) + x[i]) / n
        out[i] = a
    return out


def atr(b, n=14):
    return wilder(true_range(b), n)


def adx(b, n=14):
    """Returns (adx, +DI, -DI) series."""
    m = len(b)
    pdm, ndm = [0.0] * m, [0.0] * m
    for i in range(1, m):
        up, dn = b[i]["h"] - b[i - 1]["h"], b[i - 1]["l"] - b[i]["l"]
        pdm[i] = up if up > dn and up > 0 else 0.0
        ndm[i] = dn if dn > up and dn > 0 else 0.0
    tr_s, p_s, n_s = wilder(true_range(b), n), wilder(pdm, n), wilder(ndm, n)
    pdi = [100 * p / t if t else None for p, t in zip(p_s, tr_s)]
    ndi = [100 * q / t if t else None for q, t in zip(n_s, tr_s)]
    dx = [100 * abs(p - q) / (p + q) if p is not None and q is not None and p + q else None for p, q in zip(pdi, ndi)]
    start = next((i for i, v in enumerate(dx) if v is not None), m)
    vals = dx[start:]
    out = [None] * m
    if len(vals) >= n:
        a = sum(vals[:n]) / n
        out[start + n - 1] = a
        for i in range(n, len(vals)):
            a = (a * (n - 1) + vals[i]) / n
            out[start + i] = a
    return out, pdi, ndi


def bollinger(c, n=20, k=2.0):
    mid = sma(c, n)
    up, lo = [None] * len(c), [None] * len(c)
    for i in range(n - 1, len(c)):
        w = c[i - n + 1:i + 1]
        sd = math.sqrt(sum((v - mid[i]) ** 2 for v in w) / n)
        up[i], lo[i] = mid[i] + k * sd, mid[i] - k * sd
    return mid, up, lo


def obv(b):
    out, s = [0.0], 0.0
    for p, x in zip(b, b[1:]):
        s += x["v"] if x["c"] > p["c"] else -x["v"] if x["c"] < p["c"] else 0
        out.append(s)
    return out


def session_vwap(b, sec):
    """VWAP anchored at 00:00 UTC for intraday bars; rolling 20 bars for daily and above."""
    if sec >= 86400:
        w = b[-20:]
    else:
        day = b[-1]["t"] // 86400
        w = [x for x in b if x["t"] // 86400 == day]
        if len(w) < 3:
            w = b[-max(3, min(len(b), 86400 // sec)):]
    vol = sum(x["v"] for x in w)
    return sum((x["h"] + x["l"] + x["c"]) / 3 * x["v"] for x in w) / vol if vol > 0 else None


def volume_profile(b, bins=24, window=200):
    w = b[-window:]
    lo, hi = min(x["l"] for x in w), max(x["h"] for x in w)
    if hi <= lo:
        return None
    step = (hi - lo) / bins
    vol = [0.0] * bins
    for x in w:
        a, z = int((x["l"] - lo) / step), int(min(bins - 1, (x["h"] - lo) / step))
        share = x["v"] / (z - a + 1)
        for k in range(max(0, a), z + 1):
            vol[k] += share
    tot = sum(vol)
    if tot <= 0:
        return None
    poc = max(range(bins), key=lambda k: vol[k])
    a = z = poc
    acc = vol[poc]
    while acc < 0.7 * tot and (a > 0 or z < bins - 1):
        left, right = (vol[a - 1] if a > 0 else -1), (vol[z + 1] if z < bins - 1 else -1)
        if right >= left:
            z += 1; acc += vol[z]
        else:
            a -= 1; acc += vol[a]
    return {"poc": lo + (poc + 0.5) * step, "value_area_low": lo + a * step, "value_area_high": lo + (z + 1) * step, "bars": len(w),
            "levels": [{"price": lo + (k + 0.5) * step, "share": v / tot} for k, v in enumerate(vol)]}


def pivots(b, k=3):
    """Confirmed swing highs / lows: a bar that is the extreme of k bars on each side."""
    hi, lo = [], []
    for i in range(k, len(b) - k):
        left, right = b[i - k:i], b[i + 1:i + k + 1]
        # ties (common with a 0.5 tick): the first bar of an equal high / low is the swing
        if all(b[i]["h"] > x["h"] for x in left) and all(b[i]["h"] >= x["h"] for x in right):
            hi.append((i, b[i]["h"]))
        if all(b[i]["l"] < x["l"] for x in left) and all(b[i]["l"] <= x["l"] for x in right):
            lo.append((i, b[i]["l"]))
    return hi, lo


def structure(hi, lo):
    if len(hi) < 2 or len(lo) < 2:
        return {"label": "NOT ENOUGH SWINGS", "trend": "UNCLEAR", "highs": None, "lows": None}
    h = "Higher High" if hi[-1][1] > hi[-2][1] else "Lower High"
    l = "Higher Low" if lo[-1][1] > lo[-2][1] else "Lower Low"
    trend = "UP" if (h, l) == ("Higher High", "Higher Low") else "DOWN" if (h, l) == ("Lower High", "Lower Low") else "MIXED"
    return {"label": f"{h}, {l}", "trend": trend, "highs": h, "lows": l, "last_high": hi[-1][1], "last_low": lo[-1][1]}


def zones(hi, lo, price, a, n=4):
    """Support / resistance zones: swing points that cluster within half an ATR. More touches = stronger."""
    pts = sorted([p for _, p in hi[-40:]] + [p for _, p in lo[-40:]])
    width = max(a * 0.5, price * 0.0005)
    groups, cur = [], []
    for p in pts:
        if cur and p - cur[-1] > width:
            groups.append(cur); cur = []
        cur.append(p)
    if cur:
        groups.append(cur)
    z = [{"price": sum(g) / len(g), "low": g[0], "high": g[-1], "touches": len(g)} for g in groups]
    sup = sorted([x for x in z if x["price"] < price], key=lambda x: -x["price"])[:n]
    res = sorted([x for x in z if x["price"] > price], key=lambda x: x["price"])[:n]
    return sup, res


def fib(b, window=120):
    w = b[-window:]
    ih = max(range(len(w)), key=lambda i: w[i]["h"])
    il = min(range(len(w)), key=lambda i: w[i]["l"])
    hi, lo = w[ih]["h"], w[il]["l"]
    if hi <= lo:
        return None
    up = il < ih   # the swing ran low -> high, so retracements are measured down from the high
    lv = {str(r): (hi - (hi - lo) * r) if up else (lo + (hi - lo) * r) for r in (0.236, 0.382, 0.5, 0.618, 0.786)}
    return {"swing_high": hi, "swing_low": lo, "direction": "UP" if up else "DOWN", "levels": lv, "bars": len(w)}


def _cross(a, b, look=3):
    """+1 if a crossed above b within the last `look` bars, -1 if below, else 0."""
    for i in range(len(a) - 1, max(len(a) - 1 - look, 0), -1):
        if None in (a[i], b[i], a[i - 1], b[i - 1]):
            continue
        if a[i - 1] <= b[i - 1] and a[i] > b[i]:
            return 1
        if a[i - 1] >= b[i - 1] and a[i] < b[i]:
            return -1
    return 0


def divergence(b, r, hi, lo, recent=30):
    """Regular RSI divergence on the last two confirmed swings (the latest must be recent)."""
    n = len(b)
    if len(lo) >= 2 and lo[-1][0] >= n - recent and r[lo[-1][0]] is not None and r[lo[-2][0]] is not None:
        if lo[-1][1] < lo[-2][1] and r[lo[-1][0]] > r[lo[-2][0]] + 1:
            return "BULLISH"
    if len(hi) >= 2 and hi[-1][0] >= n - recent and r[hi[-1][0]] is not None and r[hi[-2][0]] is not None:
        if hi[-1][1] > hi[-2][1] and r[hi[-1][0]] < r[hi[-2][0]] - 1:
            return "BEARISH"
    return None


def analyze(bars, sec):
    n = len(bars)
    if n < MIN_BARS:
        return {"status": "INSUFFICIENT DATA", "bars": n, "needed": MIN_BARS}
    c = [x["c"] for x in bars]
    v = [x["v"] for x in bars]
    last = bars[-1]
    px = last["c"]
    e = {k: ema(c, k) for k in (20, 50, 100, 200)}
    s = {k: sma(c, k) for k in (50, 100, 200)}
    r = rsi(c)
    ml, ms, mh = macd(c)
    bm, bu, bl = bollinger(c)
    a = atr(bars)
    ad, pdi, ndi = adx(bars)
    ob = obv(bars)
    vw = session_vwap(bars, sec)
    hi, lo = pivots(bars)
    st = structure(hi, lo)
    av = a[-1] or 0.0
    sup, res = zones(hi, lo, px, av)
    vavg = sum(v[-21:-1]) / 20 if n > 21 else None
    vr = v[-1] / vavg if vavg else None
    ph, pl = max(x["h"] for x in bars[-21:-1]), min(x["l"] for x in bars[-21:-1])
    ev = []

    def add(kind, side, text):
        ev.append({"kind": kind, "side": side, "text": text})

    if px > ph:
        add("breakout", "bull", f"Close {px:,.1f} above the 20-bar high {ph:,.1f}" + (f", volume {vr:.1f}x normal (confirmed)" if vr and vr >= 1.5 else ", volume not confirming" if vr else ""))
    if px < pl:
        add("breakdown", "bear", f"Close {px:,.1f} below the 20-bar low {pl:,.1f}" + (f", volume {vr:.1f}x normal (confirmed)" if vr and vr >= 1.5 else ", volume not confirming" if vr else ""))
    x = _cross(ml, ms)
    if x:
        add("macd_cross", "bull" if x > 0 else "bear", f"MACD crossed {'above' if x > 0 else 'below'} its signal line")
    x = _cross(e[20], e[50])
    if x:
        add("ema_cross", "bull" if x > 0 else "bear", f"EMA20 crossed {'above' if x > 0 else 'below'} EMA50")
    x = _cross(s[50], s[200])
    if x:
        add("ma_cross", "bull" if x > 0 else "bear", "SMA50 crossed above SMA200 (golden cross)" if x > 0 else "SMA50 crossed below SMA200 (death cross)")
    dv = divergence(bars, r, hi, lo)
    if dv:
        add("divergence", "bull" if dv == "BULLISH" else "bear", f"{dv.title()} RSI divergence on the last two swings")
    if r[-1] is not None and r[-1] >= 70:
        add("rsi", "bear", f"RSI {r[-1]:.0f}: overbought")
    if r[-1] is not None and r[-1] <= 30:
        add("rsi", "bull", f"RSI {r[-1]:.0f}: oversold")
    prev_trend = structure(hi[:-1], lo[:-1])["trend"] if len(hi) > 2 and len(lo) > 2 else None
    if prev_trend in ("UP", "DOWN") and st["trend"] in ("UP", "DOWN") and prev_trend != st["trend"]:
        add("reversal", "bull" if st["trend"] == "UP" else "bear", f"Swing structure turned from {prev_trend} to {st['trend']}")

    # bias: independent votes, one per family
    votes = []

    def vote(name, val, text):
        votes.append({"name": name, "vote": val, "text": text})

    if e[20][-1] and e[50][-1]:
        vote("Trend (EMA20/50)", 1 if px > e[20][-1] > e[50][-1] else -1 if px < e[20][-1] < e[50][-1] else 0, f"price {px:,.0f}, EMA20 {e[20][-1]:,.0f}, EMA50 {e[50][-1]:,.0f}")
    if e[200][-1]:
        vote("Long trend (EMA200)", 1 if px > e[200][-1] else -1, f"price {'above' if px > e[200][-1] else 'below'} EMA200 {e[200][-1]:,.0f}")
    vote("Structure", 1 if st["trend"] == "UP" else -1 if st["trend"] == "DOWN" else 0, st["label"])
    if mh[-1] is not None:
        vote("Momentum (MACD)", 1 if mh[-1] > 0 and ml[-1] > 0 else -1 if mh[-1] < 0 and ml[-1] < 0 else 0, f"MACD {ml[-1]:+.1f}, histogram {mh[-1]:+.1f}")
    if r[-1] is not None:
        vote("RSI", 1 if 55 <= r[-1] < 70 else -1 if 30 < r[-1] <= 45 else 0, f"RSI {r[-1]:.0f}")
    if vw:
        vote("VWAP", 1 if px > vw else -1, f"price {'above' if px > vw else 'below'} VWAP {vw:,.0f}")
    if n > 21:
        vote("Volume flow (OBV)", 1 if ob[-1] > ob[-21] and px > c[-21] else -1 if ob[-1] < ob[-21] and px < c[-21] else 0, "OBV and price rising together" if ob[-1] > ob[-21] and px > c[-21] else "OBV and price falling together" if ob[-1] < ob[-21] and px < c[-21] else "OBV not confirming price")
    score = sum(x["vote"] for x in votes)
    strong = ad[-1] is not None and ad[-1] >= 25
    bias = "BULLISH" if score >= 3 else "BEARISH" if score <= -3 else "NEUTRAL"
    f2 = lambda q: None if q is None else round(q, 2)
    return {
        "status": "OK", "bars": n, "last_bar": last["t"], "price": px, "bias": bias, "score": score, "votes": votes,
        "trend_strength": "TRENDING" if strong else "WEAK / RANGING", "structure": st, "events": ev,
        "rsi": f2(r[-1]), "macd": {"line": f2(ml[-1]), "signal": f2(ms[-1]), "hist": f2(mh[-1])},
        "ema": {str(k): f2(x[-1]) for k, x in e.items()}, "sma": {str(k): f2(x[-1]) for k, x in s.items()},
        "bollinger": {"mid": f2(bm[-1]), "upper": f2(bu[-1]), "lower": f2(bl[-1]), "width_pct": f2((bu[-1] - bl[-1]) / bm[-1] * 100) if bm[-1] else None},
        "vwap": f2(vw), "atr": f2(av), "atr_pct": f2(av / px * 100) if px else None,
        "adx": f2(ad[-1]), "plus_di": f2(pdi[-1]), "minus_di": f2(ndi[-1]), "obv": f2(ob[-1]), "obv_change_20": f2(ob[-1] - ob[-21]) if n > 21 else None,
        "volume": {"last": v[-1], "avg20": f2(vavg), "ratio": f2(vr)},
        "support": sup, "resistance": res, "range20": {"high": ph, "low": pl},
        "volume_profile": volume_profile(bars), "fib": fib(bars),
    }


def quick_score(bars, sec):
    """The same vote total as analyze() and the ATR, without the levels and profiles (fast: used for calibration)."""
    c = [x["c"] for x in bars]
    n, px = len(c), bars[-1]["c"]
    e20, e50, e200 = ema(c, 20)[-1], ema(c, 50)[-1], ema(c, 200)[-1]
    r = rsi(c)[-1]
    ml, _, mh = macd(c)
    hi, lo = pivots(bars[-120:])
    st = structure(hi, lo)["trend"]
    vw = session_vwap(bars, sec)
    ob = obv(bars[-21:])
    s = 0
    if e20 and e50:
        s += 1 if px > e20 > e50 else -1 if px < e20 < e50 else 0
    if e200:
        s += 1 if px > e200 else -1
    s += 1 if st == "UP" else -1 if st == "DOWN" else 0
    if mh[-1] is not None:
        s += 1 if mh[-1] > 0 and ml[-1] > 0 else -1 if mh[-1] < 0 and ml[-1] < 0 else 0
    if r is not None:
        s += 1 if 55 <= r < 70 else -1 if 30 < r <= 45 else 0
    if vw:
        s += 1 if px > vw else -1
    if n > 21:
        s += 1 if ob[-1] > ob[0] and px > c[-21] else -1 if ob[-1] < ob[0] and px < c[-21] else 0
    return s, atr(bars[-60:])[-1]


def overlays(bars):
    """Series for the chart (aligned with bars)."""
    c = [x["c"] for x in bars]
    _, bu, bl = bollinger(c)
    return {"ema20": ema(c, 20), "ema50": ema(c, 50), "ema200": ema(c, 200), "bb_upper": bu, "bb_lower": bl}
