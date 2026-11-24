"""What a normal user may receive (owner, 24 Nov 2026): pages hidden for all users are refused by the server, and the strategy
pages a user can open carry trades and results only - no rules, parameters, reasons or method text."""
import json
import time

import pytest

import viewer

RULE_WORDS = ("RSI", "ATR", "straddle", "SMA", "candles in a row", "2x", "17:00", "stop 2", "5%", "rsi_max", "atr_min", "1700")


def _text(x):
    return json.dumps(x, default=str)


def test_flush_view_keeps_trades_and_drops_rules_and_skipped_signals():
    d = {"name": "Flush Short", "computed_at": "t", "live_price": 61000, "signals_checked": 3, "is_admin": False,
         "settings": {"rsi_max": 30, "atr_min": 0.6, "market": "BTC down more than 5% over 30 days"}, "evidence": {"period": "26-month replay", "note": "RSI below 30"},
         "market_now": {"type": "Down", "chg30": -0.21, "active": True}, "all": {"trades": 1, "net": 57.6, "max_drawdown": -58.1}, "live": {"trades": 0},
         "daily": [{"date": "2026-11-18", "trades": 1, "signals": 5, "net": 57.6}],
         "live_start_ist": "2026-11-21 17:30",
         "rows": [{"signal_id": "a", "time_ist": "2026-11-18 21:20", "period": "Before live", "status": "CLOSED", "rule": "Flush short", "result": "Target", "trade_side": "SHORT", "entry": 63031.5, "sl": 63347,
                   "tp": 62637.1, "net": 57.6, "rsi": 22.1, "stop_atr": 0.7, "t15": "Down", "t1h": "Down", "t4h": "Down", "chg30": -0.23, "market": "Down", "day_type": "Trend",
                   "reason": "all six rules agree", "d": 315.5, "mfe": 1.3, "mae": 0.2},
                  {"signal_id": "b", "time_ist": "2026-11-22 10:50", "status": "SKIPPED", "rule": "No trade", "rsi": 22.6, "reason": "stop is only 0.49x the hourly ATR (needs 0.6x)"}]}
    before = _text(d)
    v = viewer.flush_view(d)
    assert _text(d) == before                                             # the engine's own data is never changed
    assert [r["signal_id"] for r in v["rows"]] == ["a"] and v["rows"][0]["net"] == 57.6 and v["rows"][0]["sl"] == 63347
    assert set(v["rows"][0]) <= set(viewer.FLUSH_ROW) and v["market_now"] == {"active": True} and v["all"] == {"trades": 1, "net": 57.6} and v["viewer"] is True
    assert "settings" not in v and "evidence" not in v and "signals_checked" not in v and "signals" not in v["daily"][0]
    assert "live" not in v and "live_start_ist" not in v and "period" not in v["rows"][0]            # one journal only: all since 3 Nov
    assert not any(w in _text(v) for w in ("RSI", "ATR", "rsi", "0.6x", "six rules", "5%"))


