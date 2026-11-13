"""Study 2: rebuild the scalp signal on months of Delta 5m candles (same code as the live engine),
then test which information available AT SIGNAL TIME separates 'follow works' from 'reverse works'.
Train = first 60% of signals, test = last 40% (cuts are taken from train only)."""
import asyncio, bisect, math, os, sys, time
from collections import defaultdict
os.environ.setdefault("MONGO_URL", "mongodb://unused"); os.environ.setdefault("DB_NAME", "study")
import motor.motor_asyncio as mm
from mongomock_motor import AsyncMongoMockClient
mm.AsyncIOMotorClient = lambda *a, **k: AsyncMongoMockClient()
from indicators import frame_metrics, trend_dir
from strategy import scalp_direction, ext_atr
from sources import http_get, DELTA

DAYS = int(os.environ.get("STUDY_DAYS", "330"))
S = {"close_extreme_atr": 0.3, "min_scalp_volume_ratio": 0.9}
IST = 19800
FE, FM = 0.00059, 0.000236


async def fetch(res, sec, start, end):
    out, s = {}, start
    while s < end:
        e = min(end, s + 1900 * sec)
        for _ in range(4):
            try:
                r = (await http_get(f"{DELTA}/v2/history/candles", params={"symbol": "BTCUSD", "resolution": res, "start": s, "end": e})).json()
                break
            except Exception as ex:
                r = {}; await asyncio.sleep(2)
        for c in r.get("result") or []:
            out[int(c["time"])] = {"t": int(c["time"]), "o": float(c["open"]), "h": float(c["high"]), "l": float(c["low"]), "c": float(c["close"]), "v": float(c.get("volume") or 0)}
        s = e
    return [out[k] for k in sorted(out)]


