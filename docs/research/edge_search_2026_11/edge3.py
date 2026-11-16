"""Round 2: slower, daily-scale setups where fees are small next to the stop. Same discipline: select on TRAIN, confirm on VALIDATION."""
import numpy as np, pandas as pd, pickle
from edge import *
X = prep(); b5 = X["b5"]; t5 = b5["t"]; b1d, b4h = X["b1d"], X["b4h"]
m = lambda bH, sec, arr: map_htf(t5, 300, bH["t"], sec, np.asarray(arr, dtype=float))[0]
cd = b1d["c"]; fam = {}
day_open = np.where(t5 % 86400 == 0)[0]; day_open = day_open[day_open > 30 * 288]
last5 = day_open - 1                                      # the 5m bar that closes at 00:00 UTC; entry is the next open
for n in (1, 3, 7, 20):
    mom = m(b1d, 86400, np.sign(cd - np.roll(cd, n)) * (np.arange(len(cd)) >= n))
    d = mom[last5]; fam[f"G1 daily momentum, {n}-day look-back"] = (last5[d != 0], d[d != 0].astype(int))
e20, e50 = ema(cd, 20), ema(cd, 50)
tr = m(b1d, 86400, np.where((cd > e20) & (e20 > e50), 1, np.where((cd < e20) & (e20 < e50), -1, 0)))
d = tr[last5]; fam["G2 daily trend (close, EMA20, EMA50 in order)"] = (last5[d != 0], d[d != 0].astype(int))
atrd = atr_wilder(b1d); big = np.abs(cd - b1d["o"]) > atrd
rev = m(b1d, 86400, np.where(big, -np.sign(cd - b1d["o"]), 0)); d = rev[last5]
fam["G3 fade a daily move larger than 1 ATR"] = (last5[d != 0], d[d != 0].astype(int))
t4 = trend_state(b4h)[0]; first = (t4 != 0) & (t4 != np.roll(t4, 1))
i5 = np.searchsorted(t5, b4h["t"][first] + 14400 - 300); ok = (i5 < len(t5)) & (i5 > 30 * 288)
d4 = t4[first][ok]; i5 = i5[ok]; dtr = X["tr4h"] if False else m(b1d, 86400, np.where((cd > e20), 1, -1))
agree = dtr[i5] == d4
fam["G4 new 4h trend in the direction of the daily EMA20"] = (i5[agree], d4[agree].astype(int))
rows = []
for name, (idx, d) in fam.items():
    P = Paths(b5, t5[idx] + 300, d, b5["c"][idx], max_hold_s=72 * 3600, mode="market")
    atrf = X["atr1d"][idx] / P.E; ft = P.fill_t.astype(float)
    for s in (0.5, 1.0):
        for rr in (1.0, 1.5, 99):
            for hold in (24 * 3600, 72 * 3600):
                stopf = s * atrf
                net, res, xo = P.score(stopf, rr * stopf, hold, fee_in=TAKER, fee_tgt=MAKER, fee_out=TAKER)
                seq = one_at_a_time(P.fill_t, xo, 300); net = np.where(seq, net, np.nan)
                row = dict(family=name, stop=s, rr=rr, hold_h=hold // 3600)
                for nm, mk in (("train", ft < SPLIT[0]), ("val", (ft >= SPLIT[0]) & (ft < SPLIT[1])), ("test", ft >= SPLIT[1])):
                    row[nm] = summarize(np.where(mk, net, np.nan), res, stopf, ft)
                rows.append(row)
pickle.dump(rows, open("edge3.pkl", "wb"))
show = lambda r, k: f"{k}: n={r[k]['n']} win {r[k]['win']}% {r[k]['R']:+.3f}R {r[k]['bps']:+.0f}bps t={r[k]['t']:+.2f}" if r[k] else f"{k}: -"
print(len(rows), "variants (after fees)")
ok_tr = [r for r in rows if r["train"] and r["train"]["n"] >= 80 and r["train"]["win"] >= 50 and r["train"]["R"] > 0 and r["train"]["t"] >= 2]
ok_val = [r for r in ok_tr if r["val"] and r["val"]["win"] >= 50 and r["val"]["R"] > 0]
print("pass train (n>=80, win>=50, net>0, t>=2):", len(ok_tr), "| also hold on validation:", len(ok_val))
for name in fam:
    r = max([r for r in rows if r["family"] == name and r["train"]], key=lambda r: r["train"]["t"])
    print(f"  {name:<52} stop {r['stop']} rr {r['rr']} hold {r['hold_h']}h | {show(r,'train')} | {show(r,'val')}")
print("passing rows:")
for r in ok_tr: print(f"  {r['family']:<52} stop {r['stop']} rr {r['rr']} hold {r['hold_h']}h | {show(r,'train')} | {show(r,'val')}")
