import math


def ema_series(vals, n):
    if len(vals) < n:
        return []
    k = 2 / (n + 1)
    out = [None] * (n - 1)
    e = sum(vals[:n]) / n
    out.append(e)
    for v in vals[n:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def atr_series(bars, n=14):
    trs = []
    for i, b in enumerate(bars):
        if i == 0:
            trs.append(b["h"] - b["l"])
        else:
            pc = bars[i - 1]["c"]
            trs.append(max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)))
    if len(trs) < n + 1:
        return None
    a = sum(trs[1:n + 1]) / n
    for t in trs[n + 1:]:
        a = (a * (n - 1) + t) / n
    return a


def rsi_wilder(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def frame_metrics(bars, res_sec, min_bars=51):
    """bars: completed, ascending, dicts t(open sec),o,h,l,c,v."""
    out = {"bars": len(bars), "valid": False, "issues": []}
    if len(bars) < min_bars:
        out["issues"].append(f"only {len(bars)} completed bars (need {min_bars})")
        return out
    recent = bars[-min_bars:]
    for a, b in zip(recent, recent[1:]):
        if b["t"] - a["t"] != res_sec:
            out["issues"].append(f"gap/duplicate between bars at {a['t']} and {b['t']}")
            break
    closes = [b["c"] for b in bars]
    e20, e50 = ema_series(closes, 20), ema_series(closes, 50)
    last = bars[-1]
    prior = bars[-21:-1]
    vw = bars[-20:]
    vsum = sum(b["v"] for b in vw)
    vprev = sum(b["v"] for b in prior) / 20 if prior else 0
    m = {
        "close": last["c"], "high": last["h"], "low": last["l"], "open": last["o"], "volume": last["v"],
        "bar_open": last["t"], "bar_close": last["t"] + res_sec,
        "ema20": e20[-1] if e20 else None, "ema50": e50[-1] if e50 else None,
        "ema20_prev": e20[-2] if len(e20) > 1 else None,
        "atr14": atr_series(bars[-60:], 14),
        "vwap20": (sum((b["h"] + b["l"] + b["c"]) / 3 * b["v"] for b in vw) / vsum) if vsum > 0 else None,
        "prior20_high": max(b["h"] for b in prior), "prior20_low": min(b["l"] for b in prior),
        "volume_ratio": (last["v"] / vprev) if vprev > 0 else None,
    }
    out.update(m)
    req = ["ema20", "ema50", "ema20_prev", "atr14", "vwap20", "volume_ratio"]
    bad = [k for k in req if m[k] is None or not math.isfinite(m[k])]
    if bad:
        out["issues"].append("non-finite indicators: " + ", ".join(bad))
    if m["atr14"] is not None and m["atr14"] <= 0:
        out["issues"].append("ATR14 not positive")
    out["valid"] = not out["issues"]
    return out


def trend_dir(m):
    if not m.get("valid"):
        return None
    c, e20, e50, p = m["close"], m["ema20"], m["ema50"], m["ema20_prev"]
    if c > e20 > e50 and e20 > p:
        return "UP"
    if c < e20 < e50 and e20 < p:
        return "DOWN"
    return "MIXED"


def aggregate(bars, res_from, res_to):
    out = {}
    for b in bars:
        k = b["t"] - b["t"] % res_to
        g = out.get(k)
        if g is None:
            out[k] = {"t": k, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"], "n": 1}
        else:
            g["h"] = max(g["h"], b["h"]); g["l"] = min(g["l"], b["l"]); g["c"] = b["c"]; g["v"] += b["v"]; g["n"] += 1
    need = res_to // res_from
    return [g for k, g in sorted(out.items()) if g["n"] == need]
