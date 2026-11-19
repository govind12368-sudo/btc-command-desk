"""26-month evidence for the Strategy Observer page.

Every Vantage scalp signal Sep 2024 - Nov 2026 (Delta BTCUSD 5m candles), with the data each page would have shown at the
signal candle's close, and the result of each way to trade it:
  follow  = signal side, stop 0.5% (1R), target 2R, 6h exit       (Trade Journal rule)
  fade    = opposite side, same stop / 2R / 6h                     (Plan A rule)
  switch  = day-type playbook: Range -> fade, trend with the signal -> follow, else none
  regime  = Regime Strategy rules (1.5R target, max 2 a day);  regime_rev = the same trades reversed
Fees: taker entry, maker at target, taker at stop/time, +18% GST. Stop wins a same-candle tie; fill-candle: only the stop.
"""
import json, math, sys
import numpy as np
import pandas as pd
sys.path.insert(0, "/home/claude/research")
import switch_replay as SR
from lib import agg, trend_state, map_htf, atr_wilder

b5, ct, E, SD, day = SR.b5, SR.ct, SR.E, SR.dr_sig, SR.day
T, O, H, L, C, V = b5["t"], b5["o"], b5["h"], b5["l"], b5["c"], b5["v"]
TK, MK, GST = 0.0005, 0.0002, 0.18
DAYLAB = {"RANGE": "Range", "TREND_UP": "Trend up", "TREND_DOWN": "Trend down", "MIXED": "Unclear", "NA": "Unclear"}

# higher timeframes (complete bars only) and their trend / ATR, mapped to the 5m bar that closes at the signal
tf = {}
for name, sec in (("15m", 900), ("1h", 3600), ("4h", 14400)):
    b = agg(b5, sec); tr, _, _ = trend_state(b)
    v, _ = map_htf(T, 300, b["t"], sec, tr.astype(float)); tf[name] = v
    if name == "1h":
        a1h, _ = map_htf(T, 300, b["t"], sec, atr_wilder(b))
k15 = T // 900; last = np.r_[k15[1:] != k15[:-1], True]; c15_t, c15 = k15[last], C[last]


def rsi_wilder(x, n=14):
    d = np.diff(x); g = np.clip(d, 0, None); l = np.clip(-d, 0, None); ag, al = g[:n].mean(), l[:n].mean()
    for i in range(n, len(d)):
        ag = (ag * (n - 1) + g[i]) / n; al = (al * (n - 1) + l[i]) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


TL = {1: "Up", -1: "Down", 0: "Mixed"}


