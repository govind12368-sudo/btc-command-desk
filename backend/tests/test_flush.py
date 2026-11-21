"""Flush Short v2 paper journal: the five rules, 1.25R exits with maker entry, totals, emails once."""
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
def fl(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "flush", "regime", "plan_a", "daytype", "support"):
        sys.modules.pop(m, None)
    return importlib.import_module("flush")


GOOD = {"rsi": 25.0, "stop_atr": 0.7, "t15": "Down", "t1h": "Down", "t4h": "Down"}


def test_rules(fl):
    day = fl.regime._ts("2026-11-22 10:00")
    assert fl.decide("SHORT", day, GOOD)[0] is True
    assert fl.decide("LONG", day, GOOD) == (False, "LONG signal (Flush Short takes SHORT signals only)")
    assert "not below 30" in fl.decide("SHORT", day, {**GOOD, "rsi": 30.0})[1]
    assert "night window" in fl.decide("SHORT", fl.regime._ts("2026-11-22 05:59"), GOOD)[1]
    assert fl.decide("SHORT", fl.regime._ts("2026-11-22 06:00"), GOOD)[0] is True
    assert "hourly ATR" in fl.decide("SHORT", day, {**GOOD, "stop_atr": 0.59})[1]
    assert "not all Down" in fl.decide("SHORT", day, {**GOOD, "t4h": "Mixed"})[1]
    assert fl.decide("SHORT", day, None)[0] is False


def _m(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


def test_simulate(fl):
    ft = 1_800_000_000 - 1_800_000_000 % 60
    e, d = 80000.0, 400.0
    s = fl.simulate(e, d, ft, [_m(ft, e, e + 10, e - 10, e), _m(ft + 60, e, e + 10, e - 520, e - 500)], None)
    assert s["result"] == "Target" and s["exit_price"] == e - 500 and s["tp"] == e - 500 and s["sl"] == e + 400 and s["size"] == 0.125
    assert abs(s["fees"] - (e * 0.0002 + (e - 500) * 0.0002) * 0.125 * 1.18) < 1e-9 and abs(s["net"] - (500 * 0.125 - s["fees"])) < 1e-9
    s = fl.simulate(e, d, ft, [_m(ft, e, e + 10, e - 10, e), _m(ft + 60, e, e + 450, e - 600, e)], None)
    assert s["result"] == "Stop" and abs(s["fees"] - (e * 0.0002 + (e + 400) * 0.0005) * 0.125 * 1.18) < 1e-9
    s = fl.simulate(e, d, ft, [_m(ft + 60 * k, e, e + 50, e - 50, e - 30) for k in range(362)], None)
    assert s["result"] == "Time exit" and s["exit_price"] == e - 30


def test_compute_and_emails(fl, monkeypatch):
    import alert_mode
    import alerts
    import plan_a
    from core import db
    monkeypatch.setenv("ALERT_EMAIL", "me@example.com"); monkeypatch.setenv("FRONTEND_URL", "https://desk.example.com")
    e = 80000.0
    base = fl.regime._ts("2026-11-22 10:00")
    rows, b1 = [], []
    specs = [("SHORT", GOOD, "FILLED"), ("SHORT", {**GOOD, "rsi": 40.0}, "FILLED"), ("LONG", GOOD, "FILLED"), ("SHORT", GOOD, "NOT FILLED")]
    for k, (side, f, fs) in enumerate(specs):
        cap = base + 3600 * k
        r = {"signal_id": f"SCALP-{side}-{cap - cap % 300 - 300}", "captured_ist": fl.regime._ist(cap), "cap_t": cap, "model_dir": side, "entry": e,
             "model_stop": e + 400 if side == "SHORT" else e - 400, "day_type": "TREND_DOWN", "fill_status": fs}
        if fs == "FILLED":
            r["fill_t"] = cap - cap % 60 + 60
            b1 += [_m(r["fill_t"], e, e + 5, e - 5, e), _m(r["fill_t"] + 60, e, e + 5, e - 520, e - 505)]
        rows.append(r)
        run(db.regime_feat.update_one({"_id": r["signal_id"]}, {"$set": {**f, "v": fl.regime.FEAT_V}}, upsert=True))
    plan_a.STATE["rows"] = rows; plan_a.STATE["live_price"] = None

    async def c1(a, b):
        return sorted(b1, key=lambda x: x["t"])
    monkeypatch.setattr(plan_a, "candles", c1)
    sent = []

    async def fake_send(to, subject, html):
        alerts._assert_safe_email(subject, html); sent.append(subject); return "id"
    monkeypatch.setattr(alerts, "send_email", fake_send)

    async def gm():
        return {"effective_armed": True, "kill_switch": False, "channels": {"email": True}}
    monkeypatch.setattr(alert_mode, "get_mode", gm)
    clock = [base + 3 * 3600 + 600]
    monkeypatch.setattr(fl, "EXIT_WINDOW", 4 * 3600)
    monkeypatch.setattr(fl, "now_utc", lambda: __import__("datetime").datetime.fromtimestamp(clock[0], __import__("datetime").timezone.utc))
    d = run(fl.compute())["data"]
    rs = d["rows"][::-1]
    assert [x["rule"] for x in rs] == ["Flush short", "No trade", "No trade", "Not filled"]
    assert rs[0]["result"] == "Target" and rs[0]["running_total"] == round(rs[0]["net"], 2) and d["all"]["trades"] == 1 and d["all"]["win_rate"] == 1
    assert d["paper_only"] and d["evidence"]["all"]["n"] == 67 and d["live"]["trades"] == 1
    assert any("Flush Short closed — Target" in s for s in sent) and any("not filled" in s for s in sent)
    n = len(sent)
    run(fl.compute())
    assert len(sent) == n                                              # each event emailed once
