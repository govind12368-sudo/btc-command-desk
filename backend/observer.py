"""Strategy Observer: watches every page and every journal 24/7 and says, with numbers, what works and what does not.

Two kinds of evidence, always shown side by side so a lucky week is never mistaken for an edge:
  * Recent  = the site's own signals and journals (Trade Journal, Plan A, Day-type switch, Regime, Auto-Trade, Validation).
  * History = the same signals replayed over 26 months (observer_history.json, built offline by research/observer_history.py).
Forward recording: when a scalp signal fires, the reading of every page (dashboard read, scanner, pillars, market
intelligence, news, live order flow) is saved once, so each page can be judged on real outcomes as trades close.
Read-only: nothing here changes a strategy, a journal or an order.
"""
import asyncio
import json
import logging
import math
import os
from datetime import datetime, timezone

from core import db, now_utc, iso, IST
import plan_a
import regime
import support

log = logging.getLogger("observer")
STATE = {"computed_at": None, "error": None, "data": None}
MIN_N = 20


def _ist(ts, fmt="%Y-%m-%d %H:%M"):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(IST).strftime(fmt) if ts else ""


def history():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "observer_history.json")) as f:
            return json.load(f)
    except Exception:
        return None


# ------------------------------------------------------------------ statistics
def wilson(w, n, z=1.96):
    if not n:
        return None, None
    p = w / n; d = 1 + z * z / n; c = p + z * z / (2 * n); h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - h) / d, (c + h) / d


def z_vs_rest(w, n, W, N):
    """How far a bucket's win rate is from the rest of the signals, in standard errors."""
    n2, w2 = N - n, W - w
    if n < 1 or n2 < 1:
        return 0.0
    p = W / N
    se = math.sqrt(p * (1 - p) * (1 / n + 1 / n2)) if 0 < p < 1 else 0
    return (w / n - w2 / n2) / se if se else 0.0


def book_stats(trades):
    """trades: dicts with net (USD), win (bool), r (R or None)."""
    n = len(trades)
    if not n:
        return {"n": 0}
    wins = [t for t in trades if t["win"]]; losses = [t for t in trades if not t["win"]]
    aw = sum(t["net"] for t in wins) / len(wins) if wins else None
    al = -sum(t["net"] for t in losses) / len(losses) if losses else None
    be = al / (al + aw) if aw and al and (al + aw) > 0 else None
    lo, hi = wilson(len(wins), n)
    rs = [t["r"] for t in trades if t.get("r") is not None]
    gw, gl = sum(t["net"] for t in wins), -sum(t["net"] for t in losses)
    eq = pk = dd = 0.0
    for t in trades:
        eq += t["net"]; pk = max(pk, eq); dd = min(dd, eq - pk)
    win = len(wins) / n
    verdict = ("Too few trades to judge" if n < MIN_N else "Proven edge (95%)" if be and lo > be else
               "Above break-even, not proven yet" if be and win > be else "Losing (proven)" if be and hi < be else "No edge shown")
    return {"n": n, "wins": len(wins), "win": win, "win_lo": lo, "win_hi": hi, "breakeven": be, "net": round(sum(t["net"] for t in trades), 2),
            "avg_r": round(sum(rs) / len(rs), 3) if rs else None, "pf": round(gw / gl, 2) if gl > 0 else None, "max_dd": round(dd, 2),
            "avg_win": aw, "avg_loss": al, "verdict": verdict}


# ------------------------------------------------------------------ the site's trades, per book
def legs(rows):
    """Every filled, closed signal from the Trade Journal / Plan A engine, with both legs."""
    out = []
    for r in rows:
        if r.get("fill_status") != "FILLED":
            continue
        risk = abs(float(r["entry"]) - float(r["model_stop"])) * float(r.get("qty") or 0)
        base = {"signal_id": r["signal_id"], "cap_t": r["cap_t"], "side": r["model_dir"], "day_type": r.get("day_type"), "playbook": r.get("playbook")}
        if r.get("follow_result") not in (None, "OPEN"):
            base["follow"] = {"net": r["follow_net"], "win": r["follow_net"] > 0, "r": r["follow_net"] / risk if risk else None,
                              "res": r["follow_result"], "why": r.get("follow_why") or ""}
        if r.get("plan_result") not in (None, "OPEN"):
            base["fade"] = {"net": r["net"], "win": r["net"] > 0, "r": r["net"] / risk if risk else None, "res": r["plan_result"], "why": r.get("plan_why") or ""}
        out.append(base)
    return out


