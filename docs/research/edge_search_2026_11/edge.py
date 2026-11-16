"""Edge hunt: pre-declared setup families x a fixed exit grid, selected on TRAIN only, confirmed on VALIDATION,
and only the survivors are looked at on TEST. Fees included. Signals use completed bars only; entry is the next 5m open.

Binance BTCUSDT 5m, 2023-10 to 2026-11. Splits by fill time:
  train       -> 2025-03-31      validation 2025-04-01 -> 2025-12-31      test 2026-01-01 ->
"""
import sys, json, pickle
import numpy as np, pandas as pd
from lib import *

SPLIT = (pd.Timestamp("2025-04-01").timestamp(), pd.Timestamp("2026-01-01").timestamp())
STOPS, RRS, HOLDS = (0.75, 1.0, 1.5), (0.75, 1.0, 1.5, 2.0), (6 * 3600, 24 * 3600)


def rsi(c, n=14):
    d = pd.Series(c).diff(); up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(); dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).to_numpy()


def adx(b, n=14):
    h, l, c = pd.Series(b["h"]), pd.Series(b["l"]), pd.Series(b["c"])
    up, dn = h.diff(), -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0); mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.Series(true_range(b)).ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(pdm).ewm(alpha=1 / n, adjust=False).mean() / tr; mdi = 100 * pd.Series(mdm).ewm(alpha=1 / n, adjust=False).mean() / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean().to_numpy()


def prep(name="binance_5m"):
    b5 = load(name); t5 = b5["t"]
    X = dict(b5=b5)
    for nm, sec in (("b15", 900), ("b1h", 3600), ("b4h", 14400), ("b1d", 86400)):
        X[nm] = agg(b5, sec)
    m = lambda bH, sec, arr: map_htf(t5, 300, bH["t"], sec, np.asarray(arr, dtype=float))[0]
    b1h, b4h, b1d, b15 = X["b1h"], X["b4h"], X["b1d"], X["b15"]
    X["atr1h"] = m(b1h, 3600, atr_wilder(b1h)); X["atr1d"] = m(b1d, 86400, atr_wilder(b1d))
    X["tr1h"] = m(b1h, 3600, trend_state(b1h)[0]); X["tr4h"] = m(b4h, 14400, trend_state(b4h)[0])
    X["adx1h"] = m(b1h, 3600, adx(b1h))
    X["pdh"] = m(b1d, 86400, b1d["h"]); X["pdl"] = m(b1d, 86400, b1d["l"])
    return X


def at15(X, cond15_long, cond15_short):
    """Signals decided on completed 15m bars -> 5m indices (the last 5m bar of that 15m bar) and directions."""
    b15, t5 = X["b15"], X["b5"]["t"]
    d = np.where(cond15_long, 1, np.where(cond15_short, -1, 0))
    k = np.where(d != 0)[0]
    i5 = np.searchsorted(t5, b15["t"][k] + 600)             # 5m bar that closes with the 15m bar
    ok = (i5 < len(t5)) & (t5[np.clip(i5, 0, len(t5) - 1)] == b15["t"][k] + 600)
    return i5[ok], d[k][ok]


def htf15(X, key):
    """A 5m-mapped series sampled at each 15m bar's close."""
    b15, t5 = X["b15"], X["b5"]["t"]
    i5 = np.clip(np.searchsorted(t5, b15["t"] + 600), 0, len(t5) - 1)
    return X[key][i5]


