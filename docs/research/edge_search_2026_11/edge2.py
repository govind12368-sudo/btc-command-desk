"""Gross (no-fee) view of the same grid, to see whether any family has an edge before costs."""
import numpy as np, pickle
from edge import *
X = prep(); b5 = X["b5"]; t5 = b5["t"]
print(f"median 1h ATR as % of price: train {100*np.nanmedian((X['atr1h']/b5['c'])[t5<SPLIT[0]]):.2f}%  val {100*np.nanmedian((X['atr1h']/b5['c'])[(t5>=SPLIT[0])&(t5<SPLIT[1])]):.2f}%  test {100*np.nanmedian((X['atr1h']/b5['c'])[t5>=SPLIT[1]]):.2f}%")
print(f"median 1d ATR as % of price: {100*np.nanmedian(X['atr1d']/b5['c']):.2f}%")
for fam, (idx, d) in families(X).items():
    idx, d = thin(np.asarray(idx), np.asarray(d))
    good = (idx + 1 < len(t5)) & ~np.isnan(X["atr1h"][np.clip(idx, 0, len(t5) - 1)]) & (idx > 5000); idx, d = idx[good], d[good]
    P = Paths(b5, t5[idx] + 300, d, b5["c"][idx], max_hold_s=24 * 3600, mode="market")
    atrf = X["atr1h"][idx] / P.E; ft = P.fill_t.astype(float)
    best = None
    for s in STOPS:
        for rr in RRS:
            for hold in HOLDS:
                stopf = s * atrf
                net, res, xo = P.score(stopf, rr * stopf, hold, fee_in=0, fee_tgt=0, fee_out=0)
                seq = one_at_a_time(P.fill_t, xo, 300); net = np.where(seq, net, np.nan)
                tr = summarize(np.where(ft < SPLIT[0], net, np.nan), res, stopf, ft); va = summarize(np.where((ft >= SPLIT[0]) & (ft < SPLIT[1]), net, np.nan), res, stopf, ft)
                if tr and (best is None or tr["t"] > best[0]["t"]): best = (tr, va, s, rr, hold // 3600)
    tr, va, s, rr, hh = best
    print(f"{fam:<44} GROSS best: stop {s} rr {rr} hold {hh}h | train n={tr['n']} win {tr['win']}% {tr['R']:+.3f}R t={tr['t']:+.2f} | val n={va['n']} win {va['win']}% {va['R']:+.3f}R t={va['t']:+.2f}")
