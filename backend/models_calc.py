"""Five pillars, live direction, original four/six-factor models, macro publication policy, flow observation, readiness."""
from datetime import date, datetime, timedelta, timezone
from core import now_utc, iso, parse_iso, age_sec, finite
from indicators import frame_metrics, trend_dir
from direction_state import technical_grace

PILLAR_W = {"money_flow": 0.20, "liquidity": 0.15, "positioning": 0.15, "macro": 0.20, "technical": 0.20}
FOUR_W = {"macro": 0.25, "etf": 0.25, "funding_all": 0.30, "fear_greed": 0.20}
SIX_W = {"money_flow": 0.20, "liquidity": 0.15, "positioning": 0.15, "macro": 0.20, "technical": 0.20, "four_factor": 0.10}


# ETF flows only exist for US trading days and are published after the US close, often a day or
# two late by the compiler. Age is counted in weekdays, so a weekend plus one day of provider lag
# does not knock the six-factor model offline. US market holidays are not excluded (stricter).
ETF_MAX_TRADING_DAYS_OLD = 3


def us_trading_days_since(d, today):
    n, x = 0, d
    while x < today:
        x += timedelta(days=1)
        if x.weekday() < 5:
            n += 1
    return n


def strength(a):
    a = abs(a)
    return "VERY STRONG" if a >= 0.70 else "STRONG" if a >= 0.45 else "MODERATE" if a >= 0.15 else "LOW"


def sgn3(x, up, dn):
    return 1 if x >= up else -1 if x <= dn else 0


def _obs_dt(d):
    return datetime.fromisoformat(d).replace(tzinfo=timezone.utc)


def _next_business(d):
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


# ---------- macro publication-aware freshness ----------
def macro_series_status(key, num_src, meta_src, s):
    caps = {"usd": ("usd_cap_h", 264, 10), "y2": ("y2_cap_h", 120, 5), "brent": ("brent_cap_h", 240, 10)}
    ck, hard, lag_days = caps[key]
    cap_h = min(hard, s.get(ck, hard))
    out = {"series": key, "mode": s["macro_mode"], "issues": [], "valid": False}
    nd = num_src.data if num_src else None
    md = meta_src.data if meta_src else None
    if not nd or not nd.get("obs"):
        out["issues"].append(f"numeric source unavailable: {num_src.error if num_src else 'not configured'}")
        return out
    obs = nd["obs"]
    last = obs[-1]
    out.update(latest_date=last["date"], latest_value=last["value"], unit=nd.get("unit"), series_name=nd.get("series"),
               numeric_retrieved=iso(num_src.last_success))
    obs_age_h = (now_utc() - _obs_dt(last["date"])).total_seconds() / 3600
    out["observation_age_h"] = round(obs_age_h, 1)
    if _obs_dt(last["date"]).date() > now_utc().date():
        out["issues"].append("future observation date")
    if s["macro_mode"] == "strict":
        if obs_age_h > s["strict_macro_age_days"] * 24:
            out["issues"].append(f"strict mode: observation {obs_age_h:.0f}h old > {s['strict_macro_age_days']} days")
        if num_src.health() != "ok":
            out["issues"].append(f"numeric retrieval {num_src.health()}")
        out["valid"] = not out["issues"]
        return out
    mx = s["macro_meta_max_age_min"] * 60
    if num_src.health() != "ok" or age_sec(num_src.last_success) > mx:
        out["issues"].append(f"numeric retrieval not healthy within {s['macro_meta_max_age_min']} min")
    if not md or meta_src.health() != "ok" or age_sec(meta_src.last_success) > mx:
        out["issues"].append(f"publication metadata retrieval not healthy: {meta_src.error or meta_src.health()}" if meta_src else "metadata adapter missing")
    else:
        rel = date.fromisoformat(md["release_date"])
        out.update(release_date=md["release_date"], schedule=md["schedule"], metadata_retrieved=iso(meta_src.last_success))
        if rel > now_utc().date():
            out["issues"].append("future release date")
        lo = date.fromisoformat(last["date"])
        if not (rel - timedelta(days=lag_days) <= lo <= rel):
            out["issues"].append(f"latest observation {lo} does not match release {rel} metadata")
        if key == "usd":
            nxt = rel + timedelta(days=7)
            out["next_publication_inferred"] = True
        elif key == "y2":
            nxt = _next_business(rel)
            out["next_publication_inferred"] = True
        else:
            nxt = date.fromisoformat(md["next_release_date"]) if md.get("next_release_date") else rel + timedelta(days=7)
            out["next_publication_inferred"] = not md.get("next_release_date")
        deadline = datetime.combine(nxt + timedelta(days=1), datetime.min.time(), timezone.utc)
        out["next_publication"] = nxt.isoformat()
        out["deadline"] = iso(deadline)
        if now_utc() >= deadline:
            out["issues"].append(f"next publication deadline {nxt} (+1 day grace{', inferred' if out['next_publication_inferred'] else ''}) passed without a new release; holidays are not assumed")
    if obs_age_h > cap_h:
        out["issues"].append(f"observation age {obs_age_h:.0f}h exceeds cap {cap_h}h")
    out["cap_h"] = cap_h
    out["valid"] = not out["issues"]
    return out


