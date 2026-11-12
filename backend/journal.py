"""Actual-fill accounting and model-signal outcome tracking."""
from core import finite, parse_iso, now_utc, iso, ist_day_bounds

FILL_FIELDS = ["strategy", "direction", "entry_time", "entry_price", "qty_btc", "exit_time", "exit_price",
               "fees_usd", "funding_usd", "stop_price", "exchange_id", "plan_id", "signal_id", "notes"]


def fill_pnl(f, mark=None):
    sign = 1 if f.get("direction") == "LONG" else -1
    e, q, x = f.get("entry_price"), f.get("qty_btc"), f.get("exit_price")
    r = {"gross": None, "net": None, "net_known": False, "unrealized": None, "r_multiple": None, "closed": finite(x)}
    if not finite(e, q):
        return r
    if finite(x):
        r["gross"] = sign * (x - e) * q
        if f.get("fees_usd") is not None and f.get("funding_usd") is not None:
            r["net"] = r["gross"] - f["fees_usd"] - f["funding_usd"]
            r["net_known"] = True
            if finite(f.get("stop_price")) and f["stop_price"] != e:
                r["r_multiple"] = r["net"] / (abs(e - f["stop_price"]) * q)
    elif finite(mark):
        r["unrealized"] = sign * (mark - e) * q
    return r


def complete_open(f):
    return all(f.get(k) not in (None, "") for k in ("strategy", "direction", "entry_time", "entry_price", "qty_btc"))