def test_elite_view_shows_codes_and_results_only():
    tr = {"strategy": "pl1", "wb": "P&L #1", "signal_t": 1, "entry_t": 2, "side": "SHORT", "entry": 61000, "sl": 62000, "tp": 58000, "d": 1000, "rr": 3, "chg30": -0.2,
          "market": "Down", "reason": "4 red 4h candles in a row", "net": -442.9, "status": "CLOSED"}
    out = {"name": "Elite 4 Edge", "is_admin": False, "A": {"combined": {"net": 23611.18, "max_dd": -2700.97}, "per_strategy": [{"key": "pl1", "wb": "P&L #1", "name": "4 candles in a row, follow · 1:3", "net": 1.0, "max_dd": -3812.95}],
                                                            "trades": [tr], "monthly": [], "yearly": [{"year": "2024", "net": 8090.0, "max_dd": -2701.0}]},
           "B": None, "strategies": [{"key": "pl1", "wb": "P&L #1", "name": "4 candles in a row, follow · 1:3", "tf": "4h", "rr": 3, "hold_h": 168, "priority": 1, "rule": "No filter",
                                      "signal_rule": "4 candles in a row"}],
           "settings": {"notional": 10000, "leverage": 10, "paper_capital": 5000, "dd_limit": -4000, "priority": ["P&L #1"]},
           "live": {"mode": "PAPER", "price": 61000, "paper_capital": 5000, "book": {"pnl": 112.0, "realized": 112.0, "unrealized": 0, "drawdown": -181.2, "peak": 293.37, "limit": -4000},
                    "halt": {"at": "t", "reason": "drawdown limit -4000 reached"}, "real_money": {"armed": False}, "paper_stats": {"trades": 2},
                    "open_trade": {**tr, "status": "OPEN", "name": "4 candles in a row", "time_left_h": 100.0, "unrealized": 5.0},
                    "trades": [{**tr, "id": "x", "mode": "PAPER", "order_id": 7}],
                    "signals": [{"id": "s1", "ist": "2026-11-24 01:30", "wb": "P&L #1", "side": "SHORT", "status": "TAKEN", "market": "Down", "reason": ""},
                                {"id": "s2", "ist": "2026-11-24 05:30", "wb": "Row 9", "side": "LONG", "status": "SKIPPED", "reason": "skipped - Range market"}],
                    "events": [{"text": "P&L #1: entered"}], "compare": {"rows": [{"bt_reason": "x"}]}}}
    before = _text(out)
    v = viewer.elite_view(out)
    assert _text(out) == before
    assert v["strategies"] == [{"key": "pl1", "wb": "P&L #1", "priority": 1}] and "settings" not in v
    assert v["A"]["combined"] == {"net": 23611.18} and v["A"]["trades"][0]["net"] == -442.9 and "reason" not in v["A"]["trades"][0] and "name" not in v["A"]["per_strategy"][0]
    assert "live" not in v and "B" not in v and v["is_admin"] is False and v["viewer"] is True      # journal A only: no live tab, no journal B
    assert v["A"]["combined"] == {"net": 23611.18} and v["A"]["yearly"] == [{"year": "2024", "net": 8090.0}] and "max_dd" not in _text(v)     # no max drawdown anywhere
    assert not any(w in _text(v) for w in ("candles in a row", "Range market", "No filter", "dd_limit", "-4000", "PAPER", "real_money"))


