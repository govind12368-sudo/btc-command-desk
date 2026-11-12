"""Strategy engine: 11 active checks; F31 review/journal is excluded by policy."""
import math
from core import finite, tick_ceil, tick_floor, age_sec, parse_iso, now_utc, iso
from indicators import trend_dir

CFG = {
    "scalp": {"lower": "5m", "higher": "15m", "res": (300, 900), "stop": "scalp_stop", "target": "scalp_target_r"},
    "swing": {"lower": "1h", "higher": "4h", "res": (3600, 14400), "stop": "swing_stop", "target": "swing_target_r"},
}
MANDATORY = ["F26", "F27", "F28", "F29", "F30", "F32", "F34", "F35", "F37"]
ACTIVE_CHECK_IDS = ["F26", "F27", "F28", "F29", "F30", "F32", "F33", "F34", "F35", "F36", "F37"]
NAMES = {"F26": "Retrieval/candle age", "F27": "Feed/OHLC quality", "F28": "Trend alignment",
         "F29": "Momentum/closed-bar confirmation", "F30": "Spread", "F31": "Current review and journal",
         "F32": "Risk/cost inputs", "F33": "Daily limits/capacity", "F34": "Venue/contract validity",
         "F35": "Net payoff", "F36": "Anti-chase distance", "F37": "Size/level integrity"}


def exec_legs(s, strat, entry_mode=None):
    """Per-leg cost fractions (fee incl. GST, plus slippage on taker legs).
    Scalp: post-only limit entry (maker) by default, take-profit as a resting limit (maker), stop as stop-market (taker).
    Swing and market mode: every leg taker."""
    taker = s["fee_taker_per_side"] + s["slippage_bps"] / 10000
    maker = s["fee_maker_per_side"]
    limit_entry = strat == "scalp" and (s["scalp_entry_mode"] if entry_mode is None else entry_mode) >= 0.5
    limit_target = strat == "scalp" and limit_entry and s["scalp_target_exit_limit"] >= 0.5
    return {"entry": maker if limit_entry else taker, "stop": taker, "target": maker if limit_target else taker,
            "entry_type": "POST-ONLY LIMIT" if limit_entry else "MARKET",
            "target_type": "LIMIT" if limit_target else "MARKET", "stop_type": "STOP-MARKET"}


def cost_model(direction, entry, stop, target, s, fee=None, slip=None, fund=None, legs=None):
    fund = s["funding_bps"] if fund is None else fund
    if legs:
        fe, fs, ft = legs["entry"], legs["stop"], legs["target"]
        risk = abs(entry - stop) + entry * fe + stop * fs + entry * fund / 10000
        reward = abs(target - entry) - entry * fe - target * ft - entry * fund / 10000
        return {"c": fs, "legs": legs, "risk_per_btc": risk, "reward_per_btc": reward,
                "net_r": reward / risk if risk > 0 else None,
                "breakeven_fraction": risk / (risk + reward) if (risk + reward) > 0 else None}
    fee = s["fee_per_side"] if fee is None else fee
    slip = s["slippage_bps"] if slip is None else slip
    c = fee + slip / 10000
    risk = abs(entry - stop) + (entry + stop) * c + entry * fund / 10000
    reward = abs(target - entry) - (entry + target) * c - entry * fund / 10000
    return {"c": c, "risk_per_btc": risk, "reward_per_btc": reward,
            "net_r": reward / risk if risk > 0 else None,
            "breakeven_fraction": risk / (risk + reward) if (risk + reward) > 0 else None}


def size_qty(entry, risk_per_btc, s, step):
    if not (finite(entry, risk_per_btc, step) and entry > 0 and risk_per_btc > 0 and step > 0):
        return None, {}
    lim = {"by_max_notional": s["max_order_notional"] / entry,
           "by_risk": s["capital"] * s["risk_fraction"] / risk_per_btc,
           "by_leverage": s["capital"] * s["leverage_cap"] / entry}
    q = tick_floor(min(lim.values()), step)
    return q, lim