def switch_trades(L, snaps):
    out = []
    for x in L:
        leg = {"FOLLOW": "follow", "PLAN_A": "fade"}.get(x.get("playbook"))
        if not leg or leg not in x:
            continue
        if support.gate_on(x["cap_t"]) and not support.allows(snaps.get(x["signal_id"])):
            continue
        out.append({**x[leg], "signal_id": x["signal_id"], "leg": leg})
    return out


def regime_trades(rows):
    return [{"net": r["net"], "win": r["net"] > 0, "r": r.get("r"), "res": r.get("result"), "signal_id": r["signal_id"], "rule": r.get("rule") or r.get("base_rule")}
            for r in rows if r.get("status") == "CLOSED"]


# ------------------------------------------------------------------ why trades won or lost
def why_bucket(res, why):
    if res == "TARGET":
        try:
            worst = float(why.split("at worst it was ")[1].split("x")[0])
        except Exception:
            worst = None
        return "Win: clean (never 0.5R against)" if worst is not None and worst < 0.5 else "Win: had to sit through a dip first"
    if res == "TIME":
        return "6-hour exit: no follow-through either way"
    if res == "STOP":
        first = "went against at once" if why.startswith("Went against at once") else "was in profit, then turned"
        after = ("target reached later (stop too close)" if "target level anyway" in why else
                 "price came back later (noise took the stop)" if "came back to the entry" in why else "kept going against (wrong direction)")
        return f"Loss: {first}; {after}"
    return None


def reasons(trades):
    c = {}
    for t in trades:
        k = why_bucket(t.get("res"), t.get("why") or "")
        if k:
            c[k] = c.get(k, 0) + 1
    n = sum(c.values())
    return [{"reason": k, "n": v, "share": v / n} for k, v in sorted(c.items(), key=lambda kv: -kv[1])]


# ------------------------------------------------------------------ data factors (same buckets as the 26-month study)
def _want(side):
    return "Up" if side == "LONG" else "Down"


FACTORS = [
    ("day_type", "Day type (BTC Scanner)", lambda s, f: {"RANGE": "Range", "TREND_UP": "Trend up", "TREND_DOWN": "Trend down"}.get(s["day_type"], "Unclear")),
    ("side", "Signal side (Scalping Signal)", lambda s, f: s["side"]),
    ("rsi", "RSI 15m (Market Intelligence)", lambda s, f: None if f.get("rsi") is None else "under 30" if f["rsi"] < 30 else "30-40" if f["rsi"] < 40 else "40-60" if f["rsi"] < 60 else "60-70" if f["rsi"] < 70 else "70+"),
    ("trend_agree", "15m/1h/4h trends with the signal", lambda s, f: None if f.get("t4h") is None else f"{sum(f.get(k) == _want(s['side']) for k in ('t15', 't1h', 't4h'))} of 3"),
    ("t4h_vs_signal", "4h trend vs the signal", lambda s, f: None if f.get("t4h") is None else "with signal" if f["t4h"] == _want(s["side"]) else "mixed" if f["t4h"] == "Mixed" else "against signal"),
    ("stop_atr", "Stop size vs hourly ATR", lambda s, f: None if f.get("stop_atr") is None else "under 0.6x" if f["stop_atr"] < 0.6 else "0.6-0.8x" if f["stop_atr"] < 0.8 else "0.8-1.2x" if f["stop_atr"] < 1.2 else "1.2x+"),
    ("pos24", "Position in the 24h range (Live Market)", lambda s, f: None if f.get("pos24") is None else "below 10%" if f["pos24"] < 0.1 else "10-50%" if f["pos24"] < 0.5 else "50-90%" if f["pos24"] < 0.9 else "90%+"),
    ("volume", "Signal-candle volume vs average", lambda s, f: None if f.get("vr") is None else "under 1x" if f["vr"] < 1 else "1-1.5x" if f["vr"] < 1.5 else "1.5-2x" if f["vr"] < 2 else "2x+"),
    ("chg4h_vs_signal", "Last 4h move vs the signal", lambda s, f: None if f.get("chg4h") is None else (lambda c: "moved 1%+ with signal" if c > 0.01 else "moved 1%+ against" if c < -0.01 else "flat (within 1%)")(f["chg4h"] * (1 if s["side"] == "LONG" else -1))),
    ("session", "Time of day (IST)", lambda s, f: (lambda h: "00-06 IST" if h < 6 else "06-12 IST" if h < 12 else "12-18 IST" if h < 18 else "18-24 IST")(int(_ist(s["cap_t"], "%H")))),
]