def test_optpicks_view_hides_the_strategy_definition():
    leg = {"sym": "P-BTC-61400-231126", "side": "SELL", "k": 61400, "qty": 0.814, "fill": 54.9, "exit": "Settle", "exit_price": 0, "stop": 109.8, "stop_ist": None, "high": 63.3,
           "cap": 0.035, "taker": 0.0001, "gross": 44.6, "fees": 1.8, "real": True}
    t = {"id": "SSTR-2026-11-23", "day": "2026-11-23", "key": "SSTR", "status": "CLOSED", "entry_ist": "2026-11-23 17:01:01", "price": 61390.5, "legs": [leg, leg], "gross": 50.9, "fees": 6.6,
         "net": 44.34, "net_v2": 32.8, "chg_1731": 0.005, "reason": None}
    out = {"name": "Option Picks", "is_admin": False, "paper_only": True, "enabled": ["SSTR"], "history_to": "2026-11-22", "order_notional": 100000,
           "strategies": [{"key": "SSTR", "pick": 5, "capital": 4000, "enabled": True, "code": "BTC-SSTR-ATM-1700-SL2X-F20", "name": "Short ATM straddle 17:00, stop 2x each leg",
                           "entry_ist": "17:00", "live_entry_ist": "17:01", "stop": "2x sell price (1-min mark high)", "verdict": "Profitable in 2024, 2025 and 2026"}],
           "sizing": {"put_capital": 2000, "put_leverage": 25, "call_capital": 2000, "call_leverage": 25}, "size_label": "$2,000 × 25x per leg (put + call)",
           "not_checked": "Not checked: Delta margin", "next_entry": {"SSTR": "24 Nov 17:01 IST"},
           "backtest": {"SSTR": {"stats": {"net": 12281.74, "max_dd": 597.07, "worst": -208.12, "view2_net": -2340.33}, "yearly": [], "monthly": [], "trades": [t], "trades_total": 729,
                                 "realdata": [{"period": "1 Jan 2024 – 23 Nov 2026", "size": "$2,000 × 25x per leg (put + call)", "net": 12281.74, "verdict": "Sensitive to fills", "view2_net": -2340.33,
                                               "years": {"2024": 2874.86}}]}},
           "live": [t, {"id": "m", "day": "2026-11-24", "status": "SKIPPED", "reason": "put mark below $20"}], "live_stats": {"SSTR": {"net": 44.34, "trades": 1, "view2_net": 32.84}},
           "compare": [{"day": "2026-11-23", "key": "SSTR", "live_status": "CLOSED", "live_net": 44.34, "bt_status": "TAKEN", "bt_net": 44.26, "bt_reason": None, "live_reason": None}]}
    before = _text(out)
    v = viewer.optpicks_view(out)
    assert _text(out) == before
    assert v["strategies"] == [{"key": "SSTR", "pick": 5, "capital": 4000, "enabled": True, "name": "Pick 5"}]
    assert all(k not in v for k in ("sizing", "size_label", "not_checked", "next_entry", "live", "live_stats", "compare", "enabled", "order_notional"))
    b = v["backtest"]["SSTR"]
    assert b["stats"] == {"net": 12281.74} and b["trades"][0]["net"] == 44.34 and "net_v2" not in b["trades"][0]      # no max drawdown, worst trade or worse-fills figure
    assert "stop" not in b["trades"][0]["legs"][0] and b["trades"][0]["legs"][0]["fill"] == 54.9 and "verdict" not in b["realdata"][0] and "size" not in b["realdata"][0]
    assert b["realdata"][0] == {"period": "1 Jan 2024 – 23 Nov 2026", "net": 12281.74, "years": {"2024": 2874.86}}
    assert not any(w in _text(v) for w in ("straddle", "SL2X", "2x sell", "below $20", "25x", "Sensitive"))
    assert viewer.optpicks_view({**out, "error": "ProxyError: 403 host api.india.delta.exchange"})["error"] == "delayed"     # no raw error text


def test_desk_and_fund_views():
    d = viewer.desk_view({"reads": {"intraday": 1}, "books": {"since": []}, "auto": {"mode": "TESTNET"}, "support_now": [1]})
    assert d["books"] is None and d["auto"] is None and d["support_now"] is None and d["reads"] == {"intraday": 1}
    f = viewer.fund_view({"fund": {"value": 10000}, "why_defaults": "x", "exchange": {"TESTNET": {}}, "defaults": {}, "strategies": [{"key": "elite", "name": "Elite 4 Edge", "what": "stop 2 x ATR"}],
                          "decisions": [{"key": "fund", "text": "a"}, {"key": "flush", "text": "trades only below -5%"}, {"key": "signals", "text": "b"}, {"key": "real", "text": "c"}],
                          "integrity": {"summary": {"ok": 19, "total": 19}, "flush": [{"name": "Stop and target follow the rule", "detail": "stop = entry + 1R"}]},
                          "history": {"views": {}, "flush_note": "1.7 trades a month"}, "events": [{"kind": "settings", "text": "plan"}, {"kind": "closed", "text": "+$5"}]})
    assert f["fund"] == {"value": 10000} and "what" not in f["strategies"][0] and [x["key"] for x in f["decisions"]] == ["fund", "real"]
    assert f["integrity"] == {"summary": {"ok": 19, "total": 19}} and "flush_note" not in f["history"] and [e["kind"] for e in f["events"]] == ["closed"]
    assert all(k not in f for k in ("why_defaults", "exchange", "defaults"))


