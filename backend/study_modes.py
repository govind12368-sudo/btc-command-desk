"""One-off study: follow-the-signal vs Plan A reverse on the same scalp signals, same fill rule,
same 6-hour exit, real Delta 1-minute candles. Prints a compact report."""
import asyncio, bisect, json, os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
os.environ.setdefault("MONGO_URL", "mongodb://unused"); os.environ.setdefault("DB_NAME", "study")
import motor.motor_asyncio as mm
from mongomock_motor import AsyncMongoMockClient
mm.AsyncIOMotorClient = lambda *a, **k: AsyncMongoMockClient()
import plan_a as P

IST = timezone(timedelta(hours=5, minutes=30))
NEW = [("SCALP-SHORT-1794510000", "2026-11-13T00:35:08", "SHORT", 71500.5, 71858.5, 70963.5, 0.14),
       ("SCALP-SHORT-1794536400", "2026-11-13T07:55:05", "SHORT", 70550.5, 70903.5, 70021.0, 0.14),
       ("SCALP-SHORT-1794538500", "2026-11-13T08:30:05", "SHORT", 70209.5, 70561.0, 69682.5, 0.14),
       ("SCALP-SHORT-1794544800", "2026-11-13T10:15:05", "SHORT", 68602.0, 68945.5, 68087.0, 0.14)]
H = 3600


def ts(s):
    return int(datetime.fromisoformat(s).replace(tzinfo=IST).timestamp())


def ist(t, f="%m-%d %H:%M"):
    return datetime.fromtimestamp(t, IST).strftime(f)


