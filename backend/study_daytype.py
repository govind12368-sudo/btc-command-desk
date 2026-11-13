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
import daytype as DT, json

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
        dt = DT.classify(b5[i - 143:i + 1], adr)
        sigs.append({"i": i, "t": T[i], "F": res["F"][0], "R": res["R"][0], "fr": res["F"][1], "rr": res["R"][1], "xk": res["F"][2], "f": f_,
                     "dt": dt["type"], "pb": DT.playbook(dt, d), "rk": res["R"][2]})

    mean = lambda xs: sum(xs) / len(xs) if xs else 0
    def st(g, k):
        return {"n": len(g), "mean_r": round(mean([x[k] for x in g]), 3), "total_r": round(sum(x[k] for x in g), 1), "win": round(100 * mean([x[k] > 0 for x in g]))}
    def switch(g):
        v = [x["F"] if x["pb"] == "FOLLOW" else x["R"] if x["pb"] == "PLAN_A" else None for x in g]
        v = [x for x in v if x is not None]
        return {"n": len(v), "mean_r": round(mean(v), 3), "total_r": round(sum(v), 1), "win": round(100 * mean([x > 0 for x in v]))}
    def dd(vals):
        eq = pk = worst = 0.0
        for x in vals:
            eq += x; pk = max(pk, eq); worst = min(worst, eq - pk)
        return round(worst, 1)
    ev = {"period": [time.strftime("%Y-%m-%d", time.gmtime(T[700])), time.strftime("%Y-%m-%d", time.gmtime(T[-1]))], "signals": len(sigs),
          "per_day": round(len(sigs) / ((T[-1] - T[700]) / 86400), 1), "fee_r": round(mean([(FE * 2) / 0.005 for _ in sigs]), 3),
          "follow": st(sigs, "F"), "plan_a": st(sigs, "R"), "switch": switch(sigs),
          "dd": {"follow": dd([x["F"] for x in sigs]), "plan_a": dd([x["R"] for x in sigs])},
          "by_tag": {k: {"follow": st([x for x in sigs if x["dt"] == k], "F"), "plan_a": st([x for x in sigs if x["dt"] == k], "R")} for k in ("TREND_UP", "TREND_DOWN", "RANGE", "MIXED")},
          "by_playbook": {k: {"follow": st([x for x in sigs if x["pb"] == k], "F"), "plan_a": st([x for x in sigs if x["pb"] == k], "R")} for k in ("FOLLOW", "PLAN_A", "STAND_ASIDE")}}
    bm = defaultdict(list)
    for x in sigs: bm[time.strftime("%Y-%m", time.gmtime(x["t"]))].append(x)
    ev["by_month"] = [{"month": k, "n": len(g), "follow": round(sum(x["F"] for x in g), 1), "plan_a": round(sum(x["R"] for x in g), 1), "switch": switch(g)["total_r"], "switch_n": switch(g)["n"]} for k, g in sorted(bm.items())]
    half = len(sigs) // 2
    ev["halves"] = [{"follow": st(g, "F")["mean_r"], "plan_a": st(g, "R")["mean_r"], "switch": switch(g)["mean_r"]} for g in (sigs[:half], sigs[half:])]
    # hourly: how often each type, and what the NEXT 6 hours did
    dr = DT.daily_ranges(b5); share = defaultdict(int); cont = defaultdict(list); inside = []
    for i in range(288 * 12, n - 73, 12):
        a = DT.adr(dr, DT.ist_day(T[i]))
        d = DT.classify(b5[i - 143:i + 1], a)
        share[d["type"]] += 1
        nxt = (C[i + 72] - C[i]) / a
        if d["type"].startswith("TREND"):
            cont[d["type"]].append(nxt * (1 if d["type"] == "TREND_UP" else -1))
        if d["type"] == "RANGE":
            inside.append(max(Hh[i + 1:i + 73]) <= d["high"] and min(Ll[i + 1:i + 73]) >= d["low"])
    tot = sum(share.values())
    ev["share"] = {k: round(100 * v / tot) for k, v in share.items()}
    allc = cont["TREND_UP"] + cont["TREND_DOWN"]
    ev["after_trend"] = {"n": len(allc), "continued_pct": round(100 * mean([x > 0 for x in allc])), "mean_move_adr": round(mean(allc), 3)}
    ev["after_range"] = {"n": len(inside), "stayed_inside_pct": round(100 * mean(inside))}
    # end-of-day truth vs the call made at noon IST
    hit = tot2 = 0
    for day, v in dr.items():
        if v[2] < 288 or day - 1 not in dr: continue
        i0 = bisect.bisect_left(T, day * 86400 - IST)
        if i0 < 288 * 12 or i0 + 288 > n: continue
        a = DT.adr(dr, day)
        e = DT.eod_label(b5[i0:i0 + 288], a); c = DT.classify(b5[i0 + 144 - 143:i0 + 145], a)["type"]
        if e in ("NA",) or c == "NA": continue
        tot2 += 1; hit += (e == c)
    ev["noon_vs_eod"] = {"days": tot2, "same_pct": round(100 * hit / max(1, tot2))}
    print("EVIDENCE_JSON " + json.dumps(ev, separators=(",", ":")))


asyncio.run(main())