def build_levels(direction, entry, s, strat, tick):
    sf, tr = s[CFG[strat]["stop"]], s[CFG[strat]["target"]]
    if direction == "LONG":
        stop = tick_floor(entry * (1 - sf), tick)
        target = tick_floor(entry + (entry - stop) * tr, tick)
    else:
        stop = tick_ceil(entry * (1 + sf), tick)
        target = tick_ceil(entry - (stop - entry) * tr, tick)
    return stop, target


def sided_ok(direction, entry, stop, target):
    if not finite(entry, stop, target) or min(entry, stop, target) <= 0:
        return False
    return (stop < entry < target) if direction == "LONG" else (target < entry < stop)


def scalp_direction(m, htrend, s):
    """Base scalp setup on one completed 5m bar (F28 trend + F29 bar checks, without confirmation).
    Shared by the live engine, the confirmation history and the backtest so they never drift apart."""
    c, e20, e50, p = m["close"], m["ema20"], m["ema50"], m["ema20_prev"]
    atr, vw, vr = m["atr14"], m["vwap20"], m["volume_ratio"]
    if not finite(c, e20, e50, p, atr, vw, vr, m.get("high"), m.get("low")):
        return None
    tol = s["close_extreme_atr"] * atr
    if c > e20 > e50 and e20 >= p and htrend != "DOWN" and c > vw and c >= m["high"] - tol and vr >= s["min_scalp_volume_ratio"]:
        return "LONG"
    if c < e20 < e50 and e20 <= p and htrend != "UP" and c < vw and c <= m["low"] + tol and vr >= s["min_scalp_volume_ratio"]:
        return "SHORT"
    return None


def ext_atr(direction, m):
    """How far the close already is beyond EMA20 in the trade direction, in ATRs."""
    d = m["close"] - m["ema20"] if direction == "LONG" else m["ema20"] - m["close"]
    return d / m["atr14"] if finite(d, m.get("atr14")) and m["atr14"] > 0 else None


def chk(cid, ok, detail, status=None):
    return {"id": cid, "name": NAMES[cid], "status": status or ("PASS" if ok else "FAIL"), "detail": detail}


def checked_age(value):
    try:
        age = age_sec(value)
        return age if finite(age) else None
    except (TypeError, ValueError, OverflowError):
        return None


def candidate_levels(direction, strat, lo, q, prod, s):
    """Derived scenario using this venue's validated quotes and contract units."""
    tick, step, c = prod["tick_size"], prod["contract_value"], lo["close"]
    legs, extra = None, {}
    if strat == "scalp":
        legs = exec_legs(s, "scalp")
        if legs["entry_type"] == "POST-ONLY LIMIT":
            off = s["scalp_limit_offset_atr"] * lo["atr14"]
            entry = tick_floor(c - off, tick) if direction == "LONG" else tick_ceil(c + off, tick)
            wait = int(s["scalp_limit_wait_bars"])
            extra = {"limit_wait_bars": wait, "valid_until": (lo["bar_close"] + wait * 300) if finite(lo.get("bar_close")) else None}
        else:
            entry = tick_ceil(max(q["ask"], c + tick), tick) if direction == "LONG" else tick_floor(min(q["bid"], c - tick), tick)
        extra.update(entry_type=legs["entry_type"], target_type=legs["target_type"], stop_type=legs["stop_type"], tick=tick)
    else:
        entry = tick_ceil(max(c, lo["prior20_high"] + tick), tick) if direction == "LONG" else tick_floor(min(c, lo["prior20_low"] - tick), tick)
        extra = {"entry_type": "MARKET", "target_type": "MARKET", "stop_type": "STOP-MARKET"}
    stop, target = build_levels(direction, entry, s, strat, tick)
    cm = cost_model(direction, entry, stop, target, s, legs=legs)
    qty, sz = size_qty(entry, cm["risk_per_btc"], s, step)
    return {"direction": direction, "entry": entry, "stop": stop, "target": target, "qty_btc": qty,
            "contracts": round(qty / step) if qty else 0, "notional_usd": (qty or 0) * entry, **cm, "size_limits": sz,
            "risk_usd": (qty or 0) * cm["risk_per_btc"], "reward_usd": (qty or 0) * cm["reward_per_btc"],
            "atr": lo["atr14"], "close": c, "entry_live": None,
            "quote_time": q.get("event_time"), "quote_source": q.get("source"), "bar_close": lo.get("bar_close"),
            "note": "Calculated scenario; a qualified direction and every active check are still required.", **extra}