def feats(i):
    jb = int(np.searchsorted(T, ct[i] - 300))
    if jb >= len(T) or T[jb] != ct[i] - 300 or jb < 8700:
        return None
    t0 = T[jb]; e = float(E[i]); d = round(e * 0.005 * 2) / 2
    rsi = rsi_wilder(np.r_[c15[c15_t < t0 // 900][-130:], C[jb]])
    lo = int(np.searchsorted(T, t0 - 86400)); hi24, lo24 = H[lo:jb].max(), L[lo:jb].min()
    j4 = int(np.searchsorted(T, t0 - 14400)); j30 = int(np.searchsorted(T, t0 - 30 * 86400))
    tr = {k: (TL.get(int(tf[k][jb])) if not np.isnan(tf[k][jb]) else None) for k in tf}
    vr = V[jb] / V[jb - 20:jb].mean() if V[jb - 20:jb].mean() > 0 else np.nan
    return dict(rsi=float(rsi), pos=float((e - lo24) / (hi24 - lo24)) if hi24 > lo24 else None, vr=float(vr), chg4h=float(C[jb] / C[j4] - 1),
                chg30=float(C[jb] / C[j30] - 1), stop_atr=float(d / a1h[jb]) if a1h[jb] > 0 else None, t15=tr["15m"], t1h=tr["1h"], t4h=tr["4h"], d=d,
                hour=int(((t0 + 300 + 19800) % 86400) // 3600))


def sim(i, side, rr, d):
    e = float(E[i]); sd = SD[i]; p0 = int(np.searchsorted(T, ct[i])); k = None
    for j in range(p0, min(p0 + 2, len(T))):
        if (sd > 0 and L[j] <= e) or (sd < 0 and H[j] >= e):
            k = j; break
    if k is None:
        return None
    st, tg = e - side * d, e + side * rr * d; q = math.floor(10000 / e * 1000) / 1000
    for j in range(k, len(T)):
        if T[j] >= T[k] + 21600:
            res, xp = "Time", C[j]; break
        if (L[j] <= st) if side > 0 else (H[j] >= st):
            res, xp = "Stop", st; break
        if j > k and ((H[j] >= tg) if side > 0 else (L[j] <= tg)):
            res, xp = "Target", tg; break
    else:
        return None
    net = side * (xp - e) * q - (e * q * TK + xp * q * (MK if res == "Target" else TK)) * (1 + GST)
    return dict(res=res, net=float(net), r=float(net / (d * q)), gr=float(side * (xp - e) / d), fill_t=int(T[k]))


sys.path.insert(0, "/home/claude/btc-command-desk/backend")
import os
os.environ.setdefault("MONGO_URL", "mongodb://x"); os.environ.setdefault("DB_NAME", "x")
import regime as RG

rows = []
for i in range(len(ct)):
    f = feats(i)
    if f is None:
        continue
    s = int(SD[i]); dl = DAYLAB.get(day[i]["type"], "Unclear")
    r = dict(t=int(ct[i]), side="LONG" if s > 0 else "SHORT", day=dl, **f)
    r["follow"] = sim(i, s, 2.0, f["d"]); r["fade"] = sim(i, -s, 2.0, f["d"])
    if r["follow"] is None:
        continue
    pb = {"RANGE": "fade", "TREND_UP": "follow" if s > 0 else None, "TREND_DOWN": "follow" if s < 0 else None}.get(day[i]["type"])
    r["switch"] = r[pb] if pb else None
    mk, rule, tside, _ = RG.decide(r["side"], dl, {**f, "rsi": f["rsi"]})
    r["regime_rule"], r["market"] = rule, mk
    if tside:
        ts = 1 if tside == "LONG" else -1
        r["regime"] = sim(i, ts, 1.5, f["d"]); r["regime_rev"] = sim(i, -ts, 1.5, f["d"])
    rows.append(r)

# Regime: max 2 trades a day (IST date of the signal)
cnt = {}
for r in rows:
    if r.get("regime"):
        k = (r["t"] + 19800) // 86400
        if cnt.get(k, 0) >= 2:
            r["regime"] = r["regime_rev"] = None
        else:
            cnt[k] = cnt.get(k, 0) + 1


def st(xs):
    xs = [x for x in xs if x]
    if not xs:
        return {"n": 0}
    net = np.array([x["net"] for x in xs]); R = np.array([x["r"] for x in xs]); w = int(sum(x["res"] == "Target" for x in xs))
    gw, gl = net[net > 0].sum(), -net[net < 0].sum()
    eq = np.cumsum(net); dd = float((eq - np.maximum.accumulate(np.r_[0.0, eq])[1:]).min())
    return {"n": len(xs), "wins": w, "win": round(w / len(xs), 4), "net": round(float(net.sum()), 2), "avg_r": round(float(R.mean()), 3), "avg_r_gross": round(float(np.mean([x["gr"] for x in xs])), 3),
            "pf": round(float(gw / gl), 2) if gl > 0 else None, "dd": round(dd, 2)}


BOOKS = ("follow", "fade", "switch", "regime", "regime_rev")
mstate = lambda r: "Up (>+5%)" if r["chg30"] > 0.05 else "Down (<-5%)" if r["chg30"] < -0.05 else "Sideways"
out = {"period": [int(rows[0]["t"]), int(rows[-1]["t"])], "signals": len(rows),
       "books": {b: st([r.get(b) for r in rows]) for b in BOOKS},
       "books_by_market": {m: {b: st([r.get(b) for r in rows if mstate(r) == m]) for b in BOOKS} for m in ("Up (>+5%)", "Sideways", "Down (<-5%)")},
       "books_by_half": {h: {b: st([r.get(b) for r in rows if (r["t"] < rows[len(rows) // 2]["t"]) == (h == "first")]) for b in BOOKS} for h in ("first", "second")},
       "regime_by_rule": {k: st([r.get("regime") for r in rows if r["regime_rule"] == k]) for k in sorted({r["regime_rule"] for r in rows if r.get("regime")})}}


def B(name, fn):
    """Factor buckets: fn(row) -> bucket label or None."""
    g = {}
    for r in rows:
        k = fn(r)
        if k is not None:
            g.setdefault(k, []).append(r)
    return {k: {"follow": st([r["follow"] for r in v]), "fade": st([r["fade"] for r in v])} for k, v in g.items()}


want = lambda r: "Up" if r["side"] == "LONG" else "Down"
FACT = {
    "day_type": lambda r: r["day"],
    "side": lambda r: r["side"],
    "rsi": lambda r: "under 30" if r["rsi"] < 30 else "30-40" if r["rsi"] < 40 else "40-60" if r["rsi"] < 60 else "60-70" if r["rsi"] < 70 else "70+",
    "trend_agree": lambda r: f"{sum(r[k] == want(r) for k in ('t15', 't1h', 't4h'))} of 3",
    "t4h_vs_signal": lambda r: None if r["t4h"] is None else "with signal" if r["t4h"] == want(r) else "mixed" if r["t4h"] == "Mixed" else "against signal",
    "stop_atr": lambda r: None if r["stop_atr"] is None else "under 0.6x" if r["stop_atr"] < 0.6 else "0.6-0.8x" if r["stop_atr"] < 0.8 else "0.8-1.2x" if r["stop_atr"] < 1.2 else "1.2x+",
    "pos24": lambda r: None if r["pos"] is None else "below 10%" if r["pos"] < 0.1 else "10-50%" if r["pos"] < 0.5 else "50-90%" if r["pos"] < 0.9 else "90%+",
    "volume": lambda r: "under 1x" if r["vr"] < 1 else "1-1.5x" if r["vr"] < 1.5 else "1.5-2x" if r["vr"] < 2 else "2x+",
    "chg4h_vs_signal": lambda r: (lambda c: "moved 1%+ with signal" if c > 0.01 else "moved 1%+ against" if c < -0.01 else "flat (within 1%)")(r["chg4h"] * (1 if r["side"] == "LONG" else -1)),
    "session": lambda r: "00-06 IST" if r["hour"] < 6 else "06-12 IST" if r["hour"] < 12 else "12-18 IST" if r["hour"] < 18 else "18-24 IST",
    "market_30d": mstate,
}
out["factors"] = {k: B(k, f) for k, f in FACT.items()}
out["overall"] = {"follow": st([r["follow"] for r in rows]), "fade": st([r["fade"] for r in rows])}
json.dump(out, open("/home/claude/btc-command-desk/backend/observer_history.json", "w"), indent=1)
print(json.dumps(out["books"], indent=1)); print(json.dumps(out["books_by_market"])[:1500])
for k, v in out["factors"].items():
    print(k)
    for b, s in sorted(v.items()):
        print(f"   {b:<22} follow n={s['follow']['n']:>4} win={s['follow'].get('win',0):.2f} R={s['follow'].get('avg_r',0):+.3f} | fade win={s['fade'].get('win',0):.2f} R={s['fade'].get('avg_r',0):+.3f}")