def stats(fills, capital):
    closed = [(f, fill_pnl(f)) for f in fills if finite(f.get("exit_price"))]
    closed.sort(key=lambda x: x[0].get("exit_time") or "")
    known = [(f, p) for f, p in closed if p["net_known"]]
    eq, peak, dd, curve = capital, capital, 0.0, []
    for f, p in known:
        eq += p["net"]; peak = max(peak, eq); dd = max(dd, peak - eq)
        curve.append({"time": f.get("exit_time"), "equity": eq, "drawdown": peak - eq})
    wins = [p["net"] for _, p in known if p["net"] > 0]
    losses = [p["net"] for _, p in known if p["net"] <= 0]
    rs = [p["r_multiple"] for _, p in known if p["r_multiple"] is not None]
    by = {}
    for f, p in known:
        b = by.setdefault(f.get("strategy") or "other", {"trades": 0, "net": 0.0, "wins": 0})
        b["trades"] += 1; b["net"] += p["net"]; b["wins"] += p["net"] > 0
    return {"closed": len(closed), "closed_known_costs": len(known), "unknown_cost_trades": len(closed) - len(known),
            "net_pnl": sum(p["net"] for _, p in known), "equity": eq, "max_drawdown": dd, "curve": curve,
            "win_rate": len(wins) / len(known) if known else None,
            "expectancy_usd": sum(p["net"] for _, p in known) / len(known) if known else None,
            "profit_factor": (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None,
            "net_r_total": sum(rs) if rs else None, "avg_r": sum(rs) / len(rs) if rs else None, "by_strategy": by,
            "note": "Observed performance from actual fills only; model signals and assumed planner expectancy are excluded."}


def today_limits(fills, s):
    start, end = ist_day_bounds()
    entries = [f for f in fills if f.get("entry_time") and start <= parse_iso(f["entry_time"]) < end]
    closed_today = [f for f in fills if f.get("exit_time") and start <= parse_iso(f["exit_time"]) < end]
    pn = [fill_pnl(f) for f in closed_today]
    unknown = sum(1 for p in pn if not p["net_known"])
    realized = sum(p["net"] for p in pn if p["net_known"])
    open_ = [f for f in fills if not finite(f.get("exit_price"))]
    issues = []
    if len(entries) >= s["max_entries_per_day"]:
        issues.append(f"{len(entries)} entries today reached daily ceiling {s['max_entries_per_day']}")
    if unknown:
        issues.append(f"{unknown} closed trade(s) today have unknown fees/funding — realized net P&L not known")
    elif not realized > -s["daily_loss_limit"]:
        issues.append(f"realized net P&L {realized:.2f} not above −{s['daily_loss_limit']}")
    if len(open_) >= s["max_positions"]:
        issues.append(f"{len(open_)} open positions reached limit {s['max_positions']}")
    if len(open_) >= s["journal_capacity"]:
        issues.append(f"journal working capacity {s['journal_capacity']} reached")
    return {"entries_today": len(entries), "realized_net_today": realized, "unknown_cost_today": unknown,
            "open_positions": len(open_), "issues": issues,
            "detail": f"{len(entries)}/{s['max_entries_per_day']} entries today, realized net {realized:+.2f} USD (limit −{s['daily_loss_limit']}), {len(open_)}/{s['max_positions']} positions, capacity {len(open_)}/{s['journal_capacity']}"}


def first_touch(direction, stop, target, bars, start_ts, res=300, capture_review=True):
    """bars ascending completed 5m bars. Returns outcome dict. Capture candle = bar containing start_ts."""
    prev = None
    for b in bars:
        if b["t"] + res <= start_ts:
            continue
        if prev is None and b["t"] > start_ts - start_ts % res:
            return {"outcome": "HISTORY GAP", "at": iso(start_ts), "frozen": False, "note": "capture candle not in stored history"}
        if prev is not None and b["t"] - prev != res:
            return {"outcome": "HISTORY GAP", "at": iso(prev + res), "frozen": False}
        prev = b["t"]
        hit_t = b["h"] >= target if direction == "LONG" else b["l"] <= target
        hit_s = b["l"] <= stop if direction == "LONG" else b["h"] >= stop
        capture = capture_review and b["t"] <= start_ts < b["t"] + res
        if hit_t or hit_s:
            if capture:
                return {"outcome": "CAPTURE CANDLE REVIEW", "at": iso(b["t"] + res), "frozen": True}
            if hit_t and hit_s:
                return {"outcome": "SAME CANDLE REVIEW", "at": iso(b["t"] + res), "frozen": True}
            return {"outcome": "TARGET FIRST" if hit_t else "STOP FIRST", "at": iso(b["t"] + res), "frozen": True,
                    "note": "candle-end observation time, not an execution time"}
    return {"outcome": "PENDING", "at": None, "frozen": False}


def limit_fill_outcome(direction, entry, stop, target, tick, bars, signal_bar_open, wait_bars, res=300):
    """Post-only limit plan placed at the close of the signal bar.
    Filled only when a later completed bar trades THROUGH the entry by at least one tick, within wait_bars bars.
    On the fill bar only the stop is judged (conservative); TP/SL is measured from the next bar with first_touch.
    Returns fill_status WAITING FILL / FILLED / MISSED plus the usual outcome fields."""
    m = {b["t"]: b for b in bars}
    start = signal_bar_open + res
    last_t = max(m) if m else None
    for k in range(int(wait_bars)):
        t = start + k * res
        b = m.get(t)
        if b is None:
            if last_t is not None and last_t > t:
                return {"fill_status": "WAITING FILL", "outcome": "HISTORY GAP", "at": iso(t), "frozen": False, "filled_at": None}
            return {"fill_status": "WAITING FILL", "outcome": "PENDING", "at": None, "frozen": False, "filled_at": None}
        through = b["l"] <= entry - tick if direction == "LONG" else b["h"] >= entry + tick
        if not through:
            continue
        filled_at = iso(t + res)
        if (b["l"] <= stop) if direction == "LONG" else (b["h"] >= stop):
            return {"fill_status": "FILLED", "filled_at": filled_at, "outcome": "STOP FIRST", "at": filled_at, "frozen": True,
                    "note": "stop touched on the fill candle"}
        o = first_touch(direction, stop, target, bars, t + res, res, capture_review=False)
        return {"fill_status": "FILLED", "filled_at": filled_at, **o}
    return {"fill_status": "MISSED", "filled_at": None, "outcome": "NOT FILLED", "at": iso(start + int(wait_bars) * res), "frozen": True,
            "note": f"limit not traded through within {int(wait_bars)} completed bar(s)"}
