"""Delta Top 3: three PAPER strategies from the owner's workbook ('Top20 any - P&L', ranked by Delta net after fees, 22 Nov 2026).

  1. Row 9  - SMA 9/21 cross, 1h, 1:3, Normal: SMA(9) crosses above SMA(21) -> BUY, below -> SELL.
  2. Row 18 - CCI 50 back from -100/+100, 4h, 1:2, Reverse: CCI(50) climbs back above -100 -> SELL; falls back below +100 -> BUY.
  3. Row 8  - RSI 2 back from 10/90, 4h, 1:2, Reverse: RSI(2) climbs back above 10 -> SELL; falls back below 90 -> BUY.

Same engine as Candle Runs (runs.py): candles built from Delta BTCUSD 5-minute candles (it reuses the Candle Runs candle store),
signal at a candle close, entry at the next 5-minute open, stop 2 x ATR(14) of that timeframe, target RR x stop, time exit
72 h (1h) / 7 days (4h), stop first, one trade at a time per strategy, strategies entering together share one $10,000 position.
Paper journal only: no orders, no alerts, not read by Auto-Trade (admin request 22 Nov 2026).
"""
import asyncio
import json
import logging
import os

import runs
from core import now_utc, iso

log = logging.getLogger("trio")
NAME = "Delta Top 3"
LIVE_IST = os.environ.get("TRIO_LIVE_START", "2026-11-22 16:00")
STATE = {"computed_at": None, "error": None, "data": None}
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trio_history.json")
_HIST = {}

STRATS = [
    {"key": "sma", "rank": 1, "wb": "Row 9", "name": "SMA 9/21 cross · 1h · 1:3", "tf": "1h", "kind": "sma", "rr": 3.0, "hold_h": 72, "reverse": False,
     "rule": ("Check every 1h candle close. When the 9-candle simple average of the close crosses ABOVE the 21-candle average, BUY at the next open; "
              "when it crosses BELOW, SELL. Stop = 2 x ATR(14) of the 1h candles. Target = 3 x the stop distance. If neither is hit, close after 72 hours."),
     "how": "Trend following: the fast average crossing the slow one marks a new short-term trend; the 1:3 target lets the few big trends pay for many small losses.",
     "evidence": {"delta_trades": 574, "delta_win": 0.350, "delta_net": 11280, "binance_net": 10677, "replicates": "Yes", "same_trades": 0.956}},
    {"key": "cci", "rank": 2, "wb": "Row 18", "name": "CCI 50 back from ±100 · 4h · 1:2 · Reverse", "tf": "4h", "kind": "cci", "rr": 2.0, "hold_h": 168, "reverse": True,
     "rule": ("Check every 4h candle close (01:30, 05:30, 09:30, 13:30, 17:30, 21:30 IST). CCI(50) = (typical price - its 50-candle average) / (0.015 x mean deviation). "
              "When CCI climbs back ABOVE -100 (it was below), SELL at the next open; when it falls back BELOW +100 (it was above), BUY. "
              "Stop = 2 x ATR(14), target = 2 x the stop distance, close after 7 days."),
     "how": ("'Reverse' of the usual CCI rule: instead of betting on the bounce out of an extreme, it bets that the move which pushed CCI to the extreme continues "
             "after a small pause."),
     "evidence": {"delta_trades": 188, "delta_win": 0.441, "delta_net": 10179, "binance_net": 10150, "replicates": "Yes", "same_trades": 0.947}},
    {"key": "rsi", "rank": 3, "wb": "Row 8", "name": "RSI 2 back from 10/90 · 4h · 1:2 · Reverse", "tf": "4h", "kind": "rsi", "rr": 2.0, "hold_h": 168, "reverse": True,
     "rule": ("Check every 4h candle close. RSI(2) (Wilder). When RSI climbs back ABOVE 10 (it was below 10), SELL at the next open; when it falls back BELOW 90 "
              "(it was above 90), BUY. Stop = 2 x ATR(14), target = 2 x the stop distance, close after 7 days."),
     "how": ("'Reverse' of the classic RSI(2) snap-back: after a very oversold or overbought 4h candle, it trades in the direction of the move that caused it, "
             "betting the push resumes."),
     "evidence": {"delta_trades": 285, "delta_win": 0.432, "delta_net": 10080, "binance_net": 10069, "replicates": "Yes", "same_trades": 0.954}},
]


