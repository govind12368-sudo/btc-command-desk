"""Candle Runs: three PAPER strategies from the owner's backtest workbook (BTC_Backtest_3Y_All_Strategies.xlsx).
Paper journal only: it never sends an order and sends no alerts (admin request 21 Nov 2026).
Win #4 (big 1h bar, Up market) and Win #2 (4-candle run, Up market, 1:1) were removed on 21 Nov 2026: they failed the re-test.

All five are re-tested in the strategy lab (research/WORKBOOK_TOP10.md): 4h or 1h candles built from Delta BTCUSD 5-minute
candles, signal at a candle close, entry at the next 5-minute open (market order, taker fee), stop = 2 x ATR(14) of that
timeframe from the entry, target = RR x the stop distance, time exit after 7 days (4h) or 72 hours (1h), stop first when
one 5-minute candle touches both, one trade at a time per strategy, $10,000 position (floor(10000/entry, 3) BTC),
Delta taker fee 0.05% each side + 18% GST.
The workbook calls the run rules "... in a row, fade" with direction "Reverse": that trades WITH the run.
"""
import asyncio
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone

from core import now_utc, iso

log = logging.getLogger("runs")
NAME = "Candle Runs"
LIVE_IST = os.environ.get("RUNS_LIVE_START", "2026-11-22 00:00")
REPLAY_IST = os.environ.get("RUNS_REPLAY_FROM", "2026-10-01 00:00")
STOP_ATR, ATR_N, BIG_BAR, NOTIONAL = 2.0, 14, 3.0, 10_000.0
UP_MARKET = 0.05                                   # BTC 30-day change above +5% = Up market
FEES = {"taker": 0.0005, "gst": 0.18}
IST = timezone(timedelta(hours=5, minutes=30))
TF = {"4h": 14400, "1h": 3600}
STATE = {"computed_at": None, "error": None, "data": None, "bars5": []}
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "candle_runs_history.json")
_HIST = {}


def history():
    """3-year backtest rows (closed trades with a signal before the replay start), made by this same code on Binance
    BTCUSDT 5-minute candles in the strategy lab (research/export_runs_history.py). Loaded once."""
    if "doc" not in _HIST:
        try:
            import json
            with open(HISTORY_FILE) as f:
                _HIST["doc"] = json.load(f)
        except Exception as e:
            log.warning(f"candle runs history not loaded: {e}")
            _HIST["doc"] = {"rows": []}
    return _HIST["doc"]

_RUN = ("Check every 4h candle close (01:30, 05:30, 09:30, 13:30, 17:30, 21:30 IST). If the last {n} candles are all green "
        "(close above open), BUY at the next open; if all {n} are red, SELL. Stop = 2 x ATR(14) from the entry. "
        "Target = {rr} x the stop distance. If neither is hit, close after 7 days.")
_WHY = ("Momentum: {n} same-colour 4h candles in a row ({h} hours of one-way buying or selling) usually means real pressure "
        "that keeps going for days, so the trade goes WITH the run. The workbook names it 'fade', but its 'Reverse' setting "
        "flips that: it follows the run.")
STRATS = [
    {"key": "pl1", "rank": 1, "wb": "P&L #1", "name": "4 candles in a row, follow · 1:3", "tf": "4h", "kind": "run", "n": 4, "market": None, "rr": 3.0, "hold_h": 168,
     "rule": _RUN.format(n=4, rr=3), "how": _WHY.format(n=4, h=16),
     "retest": {"verdict": "PASS", "n": 245, "win": 0.453, "net": 23730, "per_trade": 96.86, "unseen_2023": 104.17}},
    {"key": "pl5", "rank": 2, "wb": "P&L #5", "name": "4 candles in a row, follow · 1:2", "tf": "4h", "kind": "run", "n": 4, "market": None, "rr": 2.0, "hold_h": 168,
     "rule": _RUN.format(n=4, rr=2), "how": _WHY.format(n=4, h=16),
     "retest": {"verdict": "PASS", "n": 285, "win": 0.491, "net": 21442, "per_trade": 75.23, "unseen_2023": 94.75}},
    {"key": "win1", "rank": 3, "wb": "Win #1", "name": "5 candles in a row, follow · 1:1", "tf": "4h", "kind": "run", "n": 5, "market": None, "rr": 1.0, "hold_h": 168,
     "rule": _RUN.format(n=5, rr=1), "how": _WHY.format(n=5, h=20),
     "retest": {"verdict": "WEAK", "n": 209, "win": 0.603, "net": 5106, "per_trade": 24.43, "unseen_2023": 17.51}},
]


