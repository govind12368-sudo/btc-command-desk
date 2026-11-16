"""Round 3: futures-positioning setups (funding and open interest) on Delta data, Sep 2024 - Nov 2026.
Splits: train -> 2025-09-30, validation 2025-10-01 -> 2026-03-31, test 2026-04-01 ->."""
import numpy as np, pandas as pd, pickle
from edge import *
SP = (pd.Timestamp("2025-10-01").timestamp(), pd.Timestamp("2026-04-01").timestamp())
X = prep("delta_5m"); b5 = X["b5"]; t5 = b5["t"]
f = np.load("data/delta_funding_1h.npz"); oi = np.load("data/delta_oi_1h.npz")
ft_, fv = f["t"].astype(np.int64), f["a"][:, 3].astype(float); ot, ov = oi["t"].astype(np.int64), oi["a"][:, 3].astype(float)
b1h = X["b1h"]; th = b1h["t"]
fund = pd.Series(fv, index=ft_).reindex(th).ffill(limit=3).to_numpy(); oiv = pd.Series(ov, index=ot).reindex(th).ffill(limit=3).to_numpy()
s = pd.Series(fund); hi = s.rolling(720).quantile(0.9).shift(1).to_numpy(); lo = s.rolling(720).quantile(0.1).shift(1).to_numpy()
top, bot = fund >= hi, fund <= lo
top, bot = top & ~np.roll(top, 1), bot & ~np.roll(bot, 1)
ch = b1h["c"]; atr = atr_wilder(b1h)
oi4 = oiv / np.roll(oiv, 4) - 1; p4 = (ch - np.roll(ch, 4)) / np.roll(atr, 4)
def edge_first(c): return c & ~np.roll(c, 1)
fam = {}
def add(name, L, S):
    d = np.where(L, 1, np.where(S, -1, 0)); k = np.where(d != 0)[0]; k = k[k > 800]
    i5 = np.searchsorted(t5, th[k] + 3300); ok = i5 < len(t5); fam[name] = (i5[ok], d[k][ok])
add("H1 fade extreme funding (top/bottom 10% of 30 days)", bot, top)
add("H2 follow extreme funding", top, bot)
add("H3 buy after an OI flush with falling price (and mirror)", edge_first((oi4 <= -0.03) & (p4 <= -1.5)), edge_first((oi4 <= -0.03) & (p4 >= 1.5)))
add("H4 follow OI build-up with price", edge_first((oi4 >= 0.03) & (p4 >= 1.5)), edge_first((oi4 >= 0.03) & (p4 <= -1.5)))
rows = []
show = lambda r, k: f"{k}: n={r[k]['n']} win {r[k]['win']}% {r[k]['R']:+.3f}R t={r[k]['t']:+.2f}" if r[k] else f"{k}: -"
for name, (idx, d) in fam.items():
    P = Paths(b5, t5[idx] + 300, d, b5["c"][idx], max_hold_s=72 * 3600, mode="market")
    atrf = X["atr1d"][idx] / P.E; ft = P.fill_t.astype(float)
    for sm in (0.5, 1.0):
        for rr in (1.0, 1.5, 99):
            for hold in (24 * 3600, 72 * 3600):
                stopf = sm * atrf
                net, res, xo = P.score(stopf, rr * stopf, hold, fee_in=TAKER, fee_tgt=MAKER, fee_out=TAKER)
                seq = one_at_a_time(P.fill_t, xo, 300); net = np.where(seq, net, np.nan)
                row = dict(family=name, stop=sm, rr=rr, hold_h=hold // 3600)
                for nm, mk in (("train", ft < SP[0]), ("val", (ft >= SP[0]) & (ft < SP[1])), ("test", ft >= SP[1])):
                    row[nm] = summarize(np.where(mk, net, np.nan), res, stopf, ft)
                rows.append(row)
pickle.dump(rows, open("edge4.pkl", "wb"))
ok_tr = [r for r in rows if r["train"] and r["train"]["n"] >= 40 and r["train"]["win"] >= 50 and r["train"]["R"] > 0 and r["train"]["t"] >= 2]
ok_val = [r for r in ok_tr if r["val"] and r["val"]["win"] >= 50 and r["val"]["R"] > 0]
print(len(rows), "variants | pass train:", len(ok_tr), "| also validation:", len(ok_val))
for name in fam:
    rs = [r for r in rows if r["family"] == name and r["train"]]
    if not rs: print("  ", name, "no trades"); continue
    r = max(rs, key=lambda r: r["train"]["t"]); print(f"  {name:<58} stop {r['stop']} rr {r['rr']} hold {r['hold_h']}h | {show(r,'train')} | {show(r,'val')}")
for r in ok_tr: print("PASS", r["family"], r["stop"], r["rr"], r["hold_h"], show(r, "train"), show(r, "val"))