def factor_table(L, feats, H):
    out = []
    hf = (H or {}).get("factors", {}); ho = (H or {}).get("overall", {})
    for key, name, fn in FACTORS:
        g = {}
        for x in L:
            b = fn(x, feats.get(x["signal_id"]) or {})
            if b is not None:
                g.setdefault(b, []).append(x)
        hb = hf.get(key, {})
        buckets = []
        for b in sorted(set(g) | set(hb)):
            xs = g.get(b, [])
            row = {"bucket": b}
            for leg in ("follow", "fade"):
                t = [x[leg] for x in xs if leg in x]
                row[f"site_{leg}"] = {"n": len(t), "wins": sum(y["win"] for y in t), "net": round(sum(y["net"] for y in t), 2)}
                h = (hb.get(b) or {}).get(leg) or {}
                o = ho.get(leg) or {}
                row[f"hist_{leg}"] = {**{k: h.get(k) for k in ("n", "wins", "win", "avg_r", "avg_r_gross")},
                                      "z": round(z_vs_rest(h.get("wins", 0), h.get("n", 0), o.get("wins", 0), o.get("n", 0)), 2) if h.get("n") else None}
            buckets.append(row)
        # direction effect: follow and reverse move in OPPOSITE ways (the data says which side wins);
        # volatility effect: both move the SAME way (the data only says how often a 2R target is reached)
        zd = max([abs((r["hist_follow"]["z"] or 0) - (r["hist_fade"]["z"] or 0)) / math.sqrt(2) for r in buckets] + [0])
        zv = max([abs((r["hist_follow"]["z"] or 0) + (r["hist_fade"]["z"] or 0)) / math.sqrt(2) for r in buckets] + [0])
        zmax = max(zd, zv)
        best = max([r[f"hist_{l}"]["avg_r"] for r in buckets for l in ("follow", "fade") if r[f"hist_{l}"].get("avg_r") is not None and (r[f"hist_{l}"].get("n") or 0) >= 150] or [None]) if buckets else None
        verdict = ("Helps pick direction (strong evidence)" if zd >= 3 else "Helps pick direction (weak evidence)" if zd >= 2 else
                   "Changes how often targets are hit, not direction" if zv >= 3 else "No measurable effect over 26 months")
        out.append({"key": key, "name": name, "zmax": round(zmax, 2), "z_direction": round(zd, 2), "z_volatility": round(zv, 2), "verdict": verdict, "best_hist_avg_r": best,
                    "beats_fees": best is not None and best > 0, "buckets": buckets})
    return sorted(out, key=lambda f: -f["zmax"])


def extremes(H, k=6):
    """Best and worst conditions over 26 months (buckets with 150+ trades), by average R after fees."""
    items = []
    for key, name, _ in FACTORS:
        for b, v in ((H or {}).get("factors", {}).get(key) or {}).items():
            for leg in ("follow", "fade"):
                s = v.get(leg) or {}
                if (s.get("n") or 0) >= 150:
                    items.append({"factor": name, "bucket": b, "leg": "Follow the signal" if leg == "follow" else "Reverse it (Plan A)", "n": s["n"],
                                  "win": s["win"], "avg_r": s["avg_r"], "avg_r_gross": s.get("avg_r_gross")})
    items.sort(key=lambda x: -x["avg_r"])
    return {"best": items[:k], "worst": items[-k:][::-1]}