def _ts(s):
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=IST).timestamp())


def _ist(t):
    return datetime.fromtimestamp(t, IST).strftime("%Y-%m-%d %H:%M")


def market_type(chg30):
    if chg30 is None:
        return "Unknown"
    return "Up" if chg30 > UP_MARKET else "Down" if chg30 < -UP_MARKET else "Range"


def fee(price, q):
    return price * q * FEES["taker"] * (1 + FEES["gst"])


def bars(b5, tf, last_t=None):
    """Candles of `tf` built from 5-minute candles (UTC-aligned). Only periods that have ended and hold at least 90% of
    their 5-minute candles are kept."""
    sec = TF[tf]
    last_t = last_t if last_t is not None else (b5[-1]["t"] if b5 else 0)
    out, cur = [], None
    for c in b5:
        k = c["t"] - c["t"] % sec
        if cur is None or cur["t"] != k:
            if cur:
                out.append(cur)
            cur = {"t": k, "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "n": 1}
        else:
            cur["h"] = max(cur["h"], c["h"]); cur["l"] = min(cur["l"], c["l"]); cur["c"] = c["c"]; cur["n"] += 1
    if cur:
        out.append(cur)
    need = 0.9 * sec / 300
    return [dict(b, tc=b["t"] + sec) for b in out if b["n"] >= need and b["t"] + sec <= last_t + 300]


def atr(bs, n=ATR_N):
    """Wilder ATR (same as the lab: ewm alpha 1/n, first value = first true range)."""
    out, a, pc = [], None, None
    for b in bs:
        tr = b["h"] - b["l"] if pc is None else max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc))
        a = tr if a is None else a + (tr - a) / n
        out.append(a); pc = b["c"]
    return out


def chg30(bs):
    """BTC change over the 30 days before each candle close (None when that candle is missing)."""
    by_tc = {b["tc"]: b["c"] for b in bs}
    return [(b["c"] / by_tc[b["tc"] - 30 * 86400] - 1) if b["tc"] - 30 * 86400 in by_tc else None for b in bs]


def signals(s, bs, at, ch):
    """[(signal time = candle close, side, stop distance, reason, chg30)] for one strategy."""
    out, sign, run = [], 0, 0
    for i, b in enumerate(bs):
        sg = (b["c"] > b["o"]) - (b["c"] < b["o"])
        run = run + 1 if sg == sign else 1
        sign = sg
        mk = market_type(ch[i])
        if s["market"] and mk != s["market"]:
            continue
        if s["kind"] == "run":
            if sg == 0 or run < s["n"]:
                continue
            why = f"{run} {'green' if sg > 0 else 'red'} 4h candles in a row (needs {s['n']}): {'BUY' if sg > 0 else 'SELL'} with the run"
        else:
            if i == 0 or sg == 0 or not (b["h"] - b["l"] > BIG_BAR * at[i - 1]):
                continue
            why = (f"1h candle range {b['h'] - b['l']:,.0f} is {(b['h'] - b['l']) / at[i - 1]:.1f}x ATR (needs > 3x), "
                   f"{'green' if sg > 0 else 'red'}: {'BUY' if sg > 0 else 'SELL'} with it")
        if s["market"]:
            why += f"; {mk} market (BTC {ch[i]:+.1%} over 30 days)"
        out.append((b["tc"], sg, STOP_ATR * at[i], why, ch[i]))
    return out


