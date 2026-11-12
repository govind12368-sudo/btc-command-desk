"""Trade planner: daily-candle context, cost-adjusted sizing, guardrails, scenarios, tranches, factor-flip sensitivity."""
from core import finite, tick_ceil, tick_floor
from indicators import frame_metrics, rsi_wilder
from strategy import cost_model, size_qty, sided_ok
import models_calc as MC


def plan(req, s, state, daily_bars, prod):
    d = req.get("direction", "LONG")
    sign = 1 if d == "LONG" else -1
    m = frame_metrics(daily_bars, 86400)
    rsi = rsi_wilder([b["c"] for b in daily_bars])
    q = state.get("quote") or {}
    tick, step = (prod or {}).get("tick_size"), (prod or {}).get("contract_value")
    out = {"daily": {k: m.get(k) for k in ("close", "ema20", "ema50", "atr14", "bars", "bar_close", "valid", "issues")} | {"rsi14": rsi}}
    if not (tick and step and m.get("valid") and q.get("bid")):
        out["error"] = "planner needs valid daily candles, live quote and contract specification"
        return out
    atr = m["atr14"]
    if req.get("entry_mode") == "limit" and finite(req.get("limit_price")) and req["limit_price"] > 0:
        entry = req["limit_price"]
    else:
        entry = q["ask"] if d == "LONG" else q["bid"]
    entry = tick_ceil(entry, tick) if d == "LONG" else tick_floor(entry, tick)
    sf, tr = req.get("stop_fraction") or s["swing_stop"], req.get("target_r") or s["swing_target_r"]

    def levels(stop_dist, r):
        stop = tick_floor(entry - stop_dist, tick) if d == "LONG" else tick_ceil(entry + stop_dist, tick)
        tgt = tick_floor(entry + abs(entry - stop) * r, tick) if d == "LONG" else tick_ceil(entry - abs(entry - stop) * r, tick)
        cm = cost_model(d, entry, stop, tgt, s)
        qty, lim = size_qty(entry, cm["risk_per_btc"], s, step)
        if s.get("conviction_scaling") and state.get("live_direction", {}).get("score") is not None:
            qty = tick_floor(qty * max(0.25, min(1, abs(state["live_direction"]["score"]))), step)
        return {"stop": stop, "target": tgt, "qty_btc": qty, "notional": qty * entry, **cm, "risk_usd": qty * cm["risk_per_btc"],
                "reward_usd": qty * cm["reward_per_btc"], "sided_ok": sided_ok(d, entry, stop, tgt)}

    base = levels(entry * sf, tr)
    wr = s["planner_win_rate"]
    base["assumed_expectancy_r"] = wr * base["net_r"] - (1 - wr) if base["net_r"] else None
    spread = (q["ask"] - q["bid"]) / ((q["ask"] + q["bid"]) / 2) * 10000
    lim = state.get("limits", {})
    g = [("Daily candles valid (≥51 completed)", m["valid"]), ("Live two-sided quote", q["ask"] > q["bid"]),
         (f"Spread ≤ {s['max_spread_bps']} bps", spread <= s["max_spread_bps"]), ("Net R > 1 after costs", (base["net_r"] or 0) > 1),
         ("Positive rounded quantity", base["qty_btc"] > 0), (f"Notional ≤ ${s['max_order_notional']:,.0f}", base["notional"] <= s["max_order_notional"] + 1e-6),
         (f"Effective leverage ≤ {s['leverage_cap']}×", base["notional"] / s["capital"] <= s["leverage_cap"]),
         (f"Risk ≤ {s['risk_fraction']} of capital", base["risk_usd"] <= s["capital"] * s["risk_fraction"] + 1e-6),
         ("Daily limits/capacity clear", not lim.get("issues"))]
    out["guardrails"] = [{"name": n, "pass": bool(v)} for n, v in g]
    out["scenarios"] = []
    for mult in (1.0, 1.5, 2.0):
        for r in (1.5, 2.0, 3.0):
            L = levels(atr * mult, r)
            out["scenarios"].append({"stop_atr": mult, "target_r": r, **{k: L[k] for k in ("stop", "target", "qty_btc", "net_r", "risk_usd", "reward_usd", "breakeven_fraction")},
                                     "assumed_expectancy_r": wr * L["net_r"] - (1 - wr) if L["net_r"] else None})
    tr_list, tot_q, tot_c = [], 0, 0
    for frac, off in zip(s["tranche_fractions"], s["atr_offsets"]):
        px = entry + sign * off * atr
        px = tick_ceil(px, tick) if d == "LONG" else tick_floor(px, tick)
        qq = tick_floor(base["qty_btc"] * frac, step)
        tr_list.append({"fraction": frac, "atr_offset": off, "price": px, "qty_btc": qq})
        tot_q += qq; tot_c += qq * px
    out["tranches"] = {"rows": tr_list, "avg_entry": tot_c / tot_q if tot_q else None, "total_qty": tot_q,
                       "note": "Offsets are applied direction-aware: negative = better price (lower for LONG, higher for SHORT)."}
    pil = state.get("pillars") or {}
    flips = []
    valid = {k: v for k, v in pil.items() if v.get("valid")}
    for k, v in valid.items():
        if v["score"] == 0:
            continue
        alt = {kk: dict(vv) for kk, vv in pil.items()}
        alt[k]["score"] = -v["score"]
        ld = MC.live_direction(alt)
        flips.append({"factor": k, "from": v["score"], "to": -v["score"], "new_score": ld.get("score"), "new_direction": ld.get("direction")})
    out["factor_flip"] = {"base": state.get("live_direction", {}).get("score"), "base_direction": state.get("live_direction", {}).get("direction"), "rows": flips}
    out.update(direction=d, entry=entry, entry_mode=req.get("entry_mode", "spot"), base=base, atr=atr, spread_bps=spread,
               hold_hours=s["planner_hold_hours"], win_rate_assumed=wr,
               note="Assumed expectancy uses the configured win-rate assumption — it is not observed performance.")
    return out
