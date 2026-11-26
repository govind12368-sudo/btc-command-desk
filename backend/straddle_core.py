"""Short ATM BTC straddle (Delta Exchange India daily options) – the maths, with no network and no database.

Owner's spec, 26 Nov 2026. Everything here is shared by the backtest replay, the paper bot and the live bot, so the
three can never disagree:
  lots per leg   floor(sizing equity x leverage / (spot x 0.001))
  fee per leg    min(rate x spot x position, cap x option price x position) x (1 + GST)
                 entry = maker rate · stop exit = taker rate · expiry = taker rate on the in-the-money leg only
  stop           combined price (call + put) >= stop multiple x P, P = call fill + put fill
  expiry         in-the-money leg pays |settlement - strike|, the other leg expires worthless
The replay of the owner's workbook must give $4,361.89 (mark, fixed) and $55,572.37 (mark, compounded): tests/test_straddle.py.

The Option Picks bot (owner, 26 Nov 2026) uses the same maths with three differences, all carried by the day facts or the config:
  leg_share 0.5  each leg is sized from half the equity (put capital + call capital = the starting capital)
  per-leg stops  a day can have one leg stopped and the other settled: exit_spot_call / exit_spot_put give each leg's fee
                 notional (the price at its stop, or the settlement), stopped_legs counts the legs bought back
  fees           Option Picks books the taker rate on entry too (maker = taker in its day facts)
Its replay must give $12,298.42 (fixed) and $77,107.84 (compounded, ★ Recommended) at $4,000 × 25x, 1 Jan 2024 – 25 Nov 2026.
"""
import gzip
import io
import json
import math
import os
from datetime import datetime

LOT = 0.001                     # BTC per contract (Delta product field contract_value)
SHEETS = {"mark_fixed": "J_Mark_Fixed", "mark_compound": "J_Mark_Compound", "trade_fixed": "J_Trade_Fixed", "trade_compound": "J_Trade_Compound"}
VARIANTS = {"mark_fixed": ("mark", "fixed"), "mark_compound": ("mark", "compounded"), "trade_fixed": ("trade", "fixed"), "trade_compound": ("trade", "compounded")}
VARIANT_NAMES = {"mark_fixed": "Mark price · Fixed capital", "mark_compound": "Mark price · Compounded",
                 "trade_fixed": "Traded price · Fixed capital", "trade_compound": "Traded price · Compounded"}
SEED_FILE = os.path.join(os.path.dirname(__file__), "data", "straddle_backtest.json.gz")

# raw facts of one backtest day (the same for fixed and compounded): Excel header -> key
RAW = {"Date": "date", "BTC spot @17:00 IST (index)": "spot", "ATM strike": "strike", "Call contract": "call", "Put contract": "put",
       "Entry call price (recorded)": "entry_call", "Entry put price (recorded)": "entry_put", "Exit reason": "exit_reason", "Exit time (IST)": "exit_time",
       "Exit call price (recorded)": "exit_call", "Exit put price (recorded)": "exit_put", "Spot at exit / settlement price": "exit_spot",
       "Maker fee rate": "maker", "Taker fee rate": "taker", "Fee cap (% of premium)": "cap",
       "Volume in entry minute (contracts, both legs)": "volume", "Open interest settled at expiry (contracts, both legs)": "oi",
       "Settlement price source": "settle_source"}