def simulate(s, sigs, b5, start_t, live):
    """One trade at a time. Entry at the first 5-minute open at/after the signal; stop first; time exit = last candle
    that opens before entry + hold."""
    T = [c["t"] for c in b5]
    import bisect
    rows, busy = [], -1
    for t, side, d, why, ch in sigs:
        if t < start_t or t < busy:
            continue
        j = bisect.bisect_left(T, t)
        if j >= len(T):
            break                                   # the entry candle has not closed yet
        if T[j] - t > 900:
            continue                                # data gap
        e = b5[j]["o"]
        q = math.floor(NOTIONAL / e * 1000) / 1000
        sl, tp = e - side * d, e + side * s["rr"] * d
        end_t = T[j] + s["hold_h"] * 3600
        x = {"strategy": s["key"], "strategy_name": s["name"], "wb": s["wb"], "signal_t": t, "signal_ist": _ist(t), "entry_t": T[j], "entry_ist": _ist(T[j]),
             "side": "LONG" if side > 0 else "SHORT", "entry": e, "sl": round(sl, 1), "tp": round(tp, 1), "d": round(d, 1), "size": q, "rr": s["rr"],
             "chg30": None if ch is None else round(ch, 4), "market": market_type(ch), "reason": why, "time_exit_ist": _ist(end_t)}
        hit, k = None, j
        while k < len(T) and T[k] < end_t:
            c = b5[k]
            if (side > 0 and c["l"] <= sl) or (side < 0 and c["h"] >= sl):
                hit = ("Stop", sl); break
            if (side > 0 and c["h"] >= tp) or (side < 0 and c["l"] <= tp):
                hit = ("Target", tp); break
            k += 1
        if hit is None and (k >= len(T)):
            px = live or b5[-1]["c"]
            gross = side * (px - e) * q; fees = fee(e, q) + fee(px, q)
            x.update(status="OPEN", result="Open", mark=px, gross=gross, fees=fees, net=gross - fees)
            rows.append(x)
            break                                   # one trade at a time: nothing after an open trade
        if hit is None:
            k -= 1; hit = ("Time exit", b5[k]["c"])
        res, xp = hit
        gross = side * (xp - e) * q; fees = fee(e, q) + fee(xp, q)
        busy = T[k] + 300
        x.update(status="CLOSED", result=res, exit_t=busy, exit_ist=_ist(busy), exit_price=xp, gross=gross, fees=fees, net=gross - fees,
                 r=(gross - fees) / (d * q), hold_h=round((busy - T[j]) / 3600, 1))
        rows.append(x)
    return rows