def test_server_refuses_hidden_pages_to_users_and_sends_them_the_reduced_view(monkeypatch):
    from test_access_control import load_server
    server = load_server()
    from fastapi.testclient import TestClient
    from auth_core import current_user
    import viewer as live_viewer          # the copy the reloaded server uses
    role = {"r": "user"}
    server.app.dependency_overrides[current_user] = lambda: {"id": "u1", "email": "viewer@example.com", "role": role["r"]}
    hidden = frozenset({"/candle-runs", "/delta-top3", "/plan-a", "/observer", "/fund-manager", "/journal", "/regime", "/validation"})
    monkeypatch.setitem(live_viewer._CACHE, "hidden", hidden)
    monkeypatch.setitem(live_viewer._CACHE, "at", time.time() + 3600)
    elite_full = {"name": "Elite 4 Edge", "A": None, "B": None, "strategies": [{"key": "pl1", "wb": "P&L #1", "priority": 1, "name": "4 candles in a row", "rule": "No filter"}],
                  "settings": {"paper_capital": 5000, "dd_limit": -4000}, "live": {"mode": "PAPER", "real_money": {"armed": False}, "compare": {"rows": []}, "events": [{"text": "x"}], "signals": [], "trades": []}}
    opt_full = {"name": "Option Picks", "strategies": [{"key": "SSTR", "pick": 5, "capital": 4000, "enabled": True, "name": "Short ATM straddle 17:00"}], "sizing": {"put_capital": 2000},
                "backtest": {}, "live": [], "live_stats": {}, "compare": []}

    async def elite_dash(journals=True):
        return json.loads(json.dumps(elite_full if journals else {"name": "Elite 4 Edge", "live": elite_full["live"]}))

    async def opt_dash():
        return json.loads(json.dumps(opt_full))

    async def fund_dash(is_admin=False):
        return {"name": "Fund Manager", "fund": {"value": 10000}, "strategies": []}
    monkeypatch.setattr(server.elite, "dashboard", elite_dash)
    monkeypatch.setattr(server.optpicks, "dashboard", opt_dash)
    monkeypatch.setattr(server.fundmgr, "dashboard", fund_dash)
    monkeypatch.setitem(server.flush.STATE, "data", {"name": "Flush Short", "rows": [], "settings": {"rsi_max": 30}})
    try:
        c = TestClient(server.app, raise_server_exceptions=False)
        for url in ("/api/candle-runs", "/api/delta-top3", "/api/plan-a", "/api/observer", "/api/fund", "/api/regime-journal", "/api/switch-support"):
            assert c.get(url).status_code == 403, url
        r = c.get("/api/flush-short")                                       # a page the user can open
        assert r.status_code == 200 and r.json()["viewer"] is True and "settings" not in r.json() and r.json()["is_admin"] is False
        e = c.get("/api/elite")
        assert e.status_code == 200 and e.json()["viewer"] is True and all(set(s) <= {"key", "wb", "priority"} for s in e.json()["strategies"]) and "live" not in e.json() and "B" not in e.json()
        assert c.get("/api/elite/live").status_code == 403                                 # live paper trading is admin-only
        o = c.get("/api/option-picks")
        assert o.status_code == 200 and o.json()["viewer"] is True and "sizing" not in o.json() and "live" not in o.json() and o.json()["strategies"][0]["name"].startswith("Pick ")
        assert c.get("/api/option-picks/trades.csv?key=SSTR").status_code == 403           # the full trade file is admin-only
        monkeypatch.setitem(live_viewer._CACHE, "hidden", frozenset({"/plan-a"}))          # the Trade Journal still uses this data, so it stays open
        assert c.get("/api/plan-a").status_code != 403 and c.get("/api/candle-runs").status_code == 200
        monkeypatch.setitem(live_viewer._CACHE, "hidden", hidden)
        role["r"] = "admin"                                                 # an admin is never blocked and gets everything
        assert c.get("/api/candle-runs").status_code == 200 and c.get("/api/fund").status_code == 200
        a = c.get("/api/elite").json()
        assert "viewer" not in a and a["is_admin"] is True and "rule" in a["strategies"][0] and "dd_limit" in a["settings"]
        assert "sizing" in c.get("/api/option-picks").json() and "live" in c.get("/api/option-picks").json() and c.get("/api/elite/live").status_code == 200
    finally:
        server.app.dependency_overrides.clear()