def validate_settings(s):
    errs = []
    pos = ["capital", "risk_fraction", "max_order_notional", "leverage_cap", "scalp_stop", "swing_stop",
           "scalp_target_r", "swing_target_r", "max_spread_bps", "daily_loss_limit", "max_entries_per_day", "max_positions", "journal_capacity",
           "scalp_min_net_r", "swing_min_net_r", "scalp_confirm_lookback_min", "scalp_max_ext_atr", "scalp_limit_wait_bars"]
    for k in pos:
        if not finite(s.get(k)) or s[k] <= 0:
            errs.append(f"{k} must be a positive number")
    for k in ["fee_per_side", "fee_maker_per_side", "fee_taker_per_side", "slippage_bps", "funding_bps", "scalp_confirm_signals",
              "max_open_model_signals", "scalp_entry_mode", "scalp_limit_offset_atr", "scalp_target_exit_limit"]:
        if not finite(s.get(k)) or s[k] < 0:
            errs.append(f"{k} must be zero or positive")
    if finite(s.get("risk_fraction")) and s["risk_fraction"] > 0.05:
        errs.append("risk_fraction above 0.05 is outside allowed range")
    if finite(s.get("scalp_stop")) and s["scalp_stop"] >= 0.5 or finite(s.get("swing_stop")) and s["swing_stop"] >= 0.5:
        errs.append("stop fraction must be below 0.5")
    return errs