def families(X):
    b5, b15, b1h = X["b5"], X["b15"], X["b1h"]
    out = {}
    c, h, l, o = b15["c"], b15["h"], b15["l"], b15["o"]
    tr1h, tr4h, adx1h = htf15(X, "tr1h"), htf15(X, "tr4h"), htf15(X, "adx1h")
    # F1 pullback inside the 1h trend, rejection candle at the 15m EMA
    for n in (20, 50):
        e = ema(c, n)
        out[f"F1 pullback to 15m EMA{n} in 1h trend"] = at15(X, (tr1h == 1) & (tr4h != -1) & (l <= e) & (c > e) & (c > o), (tr1h == -1) & (tr4h != 1) & (h >= e) & (c < e) & (c < o))
    # F2 range fade: close outside the 15m Bollinger band while the 1h ADX says no trend
    s = pd.Series(c); mid, sd = s.rolling(20).mean().to_numpy(), s.rolling(20).std(ddof=0).to_numpy()
    for k in (2.0, 2.5):
        out[f"F2 fade 15m Bollinger {k} when 1h ADX<20"] = at15(X, (c < mid - k * sd) & (adx1h < 20), (c > mid + k * sd) & (adx1h < 20))
    # F6 RSI dip in the direction of the 4h trend
    r = rsi(c)
    for lo in (30, 25):
        out[f"F6 15m RSI<{lo} dip with 4h trend"] = at15(X, (r < lo) & (tr4h == 1), (r > 100 - lo) & (tr4h == -1))
    # F8 sweep of the previous day's high/low that closes back inside (failed break)
    pdh, pdl = htf15(X, "pdh"), htf15(X, "pdl")
    out["F8 failed break of previous day high/low"] = at15(X, (l < pdl) & (c > pdl), (h > pdh) & (c < pdh))
    lo12 = pd.Series(l).rolling(48).min().shift(4).to_numpy(); hi12 = pd.Series(h).rolling(48).max().shift(4).to_numpy()
    out["F8 failed break of 12h high/low"] = at15(X, (l < lo12) & (c > lo12) & (c > o), (h > hi12) & (c < hi12) & (c < o))
    # F4 first 1h close beyond the N-hour high/low
    ch, hh, lh = b1h["c"], b1h["h"], b1h["l"]
    for n in (24, 48):
        up = pd.Series(hh).rolling(n).max().shift(1).to_numpy(); dn = pd.Series(lh).rolling(n).min().shift(1).to_numpy()
        L, S = ch > up, ch < dn
        L, S = L & ~np.roll(L, 1), S & ~np.roll(S, 1)
        d = np.where(L, 1, np.where(S, -1, 0)); k = np.where(d != 0)[0]
        i5 = np.searchsorted(b5["t"], b1h["t"][k] + 3300); ok = i5 < len(b5["t"])
        out[f"F4 1h breakout of {n}h range"] = (i5[ok], d[k][ok])
    # F3 opening-range breakout: first 5m close beyond the first hour's range, within the next 3 hours
    t5 = b5["t"]; sod = t5 % 86400
    for nm, st in (("00:00 UTC", 0), ("07:00 UTC London", 7 * 3600), ("13:30 UTC New York", 13 * 3600 + 1800)):
        day = (t5 - st) // 86400; rel = (t5 - st) % 86400
        df = pd.DataFrame({"day": day, "rel": rel, "h": b5["h"], "l": b5["l"], "c": b5["c"], "i": np.arange(len(t5))})
        first = df[df.rel < 3600].groupby("day").agg(rh=("h", "max"), rl=("l", "min"), n=("h", "size"))
        first = first[first.n == 12]
        win = df[(df.rel >= 3600) & (df.rel < 4 * 3600)].join(first, on="day", how="inner")
        win["d"] = np.where(win.c > win.rh, 1, np.where(win.c < win.rl, -1, 0))
        sig = win[win.d != 0].groupby("day").first()
        out[f"F3 opening range breakout {nm}"] = (sig.i.to_numpy(), sig.d.to_numpy())
    # F5 the site's own scalp signal, followed and reversed
    d, ind = scalp_setups(b5); idx = scalp_signals(b5, d, ind["ext"])
    out["F5 site scalp signal, follow"] = (idx, d[idx])
    out["F5 site scalp signal, reverse"] = (idx, -d[idx])
    return out


def thin(idx, d, gap=12):
    """Drop a signal that repeats the previous one's direction within `gap` 5m bars."""
    keep, last = [], {}
    for n, (i, s) in enumerate(zip(idx, d)):
        if i - last.get(s, -10 ** 9) >= gap:
            keep.append(n)
        last[s] = i
    return idx[keep], d[keep]


def one_at_a_time(fill_t, xo, sec):
    """Mask of trades that start after the previous accepted trade has ended."""
    ok = np.zeros(len(fill_t), dtype=bool); free = -1
    for i in np.argsort(fill_t, kind="stable"):
        if fill_t[i] < 0: continue
        if fill_t[i] >= free:
            ok[i] = True; free = fill_t[i] + (xo[i] + 1) * sec
    return ok