# journal columns (key, Excel header) in the workbook's order; the live journal adds LIVE_EXTRA
COLUMNS = [("date", "Date"), ("day", "Day"), ("spot", "BTC spot @17:00 IST (index)"), ("strike", "ATM strike"), ("call", "Call contract"), ("put", "Put contract"),
           ("entry_call", "Entry call price"), ("entry_put", "Entry put price"), ("p", "Entry premium P ($/BTC)"), ("stop_level", "Stop level ($/BTC)"),
           ("exit_reason", "Exit reason"), ("exit_time", "Exit time (IST)"), ("exit_call", "Exit call price"), ("exit_put", "Exit put price"),
           ("exit_combined", "Exit combined ($/BTC)"), ("exit_spot", "Spot at exit / settlement price"), ("maker", "Maker fee rate"), ("taker", "Taker fee rate"),
           ("cap", "Fee cap (% of premium)"), ("sizing_equity", "Sizing equity ($)"), ("lots", "Lots per leg"), ("position", "Position per leg (BTC)"),
           ("notional", "Notional per leg ($)"), ("premium", "Premium received ($)"), ("cost", "Buy-back / settlement cost ($)"), ("gross", "Gross P&L ($)"),
           ("entry_fees", "Entry fees incl. GST ($)"), ("exit_fees", "Exit/settlement fees incl. GST ($)"), ("fees", "Total fees ($)"), ("net", "Net P&L ($)"),
           ("net_pct", "Net P&L (% of sizing equity)"), ("equity", "Equity after trade ($)"), ("peak", "Peak equity ($)"), ("dd", "Drawdown from peak ($)"),
           ("dd_pct", "Drawdown from peak (%)"), ("result", "Result"), ("exit_over_p", "Exit ÷ P"), ("streak", "Losing streak"), ("month", "Month")]
LIVE_EXTRA = [("mode", "Mode"), ("order_ids", "Order IDs"), ("fill_ts", "Fill timestamps (ms)"), ("latency_ms", "Order latency (ms)"),
              ("entry_slip", "Entry slippage vs mark"), ("exit_slip", "Exit slippage vs mark"), ("liquidity_cap", "Liquidity cap applied"),
              ("size_reduced", "Size reduced"), ("leg_risk_cost", "Leg-risk cost ($)"), ("exchange_leverage", "Order leverage (x)"), ("margin_used", "Margin blocked ($)"),
              ("wallet_change", "Delta wallet change ($)"), ("settle_source", "Settlement source"), ("exit_note", "Exit note"), ("skip_reason", "Skip reason"), ("parity_note", "What the 1-minute rule would have done")]
# the Option Picks bot's own columns (each leg has its own stop)
OP_EXTRA = [("ref_price", "BTC futures at entry (strike reference)"), ("stop_call", "Call stop level"), ("stop_put", "Put stop level"),
            ("call_exit", "Call exit"), ("call_stop_time", "Call stop time (IST)"), ("put_exit", "Put exit"), ("put_stop_time", "Put stop time (IST)"),
            ("stopped_legs", "Legs stopped"), ("real", "Real trades at the fill (backtest)")]


# ---------------- the rules ----------------
def lots_for(equity, leverage, spot, lot=LOT):
    """Lots per leg. The tiny epsilon keeps 398.0000000001-style float noise from changing the floor."""
    if equity <= 0 or leverage <= 0 or spot <= 0:
        return 0
    return int(math.floor(equity * leverage / (spot * lot) + 1e-9))


def leg_fee(rate, cap, spot, price, position, gst):
    """Delta option fee for one leg, GST included."""
    return min(rate * spot * position, cap * price * position) * (1 + gst)


def stop_level(p, multiple):
    return p * multiple


def stop_hit(combined, p, multiple):
    return combined >= stop_level(p, multiple) - 1e-12


def settle_values(strike, settle):
    """(call value, put value) per BTC at expiry."""
    return max(settle - strike, 0.0), max(strike - settle, 0.0)


def pnl(t, lots, gst, lot=LOT):
    """Money for one day's trade. t needs: spot, entry_call, entry_put, exit_reason, exit_call, exit_put, exit_spot, maker, taker, cap."""
    pos = lots * lot
    p = t["entry_call"] + t["entry_put"]
    out_combined = t["exit_call"] + t["exit_put"]
    premium, cost = p * pos, out_combined * pos
    entry_fees = sum(leg_fee(t["maker"], t["cap"], t["spot"], t[k], pos, gst) for k in ("entry_call", "entry_put"))
    # a stop pays the taker fee on both legs; at expiry only the leg with value pays the settlement fee (the other term is 0).
    # A leg with its own exit (Option Picks bot) pays on its own notional: the price at its stop, or the settlement.
    xs = {"exit_call": t.get("exit_spot_call") or t["exit_spot"], "exit_put": t.get("exit_spot_put") or t["exit_spot"]}
    exit_fees = sum(leg_fee(t["taker"], t["cap"], xs[k], t[k], pos, gst) for k in ("exit_call", "exit_put"))
    gross = premium - cost
    return {"p": p, "exit_combined": out_combined, "position": pos, "notional": t["spot"] * pos, "premium": premium, "cost": cost, "gross": gross,
            "entry_fees": entry_fees, "exit_fees": exit_fees, "fees": entry_fees + exit_fees, "net": gross - entry_fees - exit_fees,
            "exit_over_p": out_combined / p if p else None}


