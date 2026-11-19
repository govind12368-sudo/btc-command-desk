"""What-if lab for the Strategy Observer: the same 26-month signals, with other stop sizes, entry fee and hold times.

Grid: stop 0.5 / 0.75 / 1.0 / 1.5 % of entry  x  entry fee taker / maker (resting limit)  x  hold 6 h / 12 h.
Books: follow (signal side, 2R), fade (Plan A, 2R), switch (day-type playbook, 2R), regime (Regime rules, 1.5R, max 2/day;
its 'stop >= 0.6x hourly ATR' checks use the new stop). Exits: target maker, stop / time taker, +18% GST.
Touch rules as the site: stop wins a same-candle tie; in the fill candle only the stop counts. 5m candles.
'Noise stop' = stopped, then price came back to the entry within the hold window (measured from the fill).
The baseline cell (0.5%, taker, 6 h) equals the live rules; its per-trade R spread feeds the drift check.
"""
import json, math, sys, os
import numpy as np
sys.path.insert(0, "/home/claude/research")
import observer_history as OH                     # features per signal (same as the observer's 26-month study)

os.environ.setdefault("MONGO_URL", "mongodb://x"); os.environ.setdefault("DB_NAME", "x")
sys.path.insert(0, "/home/claude/btc-command-desk/backend")
import regime as RG

T, H, L, C = OH.T, OH.H, OH.L, OH.C
E, SD, ct, day = OH.E, OH.SD, OH.ct, OH.day
TK, MK, GST = 0.0005, 0.0002, 0.18
STOPS, HOLDS = (0.005, 0.0075, 0.01, 0.015), (6, 12)

sig = []
for i in range(len(ct)):
    f = OH.feats(i)
    if f is None:
        continue
    e = float(E[i]); p0 = int(np.searchsorted(T, ct[i])); k = None
    for j in range(p0, min(p0 + 2, len(T))):
        if (SD[i] > 0 and L[j] <= e) or (SD[i] < 0 and H[j] >= e):
            k = j; break
    if k is None:
        continue
    sig.append((i, f, k))
print("filled signals", len(sig))


def path(k, e, side, d, rr, hold_h):
    st, tg = e - side * d, e + side * rr * d
    end_t = T[k] + hold_h * 3600
    res = xp = None; xj = None
    for j in range(k, len(T)):
        if T[j] >= end_t:
            res, xp, xj = "Time", C[j], j; break
        if (L[j] <= st) if side > 0 else (H[j] >= st):
            res, xp, xj = "Stop", st, j; break
        if j > k and ((H[j] >= tg) if side > 0 else (L[j] <= tg)):
            res, xp, xj = "Target", tg, j; break
    if res is None:
        return None
    noise = False
    if res == "Stop":
        for j in range(xj + 1, len(T)):
            if T[j] >= end_t:
                break
            if (H[j] >= e) if side > 0 else (L[j] <= e):
                noise = True; break
    return res, float(xp), noise


def cost(e, xp, res, q, entry_fee):
    return (e * q * entry_fee + xp * q * (MK if res == "Target" else TK)) * (1 + GST)