# ------------------------------------------------------------------ forward recording: every page's reading at each signal
async def record_snapshots():
    """For each scalp signal of the last 10 minutes, save every page's reading once (never rebuilt later)."""
    cut = iso(datetime.fromtimestamp(now_utc().timestamp() - 600, timezone.utc))
    new = [s async for s in db.signals.find({"strategy": "scalp", "captured_at": {"$gte": cut}}, {"_id": 1, "direction": 1, "captured_at": 1})]
    for s in new:
        if await db.observer_snaps.find_one({"_id": s["_id"]}, {"_id": 1}):
            continue
        doc = {"_id": s["_id"], "side": s.get("direction"), "captured_at": s.get("captured_at"), "at": iso(now_utc()), "pages": {}}
        try:
            import desk, engine, intel, scanner
            scan = scanner.STATE["result"] or scanner.compute(engine.STATE)
            rd = {"scanner": desk.r_scanner(scan, "intraday"), "pillars": desk.r_pillars(engine.STATE, "intraday"),
                  "intel": desk.r_intel(intel.STATE, "intraday"), "news": desk.r_news(intel.STATE)}
            comp = desk.compose("intraday", rd)
            doc["pages"] = {k: {"value": v[0], "detail": v[1]} for k, v in rd.items()}
            doc["pages"]["dashboard"] = {"value": None if comp["score"] is None else comp["score"] / 100, "detail": f"Market read {comp['score']} ({comp['label']})"}
            I = intel.STATE or {}
            w = ((I.get("pressure") or {}).get("windows") or {}).get("1h") or {}
            if w.get("buy_share") is not None:
                doc["pages"]["live_market"] = {"value": (w["buy_share"] - 0.5) * 4, "detail": f"Spot 1h {w['buy_share'] * 100:.0f}% buying"}
            pos = ((I.get("deriv") or {}).get("positioning") or {}).get("label")
            if pos:
                doc["pages"]["positioning"] = {"value": desk.BIAS.get(pos), "detail": f"Futures positioning {pos}"}
        except Exception as e:
            doc["error"] = str(e)[:160]
        await db.observer_snaps.update_one({"_id": s["_id"]}, {"$setOnInsert": doc}, upsert=True)


PAGE_KEYS = {"dashboard": "BTC Dashboard (market read)", "scanner": "BTC Scanner", "pillars": "Five Pillars", "intel": "Market Intelligence",
             "news": "News & Events", "live_market": "Live Market (spot order flow)", "positioning": "Futures positioning"}


def forward_pages(L, snaps):
    """Did a page's reading at the signal agree with the trade that won? (recorded since this page went live)."""
    by = {x["signal_id"]: x for x in L}
    out = []
    for k, name in PAGE_KEYS.items():
        g = {"agreed": [], "against": [], "neutral": []}
        for sid, s in snaps.items():
            x = by.get(sid)
            p = (s.get("pages") or {}).get(k) or {}
            if not x or "follow" not in x or p.get("value") is None:
                continue
            v = p["value"] * (1 if x["side"] == "LONG" else -1)
            g["agreed" if v > 0.15 else "against" if v < -0.15 else "neutral"].append(x)
        row = {"key": k, "name": name}
        for b, xs in g.items():
            row[b] = {"n": len(xs), "follow_wins": sum(x["follow"]["win"] for x in xs), "fade_wins": sum(x["fade"]["win"] for x in xs if "fade" in x)}
        row["n"] = sum(row[b]["n"] for b in g)
        out.append(row)
    return out


def support_checks(L, snaps):
    """Day-type switch support checks (recorded since 16 Nov): wins of the switch leg when each check passed or failed."""
    by = {x["signal_id"]: x for x in L}
    keys = {}
    for sid, s in snaps.items():
        x = by.get(sid); leg = {"FOLLOW": "follow", "PLAN_A": "fade"}.get(s.get("plan"))
        if not x or not leg or leg not in x:
            continue
        for c in s.get("checks") or []:
            k = keys.setdefault(c["key"], {"key": c["key"], "name": c.get("name") or c["key"], "pass": [0, 0], "fail": [0, 0]})
            if c.get("ok") is True:
                k["pass"][0] += 1; k["pass"][1] += x[leg]["win"]
            elif c.get("ok") is False:
                k["fail"][0] += 1; k["fail"][1] += x[leg]["win"]
    return list(keys.values())