def journal(days, cfg, sizing):
    """Full journal rows (COLUMNS) from raw day facts. sizing = 'fixed' or 'compounded'. Days with a skip_reason carry no money."""
    start, lev, gst, mult, share = cfg["starting_capital"], cfg["leverage"], cfg["gst"], cfg["stop_multiple"], cfg.get("leg_share", 1.0)
    eq, peak, streak, rows = start, start, 0, []
    for d in sorted(days, key=lambda x: x["date"]):
        base = {k: d.get(k) for k, _ in COLUMNS + LIVE_EXTRA + OP_EXTRA if k in d}
        base.update(date=d["date"], day=datetime.strptime(d["date"], "%Y-%m-%d").strftime("%a"), month=d["date"][:7])
        extra = d.get("leg_risk_cost") or 0.0          # live only: cost of buying back a leg that filled alone or unevenly
        if d.get("skip_reason") or d.get("entry_call") is None:
            eq -= extra
            rows.append({**base, "equity": eq, "peak": peak, "dd": eq - peak, "dd_pct": (eq - peak) / peak if peak else 0, "result": "Skipped", "lots": 0, "streak": streak,
                         "skip_net": -extra if extra else None})
            continue
        sizing_eq = start if sizing == "fixed" else eq
        lots = d["lots"] if d.get("lots") is not None else lots_for(sizing_eq * share, lev, d["spot"])
        m = pnl(d, lots, gst)
        m["net"] -= extra
        eq += m["net"]
        peak = max(peak, eq)
        win = m["net"] > 0
        streak = 0 if win else streak + 1
        rows.append({**base, **m, "stop_level": None if d.get("stop_call") is not None else stop_level(m["p"], d.get("stop_multiple") or mult), "sizing_equity": sizing_eq, "lots": lots,
                     "net_pct": m["net"] / sizing_eq if sizing_eq else None, "equity": eq, "peak": peak, "dd": eq - peak,
                     "dd_pct": (eq - peak) / peak if peak else 0, "result": "Win" if win else "Loss", "streak": streak})
    return rows


def _avg(xs):
    return sum(xs) / len(xs) if xs else None


def stops_of(r):
    """Stop exits of one row: legs stopped (Option Picks bot) or 1 for a straddle stop."""
    return r["stopped_legs"] if r.get("stopped_legs") is not None else (1 if r.get("exit_reason") == "Stop" else 0)