def agg(b5, sec):
    out = []
    for b in b5:
        k = b["t"] - b["t"] % sec
        if out and out[-1]["t"] == k:
            o = out[-1]; o["h"] = max(o["h"], b["h"]); o["l"] = min(o["l"], b["l"]); o["c"] = b["c"]; o["v"] += b["v"]; o["n"] += 1
        else:
            out.append({"t": k, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"], "n": 1})
    return out


def adx(bars, n=14):
    out = [None] * len(bars); trs = pdm = ndm = 0.0; dxs = []; a = None
    for i in range(1, len(bars)):
        b, p = bars[i], bars[i - 1]
        tr = max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"]))
        up, dn = b["h"] - p["h"], p["l"] - b["l"]
        pd_, nd_ = (up if up > dn and up > 0 else 0), (dn if dn > up and dn > 0 else 0)
        if i <= n:
            trs += tr; pdm += pd_; ndm += nd_
            if i < n: continue
        else:
            trs = trs - trs / n + tr; pdm = pdm - pdm / n + pd_; ndm = ndm - ndm / n + nd_
        if trs <= 0: continue
        pdi, ndi = 100 * pdm / trs, 100 * ndm / trs
        dx = 100 * abs(pdi - ndi) / (pdi + ndi) if pdi + ndi else 0
        if a is None:
            dxs.append(dx)
            if len(dxs) == n: a = sum(dxs) / n
        else:
            a = (a * (n - 1) + dx) / n
        out[i] = a
    return out


async def main():
    now = int(time.time()); now -= now % 300
    b5 = await fetch("5m", 300, now - DAYS * 86400, now)
    b5 = [b for b in b5 if b["t"] + 300 <= now]
    print(f"5m candles {len(b5)}: {time.strftime('%Y-%m-%d', time.gmtime(b5[0]['t']))} to {time.strftime('%Y-%m-%d %H:%M', time.gmtime(b5[-1]['t']))} UTC; gaps {sum(1 for a, b in zip(b5, b5[1:]) if b['t'] - a['t'] != 300)}")
    n = len(b5); T = [b["t"] for b in b5]; C = [b["c"] for b in b5]; Hh = [b["h"] for b in b5]; Ll = [b["l"] for b in b5]
    b15 = [x for x in agg(b5, 900) if x["n"] == 3]; T15 = [x["t"] for x in b15]
    b60 = [x for x in agg(b5, 3600) if x["n"] == 12]; T60 = [x["t"] for x in b60]
    adx60, adx15 = adx(b60), adx(b15)
    pv = [0.0]; vv = [0.0]
    for b in b5:
        pv.append(pv[-1] + (b["h"] + b["l"] + b["c"]) / 3 * b["v"]); vv.append(vv[-1] + b["v"])
    days = {}
    for i, b in enumerate(b5):
        d = (b["t"] + IST) // 86400
        x = days.setdefault(d, {"i0": i, "o": b["o"], "h": b["h"], "l": b["l"], "n": 0})
        x["h"] = max(x["h"], b["h"]); x["l"] = min(x["l"], b["l"]); x["c"] = b["c"]; x["n"] += 1

    # ---- setups and signals (same functions as the live engine)
    setups = {}; mh_cache = {}
    for i in range(700, n):
        m = frame_metrics(b5[i - 218:i + 1], 300)
        if not m.get("valid"): continue
        ct = T[i] + 300
        j = bisect.bisect_right(T15, ct - 900) - 1
        if j < 220: continue
        if j not in mh_cache:
            mh_cache = {j: frame_metrics(b15[j - 218:j + 1], 900)}
        d = scalp_direction(m, trend_dir(mh_cache[j]), S)
        if d: setups[i] = (d, m)

    def run(dirn, stop, target, f, hold=72):
        for k in range(f, min(f + hold + 1, n)):
            c = b5[k]
            if k - f >= hold: return "TIME", c["c"], k
            hs = c["l"] <= stop if dirn == "LONG" else c["h"] >= stop
            ht = c["h"] >= target if dirn == "LONG" else c["l"] <= target
            if hs: return "STOP", stop, k
            if ht and k != f: return "TARGET", target, k
        return None

    def er(i, steps, step):
        if i - steps * step < 0: return 0
        pts = [C[i - k * step] for k in range(steps + 1)]
        path = sum(abs(pts[k] - pts[k + 1]) for k in range(steps))
        return abs(pts[0] - pts[-1]) / path if path else 0

    sigs, busy, by_day = [], -1, defaultdict(int)
    for i in sorted(setups):
        d, m = setups[i]
        if i <= busy or i + 80 >= n: continue
        if sum(1 for k in range(i - 24, i) if k in setups and setups[k][0] == d) < 2: continue
        x = ext_atr(d, m)
        if x is None or x > 3.0: continue
        E = m["close"]; D = E * 0.005; sg = 1 if d == "LONG" else -1
        f = next((k for k in (i + 1, i + 2) if (Ll[k] <= E if sg > 0 else Hh[k] >= E)), None)
        if f is None: continue
        res = {}
        for mode, dd, st, tg in (("F", d, E - sg * D, E + sg * 1.5 * D), ("R", "SHORT" if sg > 0 else "LONG", E + sg * D, E - sg * 2 * D)):
            o = run(dd, st, tg, f)
            if not o: break
            r, xp, k = o
            res[mode] = ((xp - E) * (1 if dd == "LONG" else -1) / D - FE * E / D - (FM if r == "TARGET" else FE) * xp / D, r, k)
        if len(res) < 2: continue
        nolimit = run(d, E - sg * D, E + sg * 1.5 * D, f, 288)
        busy = nolimit[2] if nolimit else f + 288
        # ---- information available at the signal bar close
        day = (T[i] + IST) // 86400; td = days[day]; prev = [days[day - k] for k in range(1, 11) if day - k in days and days[day - k]["n"] >= 280]
        if len(prev) < 6 or day - 1 not in days: continue
        adr = sum(p["h"] - p["l"] for p in prev) / len(prev)
        i0 = td["i0"]; th, tl = max(Hh[i0:i + 1]), min(Ll[i0:i + 1])
        vw = (pv[i + 1] - pv[i0]) / (vv[i + 1] - vv[i0]) if vv[i + 1] > vv[i0] else E
        j60 = bisect.bisect_right(T60, T[i] + 300 - 3600) - 1; j15 = bisect.bisect_right(T15, T[i] + 300 - 900) - 1
        atr60 = sum(b["h"] - b["l"] for b in b60[j60 - 23:j60 + 1]) / 24
        yd = days[day - 1]; p3 = [days[day - k] for k in (1, 2, 3) if day - k in days]
        out_pd = int(E > yd["h"] if sg > 0 else E < yd["l"])
        ob = 0
        while ob < 40 and (b15[j15 - ob]["c"] > yd["h"] if sg > 0 else b15[j15 - ob]["c"] < yd["l"]): ob += 1
        otf = 0
        while otf < 12 and (b60[j60 - otf]["l"] > b60[j60 - otf - 1]["l"] if sg > 0 else b60[j60 - otf]["h"] < b60[j60 - otf - 1]["h"]): otf += 1
        v4 = vv[i + 1] - vv[i - 47]; vb = [vv[i + 1 - 288 * k] - vv[i - 47 - 288 * k] for k in range(1, 11) if i - 47 - 288 * k > 0]
        h24, l24 = max(Hh[i - 287:i + 1]), min(Ll[i - 287:i + 1])
        last = sigs[-1] if sigs else None
        f_ = {"er6": er(i, 24, 3), "er12": er(i, 24, 6), "er24": er(i, 24, 12), "adx60": adx60[j60] or 0, "adx15": adx15[j15] or 0,
              "vwapdist": sg * (E - vw) / atr60, "side8": sum(1 for k in range(max(i0, i - 95), i + 1) if (C[k] - vw) * sg > 0) / max(1, i + 1 - max(i0, i - 95)),
              "todayrng": (th - tl) / adr, "r24": (h24 - l24) / adr, "out_pd": out_pd, "out_bars": ob,
              "out_3d": int(E > max(p["h"] for p in p3) if sg > 0 else E < min(p["l"] for p in p3)), "otf": otf,
              "d3": sg * (E / C[i - 864] - 1) * 100, "todaydir": sg * (E - td["o"]) / adr, "rvol": v4 / (sum(vb) / len(vb)) if vb and sum(vb) > 0 else 1,
              "ydaynr": (yd["h"] - yd["l"]) / adr, "hr": ((T[i] + IST) % 86400) // 3600, "w1": sg * (E / C[i - 12] - 1) * 100,
              "ext24": ((E - l24) / (h24 - l24) if sg > 0 else (h24 - E) / (h24 - l24)) if h24 > l24 else 0.5,
              "nsetup": sum(1 for k in range(i - 24, i) if k in setups and setups[k][0] == d), "extatr": x,
              "sigtoday": by_day[day], "prevF": (1 if last["F"] > 0 else -1) if last and last["xk"] < i else 0, "long": int(sg > 0)}
        by_day[day] += 1
        sigs.append({"i": i, "t": T[i], "F": res["F"][0], "R": res["R"][0], "fr": res["F"][1], "rr": res["R"][1], "xk": res["F"][2], "f": f_})

    N = len(sigs); cut = int(N * 0.6); tr, te = sigs[:cut], sigs[cut:]
    mean = lambda xs: sum(xs) / len(xs) if xs else 0
    def line(name, g):
        c = defaultdict(int)
        for s in g: c[s["fr"]] += 1
        return f"{name:<9} n={len(g):4d} | follow: mean {mean([s['F'] for s in g]):+.3f}R total {sum(s['F'] for s in g):+7.1f}R win {100 * mean([s['F'] > 0 for s in g]):3.0f}% T/S/6h {c['TARGET']}/{c['STOP']}/{c['TIME']} | reverse: mean {mean([s['R'] for s in g]):+.3f}R total {sum(s['R'] for s in g):+7.1f}R win {100 * mean([s['R'] > 0 for s in g]):3.0f}%"
    print(f"\nsignals {N} ({N / max(1, (T[-1] - T[700]) / 86400):.1f}/day); real site rate was ~4/day")
    print(line("ALL", sigs)); print(line("TRAIN", tr)); print(line("TEST", te))
    print("\nBY MONTH")
    bm = defaultdict(list)
    for s in sigs: bm[time.strftime("%Y-%m", time.gmtime(s["t"]))].append(s)
    for k in sorted(bm): print(line(k, bm[k]))

    print("\nFEATURE SPLITS (terciles from TRAIN): mean R per bucket low/mid/high — follow | reverse | advantage follow−reverse")
    spreads = {}
    def buckets(g, key, cuts):
        return [[s for s in g if sum(s["f"][key] >= c for c in cuts) == b] for b in range(len(cuts) + 1)]
    for key in sigs[0]["f"]:
        vals = sorted(s["f"][key] for s in tr)
        cuts = sorted(set([vals[len(vals) // 3], vals[2 * len(vals) // 3]]))
        if key in ("out_pd", "out_3d", "long"): cuts = [1]
        if key == "prevF": cuts = [0, 1]
        if key == "hr": cuts = [6, 12, 18]
        outs = []
        for g in (tr, te):
            bs = buckets(g, key, cuts)
            outs.append(" ".join(f"{mean([s['F'] for s in b]):+.2f}" for b in bs) + " | " + " ".join(f"{mean([s['R'] for s in b]):+.2f}" for b in bs) + " | " + " ".join(f"{mean([s['F'] - s['R'] for s in b]):+.2f}" for b in bs) + " n " + "/".join(str(len(b)) for b in bs))
        bs = buckets(tr, key, cuts); adv = [mean([s["F"] - s["R"] for s in b]) for b in bs]
        spreads[key] = (adv[-1] - adv[0], cuts)
        print(f"{key:<9}{[round(c, 2) for c in cuts]}\n   TRAIN {outs[0]}\n   TEST  {outs[1]}")

    # ---- automatic composite from train, judged on test
    sel = [(k, v[0]) for k, v in spreads.items() if abs(v[0]) >= 0.35 and k not in ("hr",)]
    def rank_fn(key):
        vals = sorted(s["f"][key] for s in tr)
        return lambda v: bisect.bisect_left(vals, v) / len(vals) - 0.5
    rf = {k: rank_fn(k) for k, _ in sel}
    score = lambda s: sum((1 if sp > 0 else -1) * rf[k](s["f"][k]) for k, sp in sel) / max(1, len(sel))
    print("\nAUTO COMPOSITE uses:", [(k, round(sp, 2)) for k, sp in sel])
    sc = sorted(score(s) for s in tr); lo, hi = sc[len(sc) // 3], sc[2 * len(sc) // 3]
    for name, g in (("TRAIN", tr), ("TEST", te)):
        bl = [[s for s in g if score(s) < lo], [s for s in g if lo <= score(s) < hi], [s for s in g if score(s) >= hi]]
        print(f" {name} composite low/mid/high: follow " + " ".join(f"{mean([s['F'] for s in b]):+.2f}" for b in bl) + " | reverse " + " ".join(f"{mean([s['R'] for s in b]):+.2f}" for b in bl) + " n " + "/".join(str(len(b)) for b in bl))
        sw = [s["F"] if score(s) >= hi else s["R"] if score(s) < lo else 0 for s in g]
        sw2 = [s["F"] if score(s) >= hi else s["R"] for s in g]
        print(f"   switch(high→follow, low→reverse, mid→skip): total {sum(sw):+.1f}R over {sum(1 for x in sw if x)} trades | switch(mid→reverse): {sum(sw2):+.1f}R | always follow {sum(s['F'] for s in g):+.1f}R | always reverse {sum(s['R'] for s in g):+.1f}R")
    # daily-level truth: how many days does each side win
    dd = defaultdict(lambda: [0.0, 0.0])
    for s in sigs:
        d = (s["t"] + IST) // 86400; dd[d][0] += s["F"]; dd[d][1] += s["R"]
    print(f"\ndays with signals {len(dd)}: follow-better days {sum(1 for v in dd.values() if v[0] > v[1])}, reverse-better {sum(1 for v in dd.values() if v[1] > v[0])}; both-negative days {sum(1 for v in dd.values() if v[0] < 0 and v[1] < 0)}")


asyncio.run(main())