async def main():
    fx = json.load(open(os.path.join(os.path.dirname(__file__), "plan_a_seed.json")))
    sigs = [(s["signal_id"], s["captured_ist"], s["direction"], s["entry"], s["stop"], s["target"], s["qty"]) for s in fx["signals"] if s["signal_id"].startswith("SCALP")]
    have = {s[0] for s in sigs}
    sigs = sorted(sigs + [n for n in NEW if n[0] not in have], key=lambda s: s[1])
    now = int(datetime.now(timezone.utc).timestamp()); now -= now % 60
    bars = sorted(await P._fetch_1m(ts(sigs[0][1]) - 100 * H, now), key=lambda c: c["t"])
    T = [c["t"] for c in bars]
    print(f"candles {len(bars)} from {ist(T[0])} to {ist(T[-1])} IST; signals {len(sigs)}")

    def px(t):  # close of the last completed minute before t
        i = bisect.bisect_left(T, t - t % 60) - 1
        return bars[max(i, 0)]["c"]

    def hilo(a, b):
        i, j = bisect.bisect_left(T, a), bisect.bisect_left(T, b)
        seg = bars[i:j] or [bars[min(i, len(bars) - 1)]]
        return max(c["h"] for c in seg), min(c["l"] for c in seg)

    def er(t, hours, steps):
        step = hours * H // steps
        pts = [px(t - k * step) for k in range(steps + 1)]
        path = sum(abs(pts[k] - pts[k + 1]) for k in range(steps))
        return abs(pts[0] - pts[-1]) / path if path else 0

    rows = []
    for sid, cap_s, d0, e, stp, tgt, q in sigs:
        cap = ts(cap_s); cm = cap - cap % 60; sgn = 1 if d0 == "LONG" else -1; D = abs(e - stp)
        i0 = bisect.bisect_left(T, cm)
        sub = bars[i0:i0 + 400]
        fill = next((c for c in sub if cm <= c["t"] < cap + P.VALID_SEC and P._touch_entry(d0, c, e, cm)), None)
        p0 = px(cap)
        h72, l72 = hilo(cap - 72 * H, cap); pos = (p0 - l72) / (h72 - l72)
        h48, l48 = hilo(cap - 48 * H, cap - H); h1, l1 = hilo(cap - H, cap)
        rng4 = [(lambda hl: hl[0] - hl[1])(hilo(cap - (k + 1) * 4 * H, cap - k * 4 * H)) for k in range(19)]
        atr1 = sum((lambda hl: hl[0] - hl[1])(hilo(cap - (k + 1) * H, cap - k * H)) for k in range(24)) / 24
        f = {"w24": sgn * (p0 / px(cap - 24 * H) - 1) * 100, "w4": sgn * (p0 / px(cap - 4 * H) - 1) * 100, "w1": sgn * (p0 / px(cap - H) - 1) * 100,
             "er24": er(cap, 24, 24), "er6": er(cap, 6, 24), "ext": pos if sgn > 0 else 1 - pos,
             "brk": int(h1 >= h48 if sgn > 0 else l1 <= l48), "vol": rng4[0] / (sum(rng4[1:]) / 18), "datr": D / atr1, "hr": int(ist(cap, "%H"))}
        r = {"id": sid.replace("SCALP-", ""), "cap": cap, "dir": d0, "D": D, "q": q, "f": f, "fill": None}
        if fill:
            ft = fill["t"]; r["fill"] = ft
            for mode, dd, s_, t_ in (("F", d0, stp, tgt), ("R", "SHORT" if d0 == "LONG" else "LONG", e + sgn * D, e - sgn * 2 * D),
                                     ("F20", d0, stp, e + sgn * 2.0 * D), ("F24", d0, stp, e + sgn * 2.42 * D)):
                res, bar = P._first_exit(dd, s_, t_, sub, ft, P.HOLD_SEC)
                xp = t_ if res == "TARGET" else s_ if res == "STOP" else (bar["c"] if bar else bars[-1]["c"])
                g = (xp - e) * q * (1 if dd == "LONG" else -1)
                fee = P._fee(e, q, "taker") + P._fee(xp, q, "maker" if res == "TARGET" else "taker")
                r[mode] = {"res": res or "OPEN", "x": bar["t"] if bar else now, "R": (xp - e) * (1 if dd == "LONG" else -1) / D, "net": g - fee}
            hh, ll = hilo(ft, ft + P.HOLD_SEC)
            r["mfe"] = ((hh - e) if sgn > 0 else (e - ll)) / D; r["mae"] = ((e - ll) if sgn > 0 else (hh - e)) / D
        rows.append(r)

    fl = [r for r in rows if r["fill"]]
    print("\nPER SIGNAL  id | capture IST | follow res R net | reverse res R net | MFE MAE (in stop distances, 6h) | with-trend 24h% 4h% | ER24 ER6 | ext brk vol D/ATR")
    for r in rows:
        f = r["f"]; base = f"{r['id']:<17}|{ist(r['cap'])}|"
        if not r["fill"]:
            print(base + " NOT FILLED"); continue
        F, R = r["F"], r["R"]
        print(base + f"{F['res'][:3]} {F['R']:+.2f} {F['net']:+7.1f}|{R['res'][:3]} {R['R']:+.2f} {R['net']:+7.1f}|{r['mfe']:.1f} {r['mae']:.1f}|{f['w24']:+.1f} {f['w4']:+.1f}|{f['er24']:.2f} {f['er6']:.2f}|{f['ext']:.2f} {f['brk']} {f['vol']:.2f} {f['datr']:.2f}")

    def port(mode):  # one trade at a time
        busy, eq, peak, dd, out, days = 0, 0.0, 0.0, 0.0, [], defaultdict(float)
        for r in fl:
            if r["fill"] >= busy:
                m = r[mode]; busy = m["x"]; eq += m["net"]; peak = max(peak, eq); dd = min(dd, eq - peak); out.append(m); days[ist(m["x"], "%m-%d")] += m["net"]
        c = defaultdict(int)
        for m in out:
            c[m["res"]] += 1
        return {"taken": len(out), "skipped": len(fl) - len(out), "T/S/6h": f"{c['TARGET']}/{c['STOP']}/{c['TIME']}", "win%": round(100 * sum(m["net"] > 0 for m in out) / len(out)),
                "net": round(eq, 1), "maxDD": round(dd, 1)}, days

    for m in ("F", "F20", "F24", "R"):
        pm, _ = port(m)
        tw = [r[m]["net"] for r in fl if r[m]["res"] == "TARGET"]; ts = [r[m]["net"] for r in fl if r[m]["res"] == "STOP"]
        print(f"LIVE44 {m:<4}", pm, f"avg target ${sum(tw)/max(1,len(tw)):+.1f} avg stop ${sum(ts)/max(1,len(ts)):+.1f}")
    print("\nONE TRADE AT A TIME (model qty, fees incl.)")
    pf, df = port("F"); pr, dr = port("R")
    print(" FOLLOW  ", pf); print(" REVERSE ", pr)
    print(" day     follow  reverse | day range% net% ER(day)")
    for d in sorted(set(df) | set(dr)):
        a = ts(f"2026-{d}T00:00:00"); b = min(a + 24 * H, now)
        hh, ll = hilo(a, b); o, c = px(a + 60), px(b)
        print(f" {d}  {df.get(d, 0):+7.1f} {dr.get(d, 0):+7.1f} | {100 * (hh - ll) / o:4.1f} {100 * (c / o - 1):+5.1f} {er(b, 24, 24):.2f}")

    def split(name, key, cuts=None):
        vals = sorted(key(r) for r in fl)
        cuts = cuts if cuts is not None else [vals[len(vals) // 3], vals[2 * len(vals) // 3]]
        print(f"\nSPLIT {name} (cuts {[round(c, 2) for c in cuts]}): bucket n | follow win% ΣR Σ$ | reverse win% ΣR Σ$")
        for b in range(len(cuts) + 1):
            g = [r for r in fl if sum(key(r) >= c for c in cuts) == b]
            if g:
                w = lambda m: round(100 * sum(r[m]["net"] > 0 for r in g) / len(g))
                print(f"  {b} n={len(g):2d} | {w('F'):3d}% {sum(r['F']['R'] for r in g):+6.1f} {sum(r['F']['net'] for r in g):+8.1f} | {w('R'):3d}% {sum(r['R']['R'] for r in g):+6.1f} {sum(r['R']['net'] for r in g):+8.1f}")

    split("all", lambda r: 0, [])
    for k in ("w24", "w4", "w1", "er24", "er6", "ext", "vol", "datr"):
        split(k, lambda r, k=k: r["f"][k])
    split("breakout of 48h extreme in last hour", lambda r: r["f"]["brk"], [1])
    split("direction LONG=1", lambda r: int(r["dir"] == "LONG"), [1])
    split("hour IST", lambda r: r["f"]["hr"], [6, 12, 18])
    for i, r in enumerate(fl):
        r["prev"] = fl[i - 1]["F"]["net"] > 0 if i else False
        r["prev2"] = sum(x["F"]["net"] > 0 for x in fl[max(0, i - 2):i])
    split("previous signal followed = win", lambda r: int(r["prev"]), [1])
    split("wins among previous 2 signals", lambda r: r["prev2"], [1, 2])
    split("MFE reached (stop distances)", lambda r: r["mfe"], [0.5, 1.0, 1.5])
    both = sum(r["F"]["net"] < 0 and r["R"]["net"] < 0 for r in fl)
    print(f"\nboth lose: {both} of {len(fl)}; follow target {sum(r['F']['res'] == 'TARGET' for r in fl)}, stop {sum(r['F']['res'] == 'STOP' for r in fl)}, 6h {sum(r['F']['res'] == 'TIME' for r in fl)}")
    print("median minutes to exit: follow stop", sorted((r["F"]["x"] - r["fill"]) // 60 for r in fl if r["F"]["res"] == "STOP")[sum(r["F"]["res"] == "STOP" for r in fl) // 2],
          "follow target", sorted((r["F"]["x"] - r["fill"]) // 60 for r in fl if r["F"]["res"] == "TARGET")[sum(r["F"]["res"] == "TARGET" for r in fl) // 2])


asyncio.run(main())
