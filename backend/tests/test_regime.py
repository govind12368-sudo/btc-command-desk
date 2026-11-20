"""Regime Strategy paper journal: market type, rule order, 1.5R exits on the site's touch rules, 2-a-day cap, reverse book."""
import asyncio
import importlib
import os
import sys

import pytest

pytest.importorskip("mongomock_motor")
import motor.motor_asyncio as mm  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_db")
run = lambda c: asyncio.new_event_loop().run_until_complete(c)


@pytest.fixture()
def rg(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "regime", "plan_a", "daytype", "support"):
        sys.modules.pop(m, None)
    return importlib.import_module("regime")


def F(**k):
    base = {"rsi": 50.0, "stop_atr": 0.8, "vr": 1.5, "t15": "Mixed", "t1h": "Mixed", "t4h": "Mixed", "chg4h": 0.01}
    return {**base, **k}


def test_market_type(rg):
    assert rg.market_type("Trend down", "Up") == "Down trend" and rg.market_type("Unclear", "Down") == "Down trend"
    assert rg.market_type("Range", "Down") == "Range" and rg.market_type("Unclear", "Mixed") == "Range"
    assert rg.market_type("Trend up", "Down") == "Up trend" and rg.market_type("Unclear", "Up") == "Up trend"


def test_rules_in_order(rg):
    d = rg.decide
    assert d("LONG", "Trend down", F(rsi=30, stop_atr=0.6))[1:3] == ("Breakdown short", "SHORT")
    assert d("SHORT", "Trend down", F(rsi=30, stop_atr=0.59, chg4h=-0.01))[1:3] == ("No trade", None)        # stop too small; dip-bounce blocked on Trend down
    assert d("SHORT", "Unclear", F(t4h="Down", rsi=40, chg4h=-0.01))[1:3] == ("Dip-bounce long", "LONG")
    assert d("SHORT", "Unclear", F(t4h="Down", rsi=40, chg4h=0.01))[1] == "No trade"
    assert d("LONG", "Range", F(t15="Up", vr=1.2))[1:3] == ("Trend follow", "LONG")
    assert d("LONG", "Range", F(t1h="Down"))[1:3] == ("Trend fade", "SHORT")
    assert d("SHORT", "Range", F(t15="Down", t1h="Up"))[1:3] == ("Trend follow", "SHORT")                   # follow is checked first
    assert d("SHORT", "Range", F(t15="Down", vr=1.19, rsi=35, chg4h=-0.002))[1:3] == ("Dip-bounce long", "LONG")
    assert d("SHORT", "Range", F(vr=1.5, chg4h=0.002))[1] == "No trade"                                       # no trend agrees either way
    assert d("LONG", "Trend up", F(t4h="Up", rsi=66))[1:3] == ("Breakout long", "LONG")
    assert d("LONG", "Trend up", F(t4h="Up", rsi=65, chg4h=0.01))[1] == "No trade"
    assert d("SHORT", "Unclear", F(t4h="Up", rsi=50, chg4h=-0.001))[1:3] == ("Pullback long", "LONG")
    assert d("SHORT", "Trend up", F(rsi=20, stop_atr=2))[2] is None                                           # never short in an up trend
    assert d("SHORT", "Range", None)[1] == "No trade"


def test_features_use_only_closed_data(rg):
    t0 = 1_800_000_000 - 1_800_000_000 % 14400 + 7200
    bars = [{"t": t0 - 300 * k, "o": 100 + k * 0.01, "h": 101 + k * 0.01, "l": 99 + k * 0.01, "c": 100 + k * 0.01, "v": 10} for k in range(6000, 0, -1)]
    bars.append({"t": t0, "o": 100, "h": 100.5, "l": 98, "c": 98.5, "v": 25})
    bars.append({"t": t0 + 300, "o": 98.5, "h": 200, "l": 1, "c": 150, "v": 999})           # the future must not matter
    f = rg.features(t0, 0.5, bars)
    g = rg.features(t0, 0.5, bars[:-1])
    assert f == g and f["t4h"] == "Down" and f["t1h"] == "Down" and f["vr"] == 2.5 and f["chg4h"] < 0 and f["rsi"] < 50 and f["stop_atr"] > 0