def history():
    if "doc" not in _HIST:
        try:
            with open(HISTORY_FILE) as f:
                _HIST["doc"] = json.load(f)
        except Exception as e:
            log.warning(f"delta top 3 history not loaded: {e}")
            _HIST["doc"] = {"rows": []}
    return _HIST["doc"]


def _sma(x, n):
    out, s = [None] * len(x), 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def _cci(bs, n=50):
    tp = [(b["h"] + b["l"] + b["c"]) / 3 for b in bs]
    out = [None] * len(bs)
    for i in range(n - 1, len(bs)):
        w = tp[i - n + 1:i + 1]; m = sum(w) / n; md = sum(abs(x - m) for x in w) / n
        out[i] = (tp[i] - m) / (0.015 * md) if md else None
    return out


def _rsi(c, n=2):
    """Wilder RSI (same as pandas ewm(alpha=1/n, adjust=False) on gains/losses, first value from the first change)."""
    out, up, dn = [None] * len(c), None, None
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]; g, l_ = max(d, 0.0), max(-d, 0.0)
        up = g if up is None else up + (g - up) / n
        dn = l_ if dn is None else dn + (l_ - dn) / n
        out[i] = None if dn == 0 else 100 - 100 / (1 + up / dn)
    return out


def signals(s, bs, at, ch):
    """[(signal time, side, stop distance, reason, chg30)] - Normal direction first, flipped for Reverse."""
    c = [b["c"] for b in bs]
    raw = []
    if s["kind"] == "sma":
        a, z = _sma(c, 9), _sma(c, 21)
        for i in range(1, len(bs)):
            if None in (a[i], z[i], a[i - 1], z[i - 1]):
                continue
            if a[i] > z[i] and a[i - 1] <= z[i - 1]:
                raw.append((i, 1, f"SMA 9 ({a[i]:,.0f}) crossed above SMA 21 ({z[i]:,.0f})"))
            elif a[i] < z[i] and a[i - 1] >= z[i - 1]:
                raw.append((i, -1, f"SMA 9 ({a[i]:,.0f}) crossed below SMA 21 ({z[i]:,.0f})"))
    elif s["kind"] == "cci":
        x = _cci(bs)
        for i in range(1, len(bs)):
            if x[i] is None or x[i - 1] is None:
                continue
            if x[i - 1] < -100 <= x[i]:
                raw.append((i, 1, f"CCI(50) climbed back above -100 ({x[i - 1]:.0f} -> {x[i]:.0f})"))
            elif x[i - 1] > 100 >= x[i]:
                raw.append((i, -1, f"CCI(50) fell back below +100 ({x[i - 1]:.0f} -> {x[i]:.0f})"))
    else:
        x = _rsi(c)
        for i in range(1, len(bs)):
            if x[i] is None or x[i - 1] is None:
                continue
            if x[i - 1] < 10 <= x[i]:
                raw.append((i, 1, f"RSI(2) climbed back above 10 ({x[i - 1]:.1f} -> {x[i]:.1f})"))
            elif x[i - 1] > 90 >= x[i]:
                raw.append((i, -1, f"RSI(2) fell back below 90 ({x[i - 1]:.1f} -> {x[i]:.1f})"))
    out = []
    for i, side, why in raw:
        if s["reverse"]:
            side = -side
            why += f": Reverse rule, so {'BUY' if side > 0 else 'SELL'}"
        else:
            why += f": {'BUY' if side > 0 else 'SELL'}"
        out.append((bs[i]["tc"], side, runs.STOP_ATR * at[i], why, ch[i]))
    return out


