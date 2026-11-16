"""Check of the funding idea by side, because Delta funding sits at its default 0.01% most of the time."""
import numpy as np, pandas as pd
from edge import *
SP = (pd.Timestamp("2025-10-01").timestamp(), pd.Timestamp("2026-04-01").timestamp())
X = prep("delta_5m"); b5 = X["b5"]; t5 = b5["t"]; b1h = X["b1h"]; th = b1h["t"]
f = np.load("data/delta_funding_1h.npz"); fund = pd.Series(f["a"][:, 3].astype(float), index=f["t"].astype(np.int64)).reindex(th).ffill(limit=3).to_numpy()
s = pd.Series(fund); hi = s.rolling(720).quantile(0.9).shift(1).to_numpy(); lo = s.rolling(720).quantile(0.1).shift(1).to_numpy()
print("share of hours where funding == 0.01:", round(float(np.nanmean(np.isclose(fund, 0.01))), 3), "| share of hours where the 90% line == 0.01:", round(float(np.nanmean(np.isclose(hi, 0.01))), 3))
def first(c): return c & ~np.roll(c, 1)
def run(name, L, S):
    d = np.where(L, 1, np.where(S, -1, 0)); k = np.where(d != 0)[0]; k = k[k > 800]
    i5 = np.searchsorted(t5, th[k] + 3300); ok = i5 < len(t5); idx, dd = i5[ok], d[k][ok]
    if len(idx) < 5: print(f"{name:<52} too few signals ({len(idx)})"); return
    P = Paths(b5, t5[idx] + 300, dd, b5["c"][idx], max_hold_s=72 * 3600, mode="market"); stopf = 0.5 * X["atr1d"][idx] / P.E; ft = P.fill_t.astype(float)
    net, res, xo = P.score(stopf, 99 * stopf, 72 * 3600, fee_in=TAKER, fee_tgt=MAKER, fee_out=TAKER); seq = one_at_a_time(P.fill_t, xo, 300); net = np.where(seq, net, np.nan)
    out = []
    for nm, mk in (("train", ft < SP[0]), ("val", (ft >= SP[0]) & (ft < SP[1])), ("test", ft >= SP[1]), ("ALL", ft > 0)):
        r = summarize(np.where(mk, net, np.nan), res, stopf, ft); out.append(f"{nm}: n={r['n']} win {r['win']}% {r['R']:+.3f}R t={r['t']:+.2f}" if r else f"{nm}: -")
    print(f"{name:<52} " + " | ".join(out))
top, bot = fund >= hi, fund <= lo
run("ORIGINAL both sides", first(bot), first(top))
run("ORIGINAL long side only (funding in bottom 10%)", first(bot), np.zeros_like(bot))
run("ORIGINAL short side only (funding >= 90% line)", np.zeros_like(top), first(top))
stop_, sbot = fund > hi, fund < lo
run("STRICT both sides (above / below the lines)", first(sbot), first(stop_))
run("STRICT long side only", first(sbot), np.zeros_like(sbot))
run("STRICT short side only (funding above default)", np.zeros_like(stop_), first(stop_))
neg = fund < 0
run("Funding turns negative -> long", first(neg), np.zeros_like(neg))