def macro_block(R, s):
    usd = macro_series_status("usd", R["fed_h10"], R["fed_h10_meta"], s)
    y2 = macro_series_status("y2", R["fed_h15"], R["fed_h15_meta"], s)
    br = macro_series_status("brent", R["eia_brent"], R["eia_meta"], s)
    for st, src in ((usd, R["fed_h10"]), (y2, R["fed_h15"]), (br, R["eia_brent"])):
        o = (src.data or {}).get("obs") or []
        if len(o) >= 2:
            st["prior_date"], st["prior_value"] = o[-2]["date"], o[-2]["value"]
    if usd.get("prior_value"):
        usd["daily_change_pct"] = (usd["latest_value"] / usd["prior_value"] - 1) * 100
        o = R["fed_h10"].data["obs"]
        tgt = _obs_dt(o[-1]["date"]) - timedelta(days=30)
        base = [x for x in o if _obs_dt(x["date"]) <= tgt]
        usd["month_change_pct"] = (o[-1]["value"] / base[-1]["value"] - 1) * 100 if base else None
        usd["month_base_date"] = base[-1]["date"] if base else None
    if y2.get("prior_value") is not None:
        y2["daily_change_pp"] = y2["latest_value"] - y2["prior_value"]
        o = R["fed_h15"].data["obs"]
        y2["five_obs_change_bps"] = (o[-1]["value"] - o[-6]["value"]) * 100 if len(o) >= 6 else None
    return {"usd": usd, "y2": y2, "brent": br}


# ---------- five pillars ----------
def pillar(key, valid, score, detail, inputs, reason=None, observed=None):
    return {"key": key, "valid": valid, "score": score if valid else None, "detail": detail, "inputs": inputs,
            "reason": reason, "observed_at": observed, "weight": PILLAR_W[key]}