def _m(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


def test_simulate_15r(rg):
    ft = 1_800_000_000 - 1_800_000_000 % 60
    e, d = 80000.0, 400.0
    s = rg.simulate("LONG", e, d, ft, [_m(ft, e, e + 700, e - 10, e), _m(ft + 60, e, e + 610, e - 10, e + 600)], None)
    assert s["result"] == "Target" and s["exit_price"] == e + 600 and s["tp"] == e + 600 and s["sl"] == e - 400 and s["size"] == 0.125
    assert abs(s["fees"] - (e * 0.0005 + (e + 600) * 0.0002) * 0.125 * 1.18) < 1e-9
    s = rg.simulate("SHORT", e, d, ft, [_m(ft, e, e + 10, e - 10, e), _m(ft + 60, e, e + 400, e - 600, e)], None)
    assert s["result"] == "Stop"                                                              # both in one minute -> stop
    s = rg.simulate("SHORT", e, d, ft, [_m(ft + 60 * k, e, e + 50, e - 50, e - 30) for k in range(362)], None)
    assert s["result"] == "Time exit" and s["exit_price"] == e - 30 and s["exit_t"] == ft + 6 * 3600 + 60


def test_compute_cap_reverse_and_totals(rg, monkeypatch):
    import plan_a
    from core import db
    e = 80000.0
    base = rg._ts("2026-11-20 09:00")
    rows, b1 = [], []
    for k in range(4):                                         # four Range-day signals the same day; the first three qualify
        cap = base + 3600 * k
        r = {"signal_id": f"SCALP-LONG-{cap - cap % 300 - 300}", "captured_ist": rg._ist(cap), "cap_t": cap, "model_dir": "LONG", "entry": e,
             "model_stop": e - 400, "day_type": "RANGE", "fill_status": "NOT FILLED" if k == 1 else "FILLED"}
        if k != 1:
            r["fill_t"] = cap - cap % 60 + 60
            b1 += [_m(r["fill_t"], e, e + 5, e - 5, e), _m(r["fill_t"] + 60, e, e + 650, e - 5, e + 600)]
        rows.append(r)
    plan_a.STATE["rows"] = rows; plan_a.STATE["live_price"] = None

    async def c1(a, b):
        return sorted(b1, key=lambda x: x["t"])
    monkeypatch.setattr(plan_a, "candles", c1)
    for i, r in enumerate(rows):
        f = F(t15="Up") if i < 3 else F(vr=0.5, chg4h=0.01)
        run(db.regime_feat.update_one({"_id": r["signal_id"]}, {"$set": {**f, "v": rg.FEAT_V}}, upsert=True))
    d = run(rg.compute())["data"]
    rs = d["regime"]["rows"][::-1]
    assert [x["rule"] for x in rs] == ["Trend follow", "Not filled", "Trend follow", "No trade"]
    assert d["paper_only"] and d["regime"]["all"]["trades"] == 2 and d["regime"]["all"]["targets"] == 2 and d["regime"]["all"]["win_rate"] == 1
    rv = d["reverse"]["rows"]
    assert len(rv) == 2 and all(x["side"] == "SHORT" and x["result"] == "Stop" and x["rule"] == "Reverse of Trend follow" for x in rv)
    assert d["regime"]["all"]["net"] == rs[2]["running_total"] and d["daily"][0]["trades"] == 2 and d["daily"][0]["rev_net"] < 0
    assert d["regime"]["live"]["trades"] == 2                                       # after the live start


def test_two_a_day_cap(rg, monkeypatch):
    import plan_a
    from core import db
    e = 80000.0
    base = rg._ts("2026-11-21 09:00")
    rows = []
    for k in range(3):
        cap = base + 3600 * k
        rows.append({"signal_id": f"SCALP-LONG-{cap - cap % 300 - 300}", "captured_ist": rg._ist(cap), "cap_t": cap, "model_dir": "LONG", "entry": e,
                     "model_stop": e - 400, "day_type": "RANGE", "fill_status": "FILLED", "fill_t": cap + 60})
    plan_a.STATE["rows"] = rows

    async def c1(a, b):
        return [_m(r["fill_t"] + 60 * i, e, e + 5, e - 5, e) for r in rows for i in range(3)]
    monkeypatch.setattr(plan_a, "candles", c1)
    for r in rows:
        run(db.regime_feat.update_one({"_id": r["signal_id"]}, {"$set": {**F(t15="Up"), "v": rg.FEAT_V}}, upsert=True))
    rs = run(rg.compute())["data"]["regime"]["rows"][::-1]
    assert [x.get("status") for x in rs] == ["OPEN", "OPEN", None] and "max 2 a day" in rs[2]["reason"]


def test_regime_emails_once_and_follow_the_alert_switches(rg, monkeypatch):
    import alert_mode
    import alerts
    from core import db
    monkeypatch.setenv("ALERT_EMAIL", "me@example.com"); monkeypatch.setenv("FRONTEND_URL", "https://desk.example.com")
    sent = []

    async def fake_send(to, subject, html):
        alerts._assert_safe_email(subject, html)                   # the site's email safety rules still apply
        sent.append((to, subject, html)); return "id-1"
    monkeypatch.setattr(alerts, "send_email", fake_send)
    mode = {"effective_armed": True, "kill_switch": False, "channels": {"email": True}}

    async def gm():
        return mode
    monkeypatch.setattr(alert_mode, "get_mode", gm)
    now = rg._ts("2026-11-21 10:00")
    new = {"signal_id": "SCALP-LONG-1", "cap_t": now - 300, "time_ist": "2026-11-21 09:55", "signal_side": "LONG", "day_type": "Range", "market": "Range",
           "entry": 80000.0, "d": 400.0, "rule": "Trend follow", "trade_side": "LONG", "status": "WAITING", "reason": "Range; stop 0.8x ATR"}
    closed = {**new, "signal_id": "SCALP-LONG-2", "status": "CLOSED", "result": "Target", "exit_t": now - 600, "exit_price": 80600.0, "net": 66.4, "r": 1.33,
              "fill_ist": "x", "exit_ist": "y", "running_total": 66.4, "sl": 79600.0, "tp": 80600.0}
    nofill = {**new, "signal_id": "SCALP-LONG-3", "trade_side": None, "status": None, "rule": "Not filled", "rule_note": "Dip-bounce long"}
    old = {**new, "signal_id": "SCALP-LONG-4", "cap_t": rg._ts("2026-11-19 12:00")}           # before the live start: never emailed
    run(rg.notify([new, closed, nofill, old], now))
    subj = [s[1] for s in sent]
    assert len(sent) == 4 and all(s[0] == "me@example.com" for s in sent)
    assert any("Regime LONG — Trend follow (Range)" in x for x in subj) and any("Regime trade closed — Target +$66.40" in x for x in subj)
    assert any("Dip-bounce long not filled" in x for x in subj) and "80,400.0" not in sent[0][2] and "79,600.0" in sent[0][2] and "80,600.0" in sent[0][2]
    assert "no order was placed" in sent[0][2] and "https://desk.example.com/regime" in sent[0][2]
    run(rg.notify([new, closed, nofill], now))                                          # each event only once
    assert len(sent) == 4
    mode["effective_armed"] = False
    run(rg.notify([{**new, "signal_id": "SCALP-LONG-9"}], now))
    assert len(sent) == 4 and run(db.alerts_log.find_one({"_id": "regime-entry-SCALP-LONG-9"}))["result"].startswith("not sent (alert mode is OFF")
    mode["effective_armed"] = True; mode["channels"]["email"] = False
    run(rg.notify([{**new, "signal_id": "SCALP-LONG-10"}], now))
    assert len(sent) == 4 and "email channel switched off" in run(db.alerts_log.find_one({"_id": "regime-entry-SCALP-LONG-10"}))["result"]
    mode["channels"]["email"] = True
    run(rg.notify([{**new, "signal_id": "SCALP-LONG-11", "cap_t": now - 3600}], now))        # too old for an entry email
    assert len(sent) == 4