def build(b5, live, hist=None):
    replay_t, live_t = runs._ts(runs.REPLAY_IST), runs._ts(LIVE_IST)
    hdoc = history() if hist is None else hist
    frames = {}
    for tf in runs.TF:
        bs = runs.bars(b5, tf)
        frames[tf] = (bs, runs.atr(bs), runs.chg30(bs))
    allrows, per = [], []
    for s in STRATS:
        bs, at, ch = frames[s["tf"]]
        old = sorted([dict(r, strategy_name=s["name"], wb=s["wb"], status="CLOSED", period="Backtest", signal_ist=runs._ist(r["signal_t"]),
                           entry_ist=runs._ist(r["entry_t"]), exit_ist=runs._ist(r["exit_t"]), time_exit_ist=runs._ist(r["entry_t"] + s["hold_h"] * 3600))
                      for r in hdoc.get("rows", []) if r.get("strategy") == s["key"] and r["signal_t"] < replay_t], key=lambda r: r["entry_t"])
        start = max([replay_t] + [r["exit_t"] for r in old])
        rows = runs.simulate(s, signals(s, bs, at, ch), b5, start, live)
        for r in rows:
            r["period"] = "Live" if r["signal_t"] >= live_t else "Replay"
        allrows += old + rows
        per.append({k: s[k] for k in ("key", "rank", "wb", "name", "tf", "kind", "rr", "hold_h", "reverse", "rule", "how", "evidence")})
    allrows.sort(key=lambda r: r["entry_t"])
    positions = runs.split_positions(allrows)
    for p in per:
        mine = [r for r in allrows if r["strategy"] == p["key"]]
        p.update(live=runs.summary([r for r in mine if r["period"] == "Live"]), all=runs.summary([r for r in mine if r["period"] != "Backtest"]),
                 full=runs.summary(mine), retest={"verdict": "DELTA", **p["evidence"]})
    h4 = frames["4h"]
    mnow = None
    if h4[0] and h4[2][-1] is not None:
        mnow = {"chg30": round(h4[2][-1], 4), "type": runs.market_type(h4[2][-1]), "as_of_ist": runs._ist(h4[0][-1]["tc"])}
    return {"name": NAME, "paper_only": True, "alerts": False, "live_start_ist": LIVE_IST, "replay_from_ist": runs.REPLAY_IST, "live_price": live,
            "market_now": mnow, "strategies": per, "positions": positions[::-1],
            "totals": {"live": runs.summary_pos([p for p in positions if p["period"] == "Live"]),
                       "all": runs.summary_pos([p for p in positions if p["period"] != "Backtest"]), "full": runs.summary_pos(positions)},
            "backtest": {"source": hdoc.get("source"), "from_ist": hdoc.get("from_ist"), "to_ist": hdoc.get("to_signal_before_ist"),
                         "trades": sum(1 for r in allrows if r["period"] == "Backtest")},
            "position_rule": ("One $10,000 position per entry time. If two strategies enter at the same time on the same side, they share that one position "
                              "equally; each part keeps its own target and exits on its own. No double trade."),
            "last_candle_ist": runs._ist(b5[-1]["t"] + 300) if b5 else None,
            "settings": {"stop": "2 x ATR(14) of the strategy's timeframe", "entry": "next 5-minute open after the signal candle closes (market order, taker)",
                         "fees": "0.05% taker each side + 18% GST", "notional": runs.NOTIONAL, "one_trade_at_a_time": True, "alerts": "none (paper only)",
                         "auto_trade": "not used"}}


async def compute():
    import plan_a
    b5 = runs.STATE.get("bars5") or []
    if not b5:
        return STATE                                    # Candle Runs has not loaded its Delta candles yet
    live = plan_a.STATE.get("live_price") or b5[-1]["c"]
    data = build(b5, live)
    data["computed_at"] = iso(now_utc())
    STATE.update(computed_at=data["computed_at"], error=None, data=data)
    return STATE


async def loop():
    await asyncio.sleep(150)
    while True:
        try:
            await compute()
        except Exception as e:
            STATE["error"] = str(e)[:200]
            log.exception("delta top 3 compute failed")
        await asyncio.sleep(120)