def five_pillars(R, s, macro):
    ok = R["okx"]
    d = ok.data if ok.data else None
    out = {}
    tb_age, oi_age, tech_age = s["trades_book_age_min"] * 60, s["oi_funding_age_min"] * 60, s["tech_candle_age_min"] * 60
    if not d:
        why = f"OKX snapshot unavailable: {ok.error}"
        for k in ("money_flow", "liquidity", "positioning", "technical"):
            out[k] = pillar(k, False, None, why, {}, why)
    else:
        src = "OKX BTC-USDT-SWAP (USDT)"
        tr = d["trades"]
        last_ts = max((t["ts"] for t in tr), default=None)
        b = sum(t["btc"] * t["price"] for t in tr if t["side"] == "buy")
        se = sum(t["btc"] * t["price"] for t in tr if t["side"] == "sell")
        imb = (b - se) / (b + se) if (b + se) > 0 else None
        age = (now_utc().timestamp() - last_ts) if last_ts else None
        v = imb is not None and age is not None and age <= tb_age and age >= -5
        win = (min(t["ts"] for t in tr), last_ts) if tr else (None, None)
        out["money_flow"] = pillar("money_flow", v, sgn3(imb, 0.10, -0.10) if imb is not None else None,
                                   f"{src}: buyer-initiated ${b:,.0f} vs seller-initiated ${se:,.0f} USDT over last {len(tr)} trades; imbalance {imb:+.3f}" if imb is not None else "no trades",
                                   {"buy_usdt": b, "sell_usdt": se, "imbalance": imb, "trades": len(tr), "window_start": iso(win[0]), "window_end": iso(win[1])},
                                   None if v else f"latest trade age {age and round(age)}s exceeds {tb_age}s", iso(last_ts))
        bk = d["book"]
        bidn = sum(p * q for p, q in bk["bids"]); askn = sum(p * q for p, q in bk["asks"])
        share = bidn / (bidn + askn) if bidn + askn else None
        bb, ba = (bk["bids"][0][0] if bk["bids"] else None), (bk["asks"][0][0] if bk["asks"] else None)
        spread = (ba - bb) / ((ba + bb) / 2) * 10000 if bb and ba else None
        cov_b, cov_a = min(1, bidn / s["max_order_notional"]), min(1, askn / s["max_order_notional"])
        bage = age_sec(bk["event_time"])
        v = share is not None and bage is not None and bage <= tb_age and min(cov_b, cov_a) >= 0.99
        out["liquidity"] = pillar("liquidity", v, sgn3(share, 0.55, 0.45) if share is not None else None,
                                  f"{src}: {len(bk['bids'])}+{len(bk['asks'])} levels, bid share {share:.3f}, spread {spread:.2f} bps, ${s['max_order_notional']:,.0f} sweep coverage bid {cov_b:.0%} / ask {cov_a:.0%}" if share is not None else "empty book",
                                  {"bid_notional": bidn, "ask_notional": askn, "bid_share": share, "spread_bps": spread, "sweep_cov_bid": cov_b, "sweep_cov_ask": cov_a},
                                  None if v else "book stale or planned-notional coverage below 99%", bk["event_time"])
        fr = d["funding_fraction"]
        fage = age_sec(d["oi_time"])
        v = finite(fr) and fage is not None and fage <= oi_age
        out["positioning"] = pillar("positioning", v, (-1 if fr >= 0.0005 else 1 if fr <= -0.0005 else 0) if finite(fr) else None,
                                    f"{src}: current funding {fr:+.6f} (fraction per 8h = {fr*100:+.4f}% = {fr*10000:+.2f} bps), OI {d['oi_btc']:,.0f} BTC (${d['oi_usd']/1e9:.2f}B)",
                                    {"funding_fraction": fr, "oi_btc": d["oi_btc"], "oi_usd": d["oi_usd"], "funding_history": d["funding_history"][:9],
                                     "oi_change": __import__("sources").EXTRA.get("oi_change"), "long_short_ratio": d.get("long_short_account_ratio", [])[:1]},
                                    None if v else f"OI/funding age {fage and round(fage)}s exceeds {oi_age}s", d["oi_time"])
        m = frame_metrics(d["candles_15m"], 900)
        tage = (now_utc().timestamp() - m["bar_close"]) if m.get("bar_close") else None
        td = trend_dir(m) if m.get("valid") else None
        grace = technical_grace(ok)
        v = td is not None and tage is not None and 0 <= tage <= tech_age + grace
        out["technical"] = pillar("technical", v, {"UP": 1, "DOWN": -1, "MIXED": 0}.get(td),
                                  f"{src} completed 15m: close {m.get('close')}, EMA20 {m.get('ema20') and round(m['ema20'],1)}, EMA50 {m.get('ema50') and round(m['ema50'],1)}, ATR14 {m.get('atr14') and round(m['atr14'],1)}, VWAP20 {m.get('vwap20') and round(m['vwap20'],1)}, prior-20 range {m.get('prior20_low')}–{m.get('prior20_high')}, vol ratio {m.get('volume_ratio') and round(m['volume_ratio'],2)} → {td}",
                                  {k: m.get(k) for k in ("close", "ema20", "ema50", "ema20_prev", "atr14", "vwap20", "prior20_high", "prior20_low", "volume_ratio", "bars")} | {"breakout": "UP" if m.get("close") and m["close"] > m["prior20_high"] else "DOWN" if m.get("close") and m["close"] < m["prior20_low"] else "NONE"},
                                  None if v else ("; ".join(m.get("issues", [])) or f"completed candle age {tage and round(tage)}s exceeds {tech_age}s + {grace}s retrieval grace"),
                                  iso(m.get("bar_close")))
    usd, y2, br = macro["usd"], macro["y2"], macro["brent"]
    mv = usd["valid"] and y2["valid"] and br["valid"] and usd.get("daily_change_pct") is not None and y2.get("daily_change_pp") is not None
    ms = None
    reasons = [f"{k}: {'; '.join(v['issues'])}" for k, v in macro.items() if v["issues"]]
    stale = []
    if mv:
        # A day-over-day move from several days ago says nothing about the next 15 minutes.
        # Keep the pillar valid (shown, counted in coverage) but give an old series no direction.
        max_h = s["macro_score_max_age_h"]
        parts = {"USD": (usd, sgn3(-usd["daily_change_pct"], 0.10, -0.10)), "2Y": (y2, sgn3(-y2["daily_change_pp"], 0.05, -0.05))}
        ms = 0
        for k, (v, sc) in parts.items():
            if (v.get("observation_age_h") or 0) > max_h:
                stale.append(f"{k} {v.get('observation_age_h')}h")
            else:
                ms += sc
        if stale:
            reasons.append(f"scored neutral: observation older than {max_h}h ({', '.join(stale)})")
    out["macro"] = pillar("macro", mv, ms,
                          f"Broad USD {usd.get('latest_value')} on {usd.get('latest_date')} ({usd.get('daily_change_pct', 0):+.3f}% d/d); US 2Y {y2.get('latest_value')}% on {y2.get('latest_date')} ({y2.get('daily_change_pp', 0):+.2f} pp d/d); Brent ${br.get('latest_value')} on {br.get('latest_date')}" + (f" · {', '.join(stale)} old → scored neutral (>{s['macro_score_max_age_h']}h)" if stale else ""),
                          {"usd": usd, "y2": y2, "brent": br, "score_range": "-2..+2", "stale_scored_neutral": stale}, "; ".join(reasons) or None,
                          usd.get("latest_date"))
    return out