# ------------------------------------------------------------------ pages
def page_cards(B, F, fwd, chk, val, H, swing_n):
    f = {x["key"]: x for x in F}
    hb = (H or {}).get("books", {})
    fmt = lambda s: "no closed trades yet" if not s.get("n") else f"{s['wins']}/{s['n']} wins ({s['win']:.0%}), {('+' if s['net'] >= 0 else '−')}${abs(s['net']):,.0f} after fees"
    hfmt = lambda s: f"26 months: {s.get('n', 0):,} trades, {s.get('win', 0):.0%} targets, {s.get('avg_r', 0):+.2f}R after fees ({s.get('avg_r_gross', 0):+.2f}R before fees)" if s else ""
    fw = {x["key"]: x for x in fwd}

    def fwd_txt(k):
        x = fw.get(k) or {}
        if not x.get("n"):
            return "Recording its reading at every signal from now on; judged once trades close."
        a, g = x["agreed"], x["against"]
        return f"Since recording started: when it agreed with the signal, following won {a['follow_wins']}/{a['n']}; when it was against, following won {g['follow_wins']}/{g['n']}."
    sc = {c["key"]: c for c in chk}

    def chk_txt(k):
        c = sc.get(k)
        if not c:
            return ""
        p, q = c["pass"], c["fail"]
        return f" Switch support check '{c['name']}': passed → {p[1]}/{p[0]} wins, failed → {q[1]}/{q[0]} wins."
    vt = "; ".join(f"{m['model']}: {m['wins']}/{m['n']} target first ({m['win']:.0%} vs {m['breakeven']:.0%} needed)" for m in val if m["n"]) or "no decided snapshots yet"
    cards = [
        ("BTC Dashboard", "/", "Blends Scanner, Pillars, Intelligence and News into one market read.", "Useful for context; not yet proven to pick winners.", fwd_txt("dashboard") + chk_txt("market_read")),
        ("Live Market", "/market", "Price, spot order flow, funding and open interest right now.", "Order flow is the most direct short-term data we have; still being measured.", fwd_txt("live_market") + chk_txt("order_flow")),
        ("Scalping Signal", "/scalping", "The trigger for every journal on this site.", "Good timing trigger, no direction edge: before fees, following and reversing it both come out near 0R.",
         f"{hfmt(hb.get('follow'))}. Reversed: {hfmt(hb.get('fade'))}."),
        ("Swing Signal", "/swing", "Longer-term trade ideas.", "Not measured: swing signals have no tracked result yet.", f"{swing_n} swing signals recorded. Needs a journal before it can be judged."),
        ("Five Pillars", "/pillars", "Direction model from five groups of data.", "Measured on Trade Validation and recorded at every signal.", f"Validation: {vt}. " + fwd_txt("pillars")),
        ("BTC Scanner", "/scanner", "Day type (range / trend / unclear) that drives the Day-type switch.", (f.get("day_type") or {}).get("verdict", ""),
         "Day type over 26 months: " + "; ".join(f"{b['bucket']}: follow {b['hist_follow']['win']:.0%}, reverse {b['hist_fade']['win']:.0%} targets" for b in (f.get("day_type") or {}).get("buckets", []) if b["hist_follow"].get("win") is not None) + "."),
        ("Market Intelligence", "/intel", "Trends, RSI, macro, ETF and on-chain data.", (f.get("trend_agree") or {}).get("verdict", ""),
         "Trend agreement over 26 months: " + "; ".join(f"{b['bucket']} with the signal: follow {b['hist_follow']['win']:.0%}" for b in (f.get("trend_agree") or {}).get("buckets", []) if b["hist_follow"].get("win") is not None) + ". Macro / ETF / on-chain move too slowly to time a 6-hour scalp. " + fwd_txt("intel") + chk_txt("higher_tf")),
        ("News & Events", "/news", "Headlines and the economic calendar.", "A risk filter, not a direction signal.", fwd_txt("news") + chk_txt("no_shock")),
        ("Trade Journal", "/journal", "Every signal traded in its own direction (1:2).", B["follow"].get("verdict", ""), f"Recent: {fmt(B['follow'])}. {hfmt(hb.get('follow'))}."),
        ("Plan A", "/plan-a", "Every signal reversed (1:2).", B["fade"].get("verdict", ""), f"Recent: {fmt(B['fade'])}. {hfmt(hb.get('fade'))}."),
        ("Day-type switch", "/", "Range → Plan A, trend with the signal → follow; Strong support only from 19 Nov.", B["switch"].get("verdict", ""), f"Recent: {fmt(B['switch'])}. {hfmt(hb.get('switch'))}."),
        ("Regime Journal", "/regime", "Market-type rules, 1.5R target, max 2 a day.", B["regime"].get("verdict", ""), f"Recent: {fmt(B['regime'])}. {hfmt(hb.get('regime'))}. Reverse book recent: {fmt(B['regime_rev'])}."),
        ("Trade Validation", "/validation", "Checks the direction models against what price did next.", "Honest scorecard of the direction models.", vt + "."),
        ("Auto-Trade", "/auto-trade", "Places the Day-type switch trades on Delta (testnet now).", "Execution check: compares real fills with the paper journal.", f"Recent: {fmt(B['auto'])}."),
    ]
    return [{"page": a, "to": b, "role": c, "verdict": d, "evidence": e} for a, b, c, d, e in cards]