def metrics(rows, start, first=None, last=None):
    """The owner's Summary sheet, same formulas. rows = journal(); skipped days count as trading days only."""
    tr = [r for r in rows if r.get("lots")]
    nets = [r["net"] for r in tr]
    wins, losses = [x for x in nets if x > 0], [x for x in nets if x <= 0]
    pct = [r["net_pct"] for r in tr if r.get("net_pct") is not None]
    stops = [r for r in tr if r["exit_reason"] == "Stop"]
    side = sum(r.get("skip_net") or 0 for r in rows)   # leg-risk costs on days without a trade
    final = start + sum(nets) + side
    days = len(rows)
    if first and last:
        days = (datetime.strptime(last, "%Y-%m-%d") - datetime.strptime(first, "%Y-%m-%d")).days + 1
    mean = _avg(pct)
    sd = math.sqrt(sum((x - mean) ** 2 for x in pct) / (len(pct) - 1)) if len(pct) > 1 else None
    aw, al = _avg(wins), _avg(losses)
    worst = min(tr, key=lambda r: r["net"]) if tr else None
    best = max(tr, key=lambda r: r["net"]) if tr else None
    return {
        "days": len(rows), "trades": len(tr), "wins": len(wins), "losses": len(losses), "win_ratio": len(wins) / len(tr) if tr else None,
        "expiry_exits": sum(1 for r in tr if r["exit_reason"] == "Expiry"), "stop_exits": sum(stops_of(r) for r in tr), "skipped": len(rows) - len(tr),
        "premium": sum(r["premium"] for r in tr), "gross": sum(r["gross"] for r in tr), "fees": sum(r["fees"] for r in tr), "net": sum(nets) + side,
        "final_equity": final, "total_return": (final - start) / start if start else None,
        "cagr": (final / start) ** (365 / days) - 1 if tr and days > 0 and final > 0 and start > 0 else None,
        "max_dd": min((r["dd"] for r in rows), default=0), "max_dd_pct": min((r["dd_pct"] for r in rows), default=0),
        "worst": worst["net"] if worst else None, "worst_pct": min(pct) if pct else None, "best": best["net"] if best else None, "best_pct": max(pct) if pct else None,
        "avg_win": aw, "avg_loss": al, "profit_factor": sum(wins) / abs(sum(losses)) if losses and sum(losses) else None,
        "avg_net": _avg(nets), "avg_daily_return": mean, "std": sd, "sharpe": mean / sd * math.sqrt(365) if sd else None,
        "longest_streak": max((r["streak"] for r in rows), default=0), "avg_p": _avg([r["p"] for r in tr]), "avg_lots": _avg([r["lots"] for r in tr]),
        "max_lots": max((r["lots"] for r in tr), default=0), "max_notional": max((r["notional"] for r in tr), default=0),
        "avg_stop_over_p": _avg([r["exit_over_p"] for r in stops]),
        "risk_reward": aw / abs(al) if aw and al else None, "breakeven_win_ratio": abs(al) / (aw + abs(al)) if aw and al else None,
    }


def monthly(rows, start):
    """Capital before, net P&L, capital after, win ratio, stops – per calendar month."""
    out, eq = [], start
    for m in sorted({r["month"] for r in rows}):
        rs = [r for r in rows if r["month"] == m]
        tr = [r for r in rs if r.get("lots")]
        net = sum(r["net"] for r in tr)
        out.append({"month": m, "days": len(rs), "trades": len(tr), "before": eq, "net": net, "after": eq + net,
                    "win_ratio": sum(1 for r in tr if r["net"] > 0) / len(tr) if tr else None, "stops": sum(stops_of(r) for r in tr)})
        eq += net
    return out


def curve(rows):
    """Equity and drawdown % per day for the charts."""
    return [{"t": r["date"], "equity": r["equity"], "dd_pct": r["dd_pct"] * 100} for r in rows]


def recent_win_alert(rows, n=30, margin=0.05):
    """Spec 4: alert when the win ratio of the last n trades is below break-even + 5 points."""
    tr = [r for r in rows if r.get("lots")][-n:]
    if len(tr) < n:
        return None
    wins, losses = [r["net"] for r in tr if r["net"] > 0], [r["net"] for r in tr if r["net"] <= 0]
    if not wins or not losses:
        return None
    aw, al = _avg(wins), abs(_avg(losses))
    be, wr = al / (aw + al), len(wins) / len(tr)
    return {"win_ratio": wr, "breakeven": be, "alert": wr < be + margin}