def live_direction(p):
    req = ["money_flow", "liquidity", "technical"]
    valid = {k: v for k, v in p.items() if v["valid"]}
    missing_req = [k for k in req if not p[k]["valid"]]
    norm = {k: (v["score"] / 2 if k == "macro" else v["score"]) for k, v in valid.items()}
    wsum = sum(PILLAR_W[k] for k in valid)
    shares = {k: PILLAR_W[k] / wsum for k in valid} if wsum > 0 else {}
    excluded = [k for k in p if k not in valid]
    res = {"coverage": len(valid), "active_shares": shares, "excluded": excluded, "partial": len(valid) < 5,
           "normalized_scores": norm, "weights_original": PILLAR_W}
    if missing_req or len(valid) < 3 or wsum <= 0:
        res.update(direction="WAIT", score=None, strength=None,
                   reason=("required pillars unavailable: " + ", ".join(missing_req)) if missing_req else "fewer than 3 valid pillars")
        return res
    score = sum(shares[k] * norm[k] for k in valid)
    res.update(score=score, strength=strength(score),
               direction="LONG" if score >= 0.15 else "SHORT" if score <= -0.15 else "NEUTRAL",
               tilt_label=f"model tilt {score:+.2f} on a −1…+1 scale (not a measured win probability)")
    return res