def conclusions(B, F, H, ex):
    out = []
    hb = (H or {}).get("books", {})
    gross = [hb[k].get("avg_r_gross") for k in ("follow", "fade", "switch", "regime") if hb.get(k)]
    net = [hb[k].get("avg_r") for k in ("follow", "fade", "switch", "regime") if hb.get(k)]
    if gross and net:
        out.append(("Main finding", f"Over 26 months, every way of trading these signals is close to 0R before fees ({min(gross):+.3f}R to {max(gross):+.3f}R a trade) and loses after fees "
                    f"({min(net):+.3f}R to {max(net):+.3f}R). Fees cost about {abs(sum(net) / len(net) - sum(gross) / len(gross)):.2f}R a trade because the stop is only 0.5%. "
                    "The signal times entries but does not predict direction; costs decide the result."))
    rec = [(k, B[k]) for k in ("follow", "fade", "switch", "regime", "regime_rev") if B[k].get("n")]
    if rec:
        k, s = max(rec, key=lambda kv: kv[1]["win"])
        name = {"follow": "Trade Journal (follow)", "fade": "Plan A", "switch": "Day-type switch", "regime": "Regime", "regime_rev": "Regime reverse"}[k]
        hs = hb.get(k) or {}
        head = f"{name}: {s['wins']}/{s['n']} ({s['win']:.0%})" + (f", break-even {s['breakeven']:.0%}" if s.get("breakeven") else "")
        tail = (f". Over 26 months the same book made {hs.get('avg_r', 0):+.2f}R a trade after fees, so treat the recent run as likely luck until it holds for 50+ trades."
                if hs and (hs.get("avg_r") or 0) < 0 else ".")
        out.append(("Highest recent win rate", head + tail))
    dirn = [x for x in F if x["z_direction"] >= 2]
    vol = [x for x in F if x["z_direction"] < 2 and x["z_volatility"] >= 3]
    useless = [x for x in F if x["verdict"].startswith("No measurable")]
    if dirn:
        tail = ". Even so, no group of any of them is profitable after fees over 26 months." if not any(x["beats_fees"] for x in dirn) else "."
        out.append(("Data that helps pick the side", "; ".join(f"{x['name']} ({x['z_direction']:.1f} standard errors)" for x in dirn[:4]) + tail))
    else:
        out.append(("Data that helps pick the side", "None of the tested data reliably tells follow from reverse over 26 months."))
    if vol:
        out.append(("Data that only changes how often targets are hit", "; ".join(x["name"] for x in vol)
                    + ": it moves follow and reverse the same way (quiet markets reach the 2R target less often), so it says how far price will travel, not which way."))
    if useless:
        out.append(("Data that does not help pick trades", ", ".join(x["name"] for x in useless) + ": no measurable effect on winners over 26 months."))
    if ex.get("best"):
        b = ex["best"][0]
        out.append(("Best place to get in (26 months)", f"{b['leg']} when {b['factor']} is {b['bucket']}: {b['win']:.0%} targets, {b['avg_r']:+.2f}R after fees "
                    f"({b['avg_r_gross']:+.2f}R before) on {b['n']} trades. The best condition still {'loses' if b['avg_r'] < 0 else 'makes'} money after fees."))
    if ex.get("worst"):
        w = ex["worst"][0]
        out.append(("Worst place to get in (26 months)", f"{w['leg']} when {w['factor']} is {w['bucket']}: {w['win']:.0%} targets, {w['avg_r']:+.2f}R after fees on {w['n']} trades."))
    out.append(("What would help next", "1) Cut the cost per trade: with a 0.5% stop, fees take about 0.2R from every trade; a wider stop (about 1%) halves that, and limit (maker) entries cut the entry fee. "
                "2) Keep recording every page's reading at each signal (started today) and let 50+ trades close before trusting any filter. "
                "3) Funding, open-interest change and liquidation data at the signal are not yet stored over 26 months; they are the best untested candidates."))
    return [{"title": a, "text": b} for a, b in out]