def summarize(net, res, stopf, ft):
    ok = ~np.isnan(net)
    if ok.sum() < 5: return None
    x, s = net[ok], stopf[ok]
    days = max((ft[ok].max() - ft[ok].min()) / 86400, 1)
    r = x / s
    return dict(n=int(ok.sum()), per_day=round(ok.sum() / days, 2), win=round(100 * float((x > 0).mean()), 1), bps=round(1e4 * float(x.mean()), 2), R=round(float(r.mean()), 3),
                t=round(float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))), 2) if r.std() > 0 else 0.0, totR=round(float(r.sum()), 1),
                T=int((res[ok] == 1).sum()), S=int((res[ok] == -1).sum()), X=int((res[ok] == 0).sum()))


def run(name="binance_5m", splits=SPLIT, fee_in=TAKER):
    X = prep(name); b5 = X["b5"]; t5 = b5["t"]
    rows = []
    for fam, (idx, d) in families(X).items():
        idx, d = thin(np.asarray(idx), np.asarray(d))
        good = (idx + 1 < len(t5)) & ~np.isnan(X["atr1h"][np.clip(idx, 0, len(t5) - 1)]) & (idx > 5000)
        idx, d = idx[good], d[good]
        P = Paths(b5, t5[idx] + 300, d, b5["c"][idx], max_hold_s=24 * 3600, mode="market")
        atrf = X["atr1h"][idx] / P.E
        for s in STOPS:
            for rr in RRS:
                for hold in HOLDS:
                    stopf = s * atrf
                    net, res, xo = P.score(stopf, rr * stopf, hold, fee_in=fee_in, fee_tgt=MAKER, fee_out=TAKER)
                    seq = one_at_a_time(P.fill_t, xo, 300)
                    net = np.where(seq, net, np.nan)
                    ft = P.fill_t.astype(float)
                    row = dict(family=fam, stop=s, rr=rr, hold_h=hold // 3600)
                    for nm, m in (("train", ft < splits[0]), ("val", (ft >= splits[0]) & (ft < splits[1])), ("test", ft >= splits[1])):
                        row[nm] = summarize(np.where(m, net, np.nan), res, stopf, ft)
                    rows.append(row)
    return rows


if __name__ == "__main__":
    rows = run()
    pickle.dump(rows, open("edge_binance.pkl", "wb"))
    print(len(rows), "variants")
    # selection uses TRAIN only
    ok_tr = [r for r in rows if r["train"] and r["train"]["n"] >= 200 and r["train"]["win"] >= 50 and r["train"]["R"] > 0 and r["train"]["t"] >= 2]
    print("pass train (n>=200, win>=50, net>0, t>=2):", len(ok_tr))
    ok_val = [r for r in ok_tr if r["val"] and r["val"]["win"] >= 50 and r["val"]["R"] > 0]
    print("...and hold on validation (win>=50, net>0):", len(ok_val))
    show = lambda r, k: f"{k}: n={r[k]['n']} {r[k]['per_day']}/d win {r[k]['win']}% {r[k]['R']:+.3f}R t={r[k]['t']:+.2f}" if r[k] else f"{k}: -"
    print("\nBest 12 on TRAIN by t-stat among win>=50 (test NOT shown):")
    for r in sorted([r for r in rows if r["train"] and r["train"]["win"] >= 50 and r["train"]["n"] >= 200], key=lambda r: -r["train"]["t"])[:12]:
        print(f"  {r['family']:<44} stop {r['stop']} rr {r['rr']} hold {r['hold_h']}h | {show(r,'train')} | {show(r,'val')}")
    print("\nPer family: best TRAIN net (any win rate), with validation:")
    for fam in dict.fromkeys(r["family"] for r in rows):
        rs = [r for r in rows if r["family"] == fam and r["train"] and r["train"]["n"] >= 100]
        if not rs: print(f"  {fam}: too few trades"); continue
        r = max(rs, key=lambda r: r["train"]["t"])
        print(f"  {fam:<44} stop {r['stop']} rr {r['rr']} hold {r['hold_h']}h | {show(r,'train')} | {show(r,'val')}")