def four_factor(R, macro):
    usd, y2, br = macro["usd"], macro["y2"], macro["brent"]
    f = {}
    if usd["valid"] and y2["valid"] and br["valid"] and usd.get("month_change_pct") is not None and y2.get("five_obs_change_bps") is not None:
        d1 = sgn3(-usd["month_change_pct"], 1, -1)
        d2 = sgn3(-y2["five_obs_change_bps"], 10, -10)
        d3 = 1 if br["latest_value"] <= 70 else -1 if br["latest_value"] >= 85 else 0
        tot = d1 + d2 + d3
        f["macro"] = {"valid": True, "score": 1 if tot >= 1 else -1 if tot <= -1 else 0,
                      "detail": f"USD 1m {usd['month_change_pct']:+.2f}% → {d1:+d}; 2Y 5-obs {y2['five_obs_change_bps']:+.0f} bps → {d2:+d}; Brent ${br['latest_value']} → {d3:+d}; sum {tot:+d}"}
    else:
        f["macro"] = {"valid": False, "score": None, "detail": "macro inputs not valid: " + "; ".join(sum((v["issues"] for v in macro.values()), [])) }
    bb = R["etf_bitbo"]
    f["etf"] = {"valid": False, "score": None, "detail": f"ETF flow unavailable (Bitbo: {bb.error or bb.health()}); totals are never inferred from headlines"}
    if bb.data and bb.health() == "ok":
        L = bb.data["latest"]
        lag = us_trading_days_since(date.fromisoformat(L["date"]), now_utc().date())
        if lag <= ETF_MAX_TRADING_DAYS_OLD:
            v = L["total_musd"]
            f["etf"] = {"valid": True, "score": 1 if v >= 300 else -1 if v <= -300 else 0, "lag_trading_days": lag,
                        "detail": f"US spot ETF net flow {v:+.1f} USD m on {L['date']} ({lag} US trading day(s) old; Bitbo, issuer-compiled)"}
        else:
            f["etf"]["detail"] = f"latest ETF flow row {L['date']} is {lag} US trading days old (> {ETF_MAX_TRADING_DAYS_OLD})"
    cg = R["funding_multi"]
    if cg.data and cg.health() == "ok":
        a = cg.data["annualized_pct"]
        f["funding_all"] = {"valid": True, "score": 1 if a <= -5 else -1 if a >= 15 else 0, "proxy": True,
                            "detail": f"multi-exchange OI-weighted funding {a:+.2f}% annualized across {cg.data['venues_used']} venues ({cg.data['observation_time']}) — proxy, Binance/Bybit excluded"}
    else:
        f["funding_all"] = {"valid": False, "score": None, "detail": f"multi-exchange funding unavailable ({cg.error or cg.health()}); single-exchange funding is not substituted"}
    fg = R["fng"]
    if fg.data and fg.health() == "ok":
        v = fg.data["obs"][0]["value"]
        f["fear_greed"] = {"valid": True, "score": 1 if v <= 45 else -1 if v >= 70 else 0, "detail": f"Fear & Greed {v} ({fg.data['obs'][0]['label']}) on {fg.data['obs'][0]['date']}"}
    else:
        f["fear_greed"] = {"valid": False, "score": None, "detail": f"Fear & Greed unavailable ({fg.error})"}
    for k in f:
        f[k]["weight"] = FOUR_W[k]
    complete = all(v["valid"] for v in f.values())
    score = sum(FOUR_W[k] * f[k]["score"] for k in f) if complete else None
    band = None
    if score is not None:
        band = "STRONG BULLISH" if score >= 0.5 else "BULLISH" if score >= 0.2 else "STRONG BEARISH" if score <= -0.5 else "BEARISH" if score <= -0.2 else "NEUTRAL"
    return {"factors": f, "complete": complete, "score": score, "band": band,
            "missing": [k for k, v in f.items() if not v["valid"]]}