def split_positions(allrows):
    """One $10,000 position per entry time: strategies that enter at the same 5-minute open on the same side share it equally
    (same entry, same stop); each part keeps its own tested target and exits on its own. Rewrites each row's size and P&L to
    its share and returns the positions (oldest first)."""
    groups = {}
    for r in allrows:
        groups.setdefault((r["entry_t"], r["side"]), []).append(r)
    out = []
    for (et, side), parts in sorted(groups.items()):
        k, sd = len(parts), (1 if side == "LONG" else -1)
        parts.sort(key=lambda p: p["rr"])
        for r in parts:
            q = math.floor(NOTIONAL / k / r["entry"] * 1000) / 1000
            px = r["exit_price"] if r["status"] == "CLOSED" else r["mark"]
            r["full_net"] = round(r["net"], 2)
            r["share"], r["size"] = round(1 / k, 4), q
            r["gross"] = sd * (px - r["entry"]) * q
            r["fees"] = fee(r["entry"], q) + fee(px, q)
            r["net"] = r["gross"] - r["fees"]
            if r["status"] == "CLOSED" and r.get("d"):
                r["r"] = r["net"] / (r["d"] * q)
            r["together"] = [p["wb"] for p in parts]
        a = parts[0]
        st = "OPEN" if any(p["status"] == "OPEN" for p in parts) else "CLOSED"
        res = [p["result"] for p in parts]
        pos = {"id": f"{et}-{side}", "entry_t": et, "entry_ist": a["entry_ist"], "signal_ist": a["signal_ist"], "side": side, "entry": a["entry"],
               "sl": a["sl"], "d": a["d"], "period": a["period"], "market": a["market"], "chg30": a.get("chg30"), "time_exit_ist": a.get("time_exit_ist"),
               "members": [p["wb"] for p in parts], "status": st, "result": res[0] if len(set(res)) == 1 else " / ".join(res),
               "gross": sum(p["gross"] for p in parts), "fees": sum(p["fees"] for p in parts), "net": sum(p["net"] for p in parts),
               "exit_ist": max((p.get("exit_ist") or "") for p in parts) if st == "CLOSED" else None,
               "reason": a["reason"] + (f" · {k} strategies signalled together: ONE $10,000 position split {k} ways, each part exits at its own target" if k > 1 else ""),
               "parts": [{k_: p.get(k_) for k_ in ("strategy", "wb", "rr", "tp", "result", "status", "exit_ist", "exit_price", "mark", "size", "share", "gross", "fees", "net", "r", "full_net")}
                         for p in parts]}
        out.append(pos)
    return out


def summary_pos(ps):
    cl = [p for p in ps if p["status"] == "CLOSED"]; op = [p for p in ps if p["status"] == "OPEN"]
    wins = sum(1 for p in cl if p["net"] > 0)
    parts = [x for p in cl for x in p["parts"]]
    net = sum(p["net"] for p in cl); open_pnl = sum(p["net"] for p in op)
    return {"trades": len(ps), "closed": len(cl), "open": len(op), "wins": wins, "losses": len(cl) - wins, "win_rate": wins / len(cl) if cl else None,
            "shared": sum(1 for p in ps if len(p["members"]) > 1),
            "targets": sum(x["result"] == "Target" for x in parts), "stops": sum(x["result"] == "Stop" for x in parts), "time_exits": sum(x["result"] == "Time exit" for x in parts),
            "gross": round(sum(p["gross"] for p in cl), 2), "fees": round(sum(p["fees"] for p in cl), 2), "net": round(net, 2),
            "open_pnl": round(open_pnl, 2), "cumulative": round(net + open_pnl, 2)}


def summary(rows):
    cl = [r for r in rows if r["status"] == "CLOSED"]; op = [r for r in rows if r["status"] == "OPEN"]
    wins = sum(1 for r in cl if r["net"] > 0)
    net = sum(r["net"] for r in cl); open_pnl = sum(r["net"] for r in op)
    return {"trades": len(rows), "closed": len(cl), "open": len(op), "wins": wins, "losses": len(cl) - wins,
            "win_rate": wins / len(cl) if cl else None, "targets": sum(r["result"] == "Target" for r in cl),
            "stops": sum(r["result"] == "Stop" for r in cl), "time_exits": sum(r["result"] == "Time exit" for r in cl),
            "gross": round(sum(r["gross"] for r in cl), 2), "fees": round(sum(r["fees"] for r in cl), 2), "net": round(net, 2),
            "open_pnl": round(open_pnl, 2), "cumulative": round(net + open_pnl, 2)}


async def refresh():
    import daytype
    now = int(time.time()); now -= now % 300
    have = STATE["bars5"]
    floor = _ts(REPLAY_IST) - 32 * 86400
    start = have[-1]["t"] - 600 if have else floor
    new = await daytype._fetch(start, now)
    m = {c["t"]: c for c in have}
    for c in new:
        if c["t"] + 300 <= now:
            m[c["t"]] = {k: c[k] for k in ("t", "o", "h", "l", "c")}
    STATE["bars5"] = [m[t] for t in sorted(m) if t >= floor]