# ---------------- the owner's workbook ----------------
def parse_workbook(blob):
    """Raw day facts from the owner's Excel backtest: {'mark': [...], 'trade': [...], 'inputs': {...}}. Raises ValueError with a plain reason."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(blob), data_only=True, read_only=True)
    except Exception:
        raise ValueError("This is not an Excel .xlsx file")
    out = {}
    for basis, sheet in (("mark", SHEETS["mark_fixed"]), ("trade", SHEETS["trade_fixed"])):
        if sheet not in wb.sheetnames:
            raise ValueError(f"Sheet {sheet} is missing")
        it = wb[sheet].iter_rows(values_only=True)
        head = None
        for row in it:
            if row and row[0] == "Date":
                head = [str(c).strip() if c is not None else "" for c in row]
                break
        if not head:
            raise ValueError(f"Sheet {sheet}: no header row starting with Date")
        missing = [h for h in RAW if h not in head and h not in ("Settlement price source",)]
        if missing:
            raise ValueError(f"Sheet {sheet}: missing column {missing[0]}")
        idx = {RAW[h]: head.index(h) for h in RAW if h in head}
        days = []
        for row in it:
            if not isinstance(row[0], datetime):
                continue
            d = {k: row[i] for k, i in idx.items()}
            d["date"] = row[0].strftime("%Y-%m-%d")
            for k in ("spot", "strike", "entry_call", "entry_put", "exit_call", "exit_put", "exit_spot", "maker", "taker", "cap"):
                d[k] = float(d[k])
            for k in ("volume", "oi"):
                d[k] = int(d[k]) if d.get(k) is not None else None
            d["exit_time"] = str(d["exit_time"])
            days.append(d)
        if not days:
            raise ValueError(f"Sheet {sheet}: no trade rows")
        out[basis] = days
    inputs = {"starting_capital": 1000.0, "leverage": 25.0, "gst": 0.18, "stop_multiple": 2.0}
    if "Summary" in wb.sheetnames:
        names = {"Starting capital ($)": "starting_capital", "Leverage (× equity → notional per leg)": "leverage", "GST on trading fees": "gst", "Stop multiple (× entry premium)": "stop_multiple"}
        for row in wb["Summary"].iter_rows(values_only=True, max_row=20):
            if row and row[0] in names and isinstance(row[1], (int, float)):
                inputs[names[row[0]]] = float(row[1])
    out["inputs"] = inputs
    return out


def load_seed():
    with gzip.open(SEED_FILE, "rt", encoding="utf-8") as f:
        return json.load(f)


def backtest(data, key):
    """Journal rows of one of the four workbook variants, at the workbook's own inputs."""
    basis, sizing = VARIANTS[key]
    return journal(data[basis], data["inputs"], sizing)


def op_days(raw, fee_rates):
    """Option Picks pick 5 trade facts (history file + the server's own backtest days) -> this module's day facts.
    fee_rates(leg, day) -> (taker, cap) is optpicks.fee_rates (Delta's fee record, or the dated fallback)."""
    from datetime import date as _date
    out = []
    for t in sorted(raw, key=lambda r: str(r["day"])):
        day = t["day"] if isinstance(t["day"], str) else t["day"].isoformat()
        if t.get("skip") or not t.get("legs"):
            out.append({"date": day, "skip_reason": t.get("skip") or "no trade"})
            continue
        legs = {("call" if (l.get("type") or l["sym"][0]) == "C" else "put"): l for l in t["legs"]}
        c, p = legs["call"], legs["put"]
        taker, cap = fee_rates({"taker": p.get("taker"), "cap": p.get("cap")}, _date.fromisoformat(day))
        stopped = [n for n in ("call", "put") if legs[n].get("exit") == "Stop"]
        ref, settle = float(t["price"]), float(t["settle"])
        times = [legs[n].get("stop_ist") for n in stopped if legs[n].get("stop_ist")]
        out.append({"date": day, "spot": ref, "ref_price": ref, "strike": float(p["k"]), "call": c["sym"], "put": p["sym"],
                    "entry_call": float(c["fill"]), "entry_put": float(p["fill"]), "exit_call": float(c["exit_price"]), "exit_put": float(p["exit_price"]),
                    "exit_reason": "Stop" if len(stopped) == 2 else "Stop+Expiry" if stopped else "Expiry",
                    "exit_time": max(times) if len(stopped) == 2 and times else "17:30", "exit_spot": settle,
                    "exit_spot_call": ref if "call" in stopped else settle, "exit_spot_put": ref if "put" in stopped else settle,
                    "maker": taker, "taker": taker, "cap": cap, "stop_call": c.get("stop"), "stop_put": p.get("stop"),
                    "call_exit": c.get("exit"), "call_stop_time": c.get("stop_ist"), "put_exit": p.get("exit"), "put_stop_time": p.get("stop_ist"),
                    "stopped_legs": len(stopped), "real": bool(t.get("real")), "settle_source": "Delta settlement index"})
    return out