cells = {}
DAYLAB = {"RANGE": "Range", "TREND_UP": "Trend up", "TREND_DOWN": "Trend down", "MIXED": "Unclear", "NA": "Unclear"}
for sp in STOPS:
    for hold in HOLDS:
        tr = {"follow": [], "fade": [], "switch": [], "regime": []}
        per_day = {}
        for i, f, k in sig:
            e = float(E[i]); s = int(SD[i]); d = round(e * sp * 2) / 2; q = math.floor(10000 / e * 1000) / 1000
            legs = {}
            for name, side in (("follow", s), ("fade", -s)):
                p = path(k, e, side, d, 2.0, hold)
                if p:
                    legs[name] = (p, side)
            dt = day[i]["type"]
            pb = {"RANGE": "fade", "TREND_UP": "follow" if s > 0 else None, "TREND_DOWN": "follow" if s < 0 else None}.get(dt)
            items = [(n, legs[n]) for n in ("follow", "fade") if n in legs]
            if pb and pb in legs:
                items.append(("switch", legs[pb]))
            ff = {**f, "stop_atr": (d / (f["d"] / f["stop_atr"])) if f.get("stop_atr") else None}
            _, rule, tside, _ = RG.decide("LONG" if s > 0 else "SHORT", DAYLAB.get(dt, "Unclear"), ff)
            if tside:
                dk = (int(ct[i]) + 19800) // 86400
                if per_day.get(dk, 0) < 2:
                    per_day[dk] = per_day.get(dk, 0) + 1
                    side = 1 if tside == "LONG" else -1
                    p = path(k, e, side, d, 1.5, hold)
                    if p:
                        items.append(("regime", (p, side)))
            for name, ((res, xp, noise), side) in items:
                gross = side * (xp - e) * q
                tr[name].append({"t": int(ct[i]), "res": res, "gross_r": gross / (d * q), "fee_t": cost(e, xp, res, q, TK) / (d * q),
                                 "fee_m": cost(e, xp, res, q, MK) / (d * q), "noise": noise})
        for name, xs in tr.items():
            mid = sig[len(sig) // 2][0]
            for fee in ("taker", "maker"):
                key = f"{name}|{sp}|{fee}|{hold}"
                if not xs:
                    cells[key] = {"n": 0}; continue
                g = np.array([x["gross_r"] for x in xs]); fr = np.array([x["fee_t" if fee == "taker" else "fee_m"] for x in xs]); net = g - fr
                stops = [x for x in xs if x["res"] == "Stop"]
                half = np.array([x["t"] < int(ct[mid]) for x in xs])
                cells[key] = {"book": name, "stop_pct": sp, "entry_fee": fee, "hold_h": hold, "n": len(xs),
                              "win": round(float(np.mean([x["res"] == "Target" for x in xs])), 4),
                              "avg_r_gross": round(float(g.mean()), 4), "fee_r": round(float(fr.mean()), 4), "avg_r": round(float(net.mean()), 4),
                              "std_r": round(float(net.std(ddof=1)), 4), "ci_lo": round(float(net.mean() - 1.96 * net.std(ddof=1) / math.sqrt(len(xs))), 4),
                              "ci_hi": round(float(net.mean() + 1.96 * net.std(ddof=1) / math.sqrt(len(xs))), 4),
                              "time_exits": round(float(np.mean([x["res"] == "Time" for x in xs])), 4),
                              "noise_stops": round(len([x for x in stops if x["noise"]]) / len(stops), 4) if stops else None,
                              "first_half_r": round(float(net[half].mean()), 4) if half.any() else None,
                              "second_half_r": round(float(net[~half].mean()), 4) if (~half).any() else None,
                              "usd_per_trade": round(float(net.mean() * 0.0 + np.mean([x["gross_r"] - x["fee_t" if fee == "taker" else "fee_m"] for x in xs]) * 10000 * sp), 2)}
        print(sp, hold, {n: (cells[f'{n}|{sp}|taker|{hold}']['n'], cells[f'{n}|{sp}|taker|{hold}']['avg_r'], cells[f'{n}|{sp}|maker|{hold}']['avg_r'],
                             cells[f'{n}|{sp}|taker|{hold}']['avg_r_gross']) for n in tr})

out = {"period": [int(ct[sig[0][0]]), int(ct[sig[-1][0]])], "signals": len(sig), "stops": STOPS, "holds": HOLDS,
       "cells": list(cells.values()), "baseline": {b: cells[f"{b}|0.005|taker|6"] for b in ("follow", "fade", "switch", "regime")}}
json.dump(out, open("/home/claude/btc-command-desk/backend/observer_whatif.json", "w"), indent=1)