def six_factor(pillars, four):
    contrib, missing = {}, []
    for k in ("money_flow", "liquidity", "positioning", "macro", "technical"):
        if pillars[k]["valid"]:
            contrib[k] = pillars[k]["score"] / 2 if k == "macro" else pillars[k]["score"]
        else:
            missing.append(k)
    if four["complete"]:
        contrib["four_factor"] = four["score"]
    else:
        missing.append("four_factor (" + ", ".join(four["missing"]) + " unavailable)")
    if missing:
        return {"complete": False, "score": None, "direction": "UNAVAILABLE", "missing": missing, "contributions": contrib}
    sc = sum(SIX_W[k] * v for k, v in contrib.items())
    return {"complete": True, "score": sc, "strength": strength(sc), "direction": "LONG" if sc > 0 else "SHORT" if sc < 0 else "NEUTRAL",
            "missing": [], "contributions": contrib}


# ---------- buy/sell observation (Delta execution market, WebSocket trades/book) ----------
def flow_observation(WS, product, window_sec=300):
    cv = (product or {}).get("contract_value")
    nowt = now_utc().timestamp()
    tr = [t for t in WS["trades"] if t["ts"] >= nowt - window_sec]
    if not cv:
        return {"valid": False, "reason": "contract value unavailable for contract→BTC conversion"}
    b = sum(t["size"] * cv * t["price"] for t in tr if t["side"] == "buy")
    se = sum(t["size"] * cv * t["price"] for t in tr if t["side"] == "sell")
    bk = WS.get("book") or {}
    bidn = sum(p * q * cv for p, q in bk.get("bids", [])[:20]); askn = sum(p * q * cv for p, q in bk.get("asks", [])[:20])
    share = bidn / (bidn + askn) if bidn + askn else None
    imb = (b - se) / (b + se) if b + se else None
    if imb is None:
        label = "UNAVAILABLE"
    else:
        label = "STRONG BUY PRESSURE" if imb >= 0.65 else "STRONG SELL PRESSURE" if imb <= -0.65 else "BUY-SIDE LEAN" if imb > 0 else "SELL-SIDE LEAN" if imb < 0 else "BALANCED"
    book_sign = (1 if share > 0.5 else -1 if share < 0.5 else 0) if share is not None else None
    flow_sign = (1 if imb > 0 else -1 if imb < 0 else 0) if imb is not None else None
    conflict = flow_sign is not None and book_sign is not None and flow_sign * book_sign < 0
    return {"valid": imb is not None, "venue": "Delta Exchange India", "instrument": "BTCUSD perpetual (USD)",
            "buy_usd": b, "sell_usd": se, "trades": len(tr), "window_sec": window_sec,
            "window_start": iso(min((t["ts"] for t in tr), default=None)), "window_end": iso(max((t["ts"] for t in tr), default=None)),
            "imbalance": imb, "label": label, "bid_share": share, "ask_share": (1 - share) if share is not None else None,
            "book_time": bk.get("event_time"), "agreement": "CONFLICT" if conflict else "AGREE" if flow_sign and book_sign else "NEUTRAL",
            "note": "Book depth uses top 20 levels; trade side = taker side."}


def readiness(scalp, swing, pillars_cov, limits_ok, risk_ok):
    from strategy import MANDATORY
    mand = [c for st in (scalp, swing) for c in st["checks"] if c["id"] in MANDATORY]
    comps = {"combined_mandatory": (sum(c["status"] == "PASS" for c in mand) / max(1, len(mand)), 0.35),
             "five_pillar_coverage": (pillars_cov / 5, 0.25),
             "scalping_checks": (scalp["passed"] / max(1, len(scalp["checks"])), 0.15), "swing_checks": (swing["passed"] / max(1, len(swing["checks"])), 0.10),
             "risk_capital_controls": ((risk_ok + limits_ok) / 2, 0.05)}
    # Reallocate the removed advisory-review share proportionally. Complete
    # market/strategy inputs still reach 100%; news/calendar never enter it.
    weight_total = sum(w for _, w in comps.values())
    comps = {key: (value, weight / weight_total) for key, (value, weight) in comps.items()}
    total = sum(v * w for v, w in comps.values())
    return {"score": total, "components": {k: {"fraction": v, "weight": w} for k, (v, w) in comps.items()},
            "excluded_inputs": ["news", "calendar", "daily_review"],
            "note": "Checklist completion only — not a win probability or permission to trade. News, calendar events and daily review have no effect on this score, trade decisions or BTC direction."}