def build(b5, live, now_t=None, hist=None):
    replay_t, live_t = _ts(REPLAY_IST), _ts(LIVE_IST)
    hdoc = history() if hist is None else hist
    names = {s["key"]: s for s in STRATS}
    frames = {}
    for tf in TF:
        bs = bars(b5, tf)
        frames[tf] = (bs, atr(bs), chg30(bs))
    allrows, per = [], []
    for s in STRATS:
        bs, at, ch = frames[s["tf"]]
        old = sorted([dict(r, strategy_name=s["name"], wb=s["wb"], status="CLOSED", period="Backtest", signal_ist=_ist(r["signal_t"]), entry_ist=_ist(r["entry_t"]),
                           exit_ist=_ist(r["exit_t"]), time_exit_ist=_ist(r["entry_t"] + s["hold_h"] * 3600))
                      for r in hdoc.get("rows", []) if r.get("strategy") == s["key"] and r["signal_t"] < replay_t], key=lambda r: r["entry_t"])
        start = max([replay_t] + [r["exit_t"] for r in old])      # one trade at a time across the backtest -> replay join
        rows = simulate(s, signals(s, bs, at, ch), b5, start, live)
        for r in rows:
            r["period"] = "Live" if r["signal_t"] >= live_t else "Replay"
        allrows += old + rows
        per.append({k: s[k] for k in ("key", "rank", "wb", "name", "tf", "kind", "n", "market", "rr", "hold_h", "rule", "how", "retest")})
    allrows.sort(key=lambda r: r["entry_t"])
    positions = split_positions(allrows)
    for p in per:
        mine = [r for r in allrows if r["strategy"] == p["key"]]
        p.update(live=summary([r for r in mine if r["period"] == "Live"]), all=summary([r for r in mine if r["period"] != "Backtest"]), full=summary(mine))
    h4 = frames["4h"]
    mnow = None
    if h4[0] and h4[2][-1] is not None:
        mnow = {"chg30": round(h4[2][-1], 4), "type": market_type(h4[2][-1]), "as_of_ist": _ist(h4[0][-1]["tc"])}
    return {"name": NAME, "paper_only": True, "alerts": False, "live_start_ist": LIVE_IST, "replay_from_ist": REPLAY_IST, "live_price": live,
            "market_now": mnow, "strategies": per, "positions": positions[::-1],
            "totals": {"live": summary_pos([p for p in positions if p["period"] == "Live"]), "all": summary_pos([p for p in positions if p["period"] != "Backtest"]),
                       "full": summary_pos(positions)},
            "position_rule": ("One $10,000 position per entry time. When two or more strategies enter at the same time (same side, entry and stop), "
                              "they share that one position equally; each part keeps its own target and exits on its own. No double trade."),
            "backtest": {"source": hdoc.get("source"), "from_ist": hdoc.get("from_ist"), "to_ist": hdoc.get("to_signal_before_ist"), "trades": sum(1 for r in allrows if r["period"] == "Backtest")},
            "last_candle_ist": _ist(b5[-1]["t"] + 300) if b5 else None,
            "settings": {"stop": "2 x ATR(14) of the strategy's timeframe", "entry": "next 5-minute open after the signal candle closes (market order, taker)",
                         "fees": "0.05% taker each side + 18% GST", "notional": NOTIONAL, "one_trade_at_a_time": True, "shared_position": True, "alerts": "none (paper only)"}}


async def compute():
    import plan_a
    await refresh()
    b5 = STATE["bars5"]
    if not b5:
        return STATE
    live = plan_a.STATE.get("live_price") or b5[-1]["c"]
    data = build(b5, live)
    data["computed_at"] = iso(now_utc())
    STATE.update(computed_at=data["computed_at"], error=None, data=data)
    return STATE


async def loop():
    await asyncio.sleep(90)
    while True:
        try:
            await compute()
        except Exception as e:
            STATE["error"] = str(e)[:200]
            log.exception("candle runs compute failed")
        await asyncio.sleep(120)