# ------------------------------------------------------------------ main
async def compute():
    H = history()
    rows = plan_a.STATE.get("rows") or []
    L = legs(rows)
    snaps = await support.all_snaps()
    feats = {d["_id"]: d async for d in db.regime_feat.find({"v": regime.FEAT_V})}
    rg = (regime.STATE.get("data") or {})
    auto = [{"net": t.get("net_usd") or 0, "win": (t.get("net_usd") or 0) > 0, "r": None, "res": t.get("exit_reason"), "mode": t.get("mode")}
            async for t in db.autotrade_trades.find({"status": "CLOSED"})]
    B = {"follow": book_stats([x["follow"] for x in L if "follow" in x]), "fade": book_stats([x["fade"] for x in L if "fade" in x]),
         "switch": book_stats(switch_trades(L, snaps)), "regime": book_stats(regime_trades((rg.get("regime") or {}).get("rows") or [])),
         "regime_rev": book_stats(regime_trades((rg.get("reverse") or {}).get("rows") or [])), "auto": book_stats(auto)}
    val = []
    async for g in db.validation_snapshots.aggregate([{"$match": {"outcome": {"$in": ["TARGET FIRST", "STOP FIRST"]}}},
                                                      {"$group": {"_id": "$model", "n": {"$sum": 1}, "wins": {"$sum": {"$cond": [{"$eq": ["$outcome", "TARGET FIRST"]}, 1, 0]}},
                                                                  "tr": {"$avg": "$target_ratio"}}}]):
        be = 1 / (1 + g["tr"]) if g.get("tr") else None
        val.append({"model": g["_id"], "n": g["n"], "wins": g["wins"], "win": g["wins"] / g["n"] if g["n"] else None, "breakeven": be})
    F = factor_table(L, feats, H)
    ex = extremes(H)
    osnaps = {d["_id"]: d async for d in db.observer_snaps.find({})}
    fwd = forward_pages(L, osnaps)
    chk = support_checks(L, snaps)
    swing_n = await db.signals.count_documents({"strategy": "swing"})
    hb = (H or {}).get("books", {})
    names = {"follow": ("Trade Journal – follow every signal (2R)", "/journal"), "fade": ("Plan A – reverse every signal (2R)", "/plan-a"),
             "switch": ("Day-type switch (Strong support only from 19 Nov)", "/"), "regime": ("Regime Strategy (1.5R)", "/regime"),
             "regime_rev": ("Regime reverse (1.5R)", "/regime"), "auto": ("Auto-Trade – real fills (testnet)", "/auto-trade")}
    board = [{"key": k, "name": names[k][0], "to": names[k][1], "recent": B[k], "history": hb.get(k)} for k in names]
    board.sort(key=lambda b: -(b["recent"].get("win") or 0))
    rsn = {"follow": reasons([x["follow"] for x in L if "follow" in x]), "fade": reasons([x["fade"] for x in L if "fade" in x]),
           "switch": reasons(switch_trades(L, snaps))}
    first = min((x["cap_t"] for x in L), default=None)
    data = {"computed_at": iso(now_utc()), "recent_from": _ist(first) if first else None, "signals": len(rows), "closed_signals": len(L),
            "board": board, "factors": F, "extremes": ex, "reasons": rsn, "forward_pages": fwd, "support_checks": chk, "validation": val,
            "pages": page_cards(B, F, fwd, chk, val, H, swing_n), "conclusions": conclusions(B, F, H, ex),
            "history_period": [_ist(t, "%d %b %Y") for t in (H or {}).get("period", [])], "history_signals": (H or {}).get("signals"),
            "history_by_market": (H or {}).get("books_by_market"), "history_by_half": (H or {}).get("books_by_half"), "regime_rules_history": (H or {}).get("regime_by_rule"),
            "recorded_snapshots": len(osnaps)}
    day = _ist(int(now_utc().timestamp()), "%Y-%m-%d")
    await db.observer_log.update_one({"_id": day}, {"$set": {"at": data["computed_at"], "board": [{"key": b["key"], "n": b["recent"].get("n"), "win": b["recent"].get("win"),
                                                                                                     "net": b["recent"].get("net")} for b in board]}}, upsert=True)
    data["log"] = [d async for d in db.observer_log.find({}).sort("_id", -1).limit(14)]
    STATE.update(computed_at=data["computed_at"], error=None, data=data)
    return STATE


async def loop():
    await asyncio.sleep(90)
    n = 0
    while True:
        try:
            await record_snapshots()
            if n % 10 == 0:
                await compute()
        except Exception as e:
            STATE["error"] = str(e)[:200]
            log.exception("observer failed")
        n += 1
        await asyncio.sleep(60)