def evaluate(strat, ctx, s, plan=None):
    cfg = CFG[strat]
    lo, hi = ctx["frames"].get(cfg["lower"], {}), ctx["frames"].get(cfg["higher"], {})
    q, prod = ctx.get("quote") or {}, ctx.get("product") or {}
    tick, step = prod.get("tick_size"), prod.get("contract_value")
    maxage = s["max_quote_age_sec"]
    checks = {}

    # F26
    issues = []
    ra = checked_age(ctx.get("candles_retrieved"))
    qa = checked_age(q.get("event_time"))
    if ra is not None and ra < -5:
        issues.append(f"candle retrieval time is {round(-ra)}s in the future")
    elif ra is None or ra > maxage:
        issues.append(f"candle retrieval age {ra and round(ra)}s exceeds {maxage}s" if ra is not None else "candles never retrieved")
    if qa is None:
        issues.append("no quote timestamp")
    elif qa < -5:
        issues.append(f"quote time is {round(-qa)}s in the future")
    elif qa > maxage:
        issues.append(f"quote age {round(qa)}s exceeds {maxage}s")
    for m, res, tf in ((lo, cfg["res"][0], cfg["lower"]), (hi, cfg["res"][1], cfg["higher"])):
        if not finite(m.get("bar_close")):
            issues.append(f"{tf}: missing or non-finite completed-candle timestamp")
            continue
        a = now_utc().timestamp() - m["bar_close"]
        if a < -5:
            issues.append(f"{tf}: candle close is in the future")
        elif a > res + maxage:
            issues.append(f"{tf}: last completed candle closed {round(a)}s ago (> {res + maxage}s)")
    checks["F26"] = chk("F26", not issues, "; ".join(issues) or f"quote age {round(qa)}s, candle retrieval {round(ra)}s, both frames current")

    # F27
    issues = []
    for m, tf in ((lo, cfg["lower"]), (hi, cfg["higher"])):
        if not m.get("valid"):
            issues.append(f"{tf}: " + ("; ".join(m.get("issues", [])) or "unavailable"))
        required = ("close", "ema20", "ema50", "ema20_prev", "atr14", "vwap20", "volume_ratio", "prior20_high", "prior20_low", "bar_open", "bar_close", "high", "low", "bars")
        if not all(finite(m.get(key)) for key in required):
            issues.append(f"{tf}: missing or non-finite indicator/candle fields")
        elif m["atr14"] <= 0 or m["bars"] < 51 or min(m["close"], m["ema20"], m["ema50"], m["high"], m["low"]) <= 0 or m["low"] > m["high"]:
            issues.append(f"{tf}: invalid price range, ATR or indicator history")
    two_sided = finite(q.get("bid"), q.get("ask")) and q["bid"] > 0 and q["ask"] > q["bid"]
    if not two_sided:
        issues.append("no valid two-sided quote")
    checks["F27"] = chk("F27", not issues, "; ".join(issues) or f"{lo['bars']} {cfg['lower']} and {hi['bars']} {cfg['higher']} completed bars, indicators finite, ATR>0, bid<ask")
    data_ok = not issues

    direction, htrend, ltrend = None, trend_dir(hi) if data_ok else None, None
    conf = {}
    if data_ok:
        c, e20, e50, p = lo["close"], lo["ema20"], lo["ema50"], lo["ema20_prev"]
        if strat == "scalp":
            if c > e20 > e50 and e20 >= p and htrend != "DOWN":
                direction = "LONG"
            elif c < e20 < e50 and e20 <= p and htrend != "UP":
                direction = "SHORT"
        else:
            if c > e20 > e50 and e20 > p and htrend == "UP":
                direction = "LONG"
            elif c < e20 < e50 and e20 < p and htrend == "DOWN":
                direction = "SHORT"
        ltrend = trend_dir(lo)

    # F28
    if plan:
        direction = plan["direction"]
        checks["F28"] = chk("F28", True, f"saved Swing plan {plan['plan_id']} {direction} takes precedence over rolling candidate")
    elif not data_ok:
        checks["F28"] = chk("F28", False, "inputs unavailable (see F27)", "UNAVAILABLE")
    else:
        checks["F28"] = chk("F28", direction is not None, f"lower close {lo['close']:.1f}, EMA20 {lo['ema20']:.1f}, EMA50 {lo['ema50']:.1f}, prev EMA20 {lo['ema20_prev']:.1f}; higher trend {htrend}" + (f" → {direction}" if direction else " → no qualified direction"))

    # F29
    if plan:
        checks["F29"] = chk("F29", True, f"saved confirmation bar {iso(plan['confirm_bar_open'])} (close {plan['confirm_close']}, breakout ref {plan['breakout_ref']})")
    elif not data_ok or not direction:
        checks["F29"] = chk("F29", False, "requires valid data and a direction", "UNAVAILABLE" if not data_ok else "FAIL")
    else:
        c, atr, vw, vr = lo["close"], lo["atr14"], lo["vwap20"], lo["volume_ratio"]
        tf = CFG[strat]["lower"]
        # Each part: (rule text, passed, plain-English condition with the price/level that would satisfy it)
        if strat == "scalp":
            tol = s["close_extreme_atr"] * atr
            vmin = s["min_scalp_volume_ratio"]
            if direction == "LONG":
                parts = [("close > VWAP20", c > vw, f"{tf} candle closes above VWAP {vw:,.0f} (last close {c:,.0f})"),
                         (f"close ≥ high − {s['close_extreme_atr']}×ATR", c >= lo["high"] - tol, f"{tf} candle closes near its high (at or above {lo['high'] - tol:,.0f} on the last bar)"),
                         (f"vol ratio ≥ {vmin}", vr >= vmin, f"{tf} volume at least {vmin}× average (now {vr:.2f}×)")]
            else:
                parts = [("close < VWAP20", c < vw, f"{tf} candle closes below VWAP {vw:,.0f} (last close {c:,.0f})"),
                         (f"close ≤ low + {s['close_extreme_atr']}×ATR", c <= lo["low"] + tol, f"{tf} candle closes near its low (at or below {lo['low'] + tol:,.0f} on the last bar)"),
                         (f"vol ratio ≥ {vmin}", vr >= vmin, f"{tf} volume at least {vmin}× average (now {vr:.2f}×)")]
            need = int(s["scalp_confirm_signals"])
            if need > 0:
                hist = ctx.get("scalp_history")
                look = int(s["scalp_confirm_lookback_min"])
                if hist and hist.get("lookback_min") == look:
                    n = hist.get(direction, 0)
                    parts.append((f"{n} earlier {direction} setup(s) in last {look} min ≥ {need}", n >= need, f"at least {need} earlier {direction} setups in the last {look} min (now {n})"))
                else:
                    parts.append((f"earlier-setup confirmation history unavailable (need {need} in {look} min)", False, "earlier-setup history is still loading"))
        else:
            vmin = s["swing_volume_ratio"]
            if direction == "LONG":
                parts = [("close > prior-20 high", c > lo["prior20_high"], f"{tf} candle closes above the 20-bar high {lo['prior20_high']:,.0f} (last close {c:,.0f})"),
                         ("close > VWAP20", c > vw, f"{tf} candle closes above VWAP {vw:,.0f}"),
                         (f"vol ratio ≥ {vmin}", vr >= vmin, f"{tf} volume at least {vmin}× average (now {vr:.2f}×)")]
            else:
                parts = [("close < prior-20 low", c < lo["prior20_low"], f"{tf} candle closes below the 20-bar low {lo['prior20_low']:,.0f} (last close {c:,.0f})"),
                         ("close < VWAP20", c < vw, f"{tf} candle closes below VWAP {vw:,.0f}"),
                         (f"vol ratio ≥ {vmin}", vr >= vmin, f"{tf} volume at least {vmin}× average (now {vr:.2f}×)")]
        checks["F29"] = chk("F29", all(p[1] for p in parts), ", ".join(f"{k}: {'yes' if v else 'no'}" for k, v, _ in parts) + f" (close {c:.1f}, VWAP20 {vw:.1f}, ATR14 {atr:.1f}, vol ratio {vr:.2f})")
        checks["F29"]["conditions"] = [{"rule": k, "ok": bool(v), "plain": t} for k, v, t in parts]

    # F30
    if two_sided:
        mid = (q["bid"] + q["ask"]) / 2
        spread = (q["ask"] - q["bid"]) / mid * 10000
        checks["F30"] = chk("F30", spread <= s["max_spread_bps"], f"spread {spread:.3f} bps vs max {s['max_spread_bps']} bps (bid {q['bid']}, ask {q['ask']}, {q.get('source')})")
    else:
        spread = None
        checks["F30"] = chk("F30", False, "no valid two-sided quote", "UNAVAILABLE")

    # F31 is excluded from both plans. Reviews and journal records are preserved.

    # F32
    serr = validate_settings(s)
    checks["F32"] = chk("F32", not serr, "; ".join(serr) or "capital, risk, leverage, order limit, stop, target ratio, costs, spread and daily-loss controls valid")

    # F33
    lim = ctx["limits"]
    l_issues = list(lim["issues"])
    if serr:
        l_issues.insert(0, "F32 must pass")
    checks["F33"] = chk("F33", not l_issues, "; ".join(l_issues) or lim["detail"])

    # F34
    p_issues = []
    if not prod:
        p_issues.append("contract specification unavailable")
    else:
        if prod.get("symbol") != "BTCUSD": p_issues.append("symbol is not BTCUSD")
        if prod.get("state") != "live": p_issues.append(f"state {prod.get('state')}")
        if prod.get("contract_type") != "perpetual_futures": p_issues.append(f"contract type {prod.get('contract_type')}")
        if prod.get("underlying") not in ("BTC", "XBT"): p_issues.append("underlying is not BTC/XBT")
        if prod.get("contract_unit_currency") not in ("BTC", "XBT"): p_issues.append("contract not BTC-denominated")
        if not (finite(tick) and tick > 0): p_issues.append("tick size not positive")
        if not (finite(step) and step > 0): p_issues.append("quantity increment not positive")
        product_age = checked_age(ctx.get("product_retrieved"))
        if product_age is None or product_age > 7200: p_issues.append("contract spec missing timestamp or older than 2h")
        elif product_age < -5: p_issues.append("contract specification retrieval is in the future")
    checks["F34"] = chk("F34", not p_issues, "; ".join(p_issues) or f"live BTCUSD perpetual, tick {tick} USD, 1 contract = {step} BTC, {prod.get('quoting')}-quoted")

    # Levels
    lv = None
    if direction and data_ok and not p_issues:
        if plan:
            entry_live = q["ask"] if direction == "LONG" else q["bid"]
            entry, stop, target = plan["entry"], plan["stop"], plan["target"]
            fee = max(s["fee_per_side"], plan["costs"]["fee_per_side"]); slip = max(s["slippage_bps"], plan["costs"]["slippage_bps"]); fund = max(s["funding_bps"], plan["costs"]["funding_bps"])
            cm = cost_model(direction, entry, stop, target, s, fee, slip, fund)
            qty, sz = size_qty(entry, cm["risk_per_btc"], s, step)
            qty = min(qty or 0, plan["qty_btc"])
            lv = {"direction": direction, "entry": entry, "stop": stop, "target": target, "qty_btc": qty,
              "contracts": round(qty / step) if qty else 0, "notional_usd": (qty or 0) * entry, **cm, "size_limits": sz,
              "risk_usd": (qty or 0) * cm["risk_per_btc"], "reward_usd": (qty or 0) * cm["reward_per_btc"],
              "atr": plan["atr"] if plan else lo["atr14"], "close": lo["close"], "entry_live": entry_live}
        else:
            lv = candidate_levels(direction, strat, lo, q, prod, s)

    prerequisites = [f"{cid} {checks[cid]['name']}: {checks[cid]['detail']}" for cid in ("F26", "F27", "F28", "F32", "F34") if checks[cid]["status"] != "PASS"]
    unavailable_reason = "Waiting on " + "; ".join(prerequisites) if prerequisites else "level calculation did not produce valid values"
    scenarios = {}
    if not direction and data_ok and not p_issues and not serr and checks["F26"]["status"] == "PASS":
        scenarios = {side: candidate_levels(side, strat, lo, q, prod, s) for side in ("LONG", "SHORT")}

    # F35
    min_r = s[f"{strat}_min_net_r"]
    if lv and finite(lv["net_r"]):
        costs = (f"entry {lv['legs']['entry']:.5f} {lv['legs']['entry_type']}, target {lv['legs']['target']:.5f} {lv['legs']['target_type']}, stop {lv['legs']['stop']:.5f} STOP-MARKET"
                 if lv.get("legs") else f"cost c={lv['c']:.5f}/side")
        checks["F35"] = chk("F35", lv["net_r"] > min_r, f"net R {lv['net_r']:.3f} vs min {min_r} (reward/BTC {lv['reward_per_btc']:.2f}, risk/BTC {lv['risk_per_btc']:.2f}, {costs})")
    else:
        detail = "; ".join(f"{side} scenario net R {v['net_r']:.3f}" for side, v in scenarios.items()) + "; F28 has no qualified direction" if scenarios else unavailable_reason
        checks["F35"] = chk("F35", False, detail, "WAITING SETUP" if scenarios else "UNAVAILABLE")

    # F36
    if lv:
        if plan:
            d = abs(lv["entry_live"] - plan["entry"])
            checks["F36"] = chk("F36", d <= plan["atr"], f"live entry {lv['entry_live']} is {d:.1f} from saved entry {plan['entry']} (saved ATR {plan['atr']:.1f})")
        else:
            d = abs(lv["entry"] - lo["close"])
            ok, detail = d <= lo["atr14"], f"entry {lv['entry']} is {d:.1f} from completed close {lo['close']} (ATR14 {lo['atr14']:.1f})"
            if strat == "scalp":
                x, mx = ext_atr(direction, lo), s["scalp_max_ext_atr"]
                ok = ok and x is not None and x <= mx
                detail += f"; close {x:.2f}×ATR beyond EMA20 vs max {mx}" if x is not None else "; EMA20 extension unavailable"
            checks["F36"] = chk("F36", ok, detail)
    else:
        detail = "; ".join(f"{side} scenario distance {abs(v['entry'] - lo['close']):.1f} USD / ATR14 {lo['atr14']:.1f}" for side, v in scenarios.items()) + "; F28 has no qualified direction" if scenarios else unavailable_reason
        checks["F36"] = chk("F36", False, detail, "WAITING SETUP" if scenarios else "UNAVAILABLE")

    # F37
    if lv:
        okq = finite(lv["qty_btc"]) and lv["qty_btc"] > 0
        oks = sided_ok(direction, lv["entry"], lv["stop"], lv["target"])
        okr = finite(lv["reward_per_btc"]) and lv["reward_per_btc"] > 0
        checks["F37"] = chk("F37", okq and oks and okr, f"qty {lv['qty_btc']} BTC ({lv['contracts']} contracts, notional ${lv['notional_usd']:.0f}); stop/target sided {'correctly' if oks else 'INCORRECTLY'}; net reward/BTC {lv['reward_per_btc']:.2f}")
    else:
        detail = "; ".join(f"{side} scenario {v['qty_btc']} BTC ({v['contracts']} contracts), entry {v['entry']}, stop {v['stop']}, target {v['target']}" for side, v in scenarios.items()) + "; F28 has no qualified direction" if scenarios else unavailable_reason
        checks["F37"] = chk("F37", False, detail, "WAITING SETUP" if scenarios else "UNAVAILABLE")

    order = ACTIVE_CHECK_IDS
    lst = [checks[k] for k in order]
    passed = sum(1 for c in lst if c["status"] == "PASS")
    mand_ok = all(checks[k]["status"] == "PASS" for k in MANDATORY)
    if ctx.get("open_position"):
        status = "MONITOR"
    elif passed == len(order) and direction in ("LONG", "SHORT"):
        status = "BUY" if direction == "LONG" else "SELL"
    elif mand_ok:
        status = "READY TO CAPTURE"
    elif direction and passed >= s["min_candidate_checks"]:
        status = "PLAN ONLY"
    else:
        status = "WAIT"
    failing = [c["id"] for c in lst if c["status"] != "PASS"]
    return {"strategy": strat, "status": status, "direction": direction, "higher_trend": htrend, "lower_trend": ltrend,
            "checks": lst, "passed": passed, "total_checks": len(order), "mandatory_pass": mand_ok, "failing": failing, "levels": lv,
            "scenario_levels": scenarios, "excluded_checks": [{"id": "F31", "name": NAMES["F31"], "reason": "Excluded from swing/scalp eligibility by owner policy; journal data retained."}],
            "frames": {cfg["lower"]: _slim(lo), cfg["higher"]: _slim(hi)}, "confirmation": conf,
            "spread_bps": spread, "plan_id": plan["plan_id"] if plan else None}


def _slim(m):
    return {k: v for k, v in m.items() if k != "raw"}
