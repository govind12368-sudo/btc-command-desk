"""Dashboard desk summary: module readings, weights and the link to the scalp and swing plans."""
import importlib
import os
import sys
from datetime import timedelta

import pytest

pytest.importorskip("mongomock_motor")
import motor.motor_asyncio as mm  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_db")


@pytest.fixture()
def desk(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "desk"):
        sys.modules.pop(m, None)
    return importlib.import_module("desk")


def view(dec, score, b=1, s=4, n=1):
    return {"decision": dec, "score": score, "for_buy": b, "for_sell": s, "neutral": n, "factors": [1]}


SCAN = {"intraday": view("SELL", -3), "swing": view("WAIT", -1, 1, 2, 3), "day_type": {"label": "Trend down"}, "playbook": "Trend down: follow SHORT signals."}
ST = {"price": 64800.0, "decision": {"decision": "WAIT"}, "live_direction": {"score": -0.21, "direction": "SHORT", "coverage": 5, "active_shares": {"technical": 0.22}},
      "six_factor": {"complete": True, "score": -0.12, "direction": "SHORT", "strength": "LOW"},
      "pillars": {"technical": {"valid": True, "score": -1}, "macro": {"valid": False, "score": None}},
      "scalp": {"status": "WAIT", "direction": None, "passed": 7, "total_checks": 11}, "swing": {"status": "BUY", "direction": "LONG", "passed": 11, "total_checks": 11, "levels": {"entry": 64000, "stop": 62000, "target": 68000}}}
INTEL = {"tf": {"15m": {"bias": "BEARISH"}, "1h": {"bias": "NEUTRAL"}, "4h": {"bias": "BEARISH"}, "1d": {"bias": "BEARISH"}},
         "deriv": {"positioning": {"label": "BEARISH"}}, "pressure": {"windows": {"1h": {"label": "SELLERS", "buy_share": 0.446}}},
         "etf": {"label": "Steady selling by ETF investors", "streak": {"days": 7, "side": "OUTFLOW"}}, "macro": {"summary": {"label": "Macro backdrop leans against Bitcoin", "headwinds": 2, "tailwinds": 0}},
         "risk": {"crash": {"score": 58, "band": "ELEVATED"}, "recovery": {"score": 4, "band": "LOW"}}, "sentiment": {"fng": {"value": 10, "label": "Extreme Fear"},
         "news": {"total": 32, "high_impact": 3, "bullish_words": 0, "bearish_words": 4, "tone": "More negative than positive headlines", "top": [{"title": "t", "link": "https://x/y"}]}},
         "forecast": {"rows": [{"horizon": "24h", "status": "OK", "range68": [62155.0, 66638.0]}]}}


def test_readings_and_weights(desk):
    assert [desk.label(v) for v in (0.5, 0.2, 0.19, -0.19, -0.2, -0.5, None)] == ["BULLISH", "LEAN BULLISH", "NEUTRAL", "NEUTRAL", "LEAN BEARISH", "BEARISH", None]
    assert desk.r_scanner(SCAN, "intraday")[0] == -1.0 and desk.r_scanner(SCAN, "swing")[0] == -0.5 and desk.r_scanner({}, "swing")[0] is None
    assert abs(desk.r_pillars(ST, "intraday")[0] + 0.7) < 1e-9 and abs(desk.r_pillars(ST, "swing")[0] + 0.4) < 1e-9          # swing uses the six-factor model
    assert desk.r_pillars({"live_direction": {"score": None, "reason": "required pillars unavailable"}}, "intraday")[0] is None
    v, d = desk.r_pillars({"live_direction": {"score": -0.28, "direction": "WAIT", "coverage": 5}}, "intraday")
    assert abs(v + 0.28 / 0.30 * 0.5) < 1e-9 and "half strength" in d                                                    # an unconfirmed model never counts in full
    v, d = desk.r_intel(INTEL, "intraday")
    assert abs(v - (-2 / 3 - 1 - 1) / 3) < 1e-9 and "15m Bearish" in d and "45% buying" in d
    v, _ = desk.r_intel(INTEL, "swing")
    assert abs(v - (-1 - 1 - 1 - 0.54) / 4) < 1e-9 and desk.r_intel({}, "swing")[0] is None
    assert desk.r_news(INTEL)[0] == -0.5 and desk.r_news({})[0] is None
    for w in desk.WEIGHTS.values():
        assert sum(w.values()) == 100
    r = desk.compose("intraday", {"scanner": (-1.0, ""), "pillars": (-0.7, ""), "intel": (0.5, ""), "news": (None, "No data")})
    rows = {x["key"]: x for x in r["rows"]}
    assert r["coverage"] == 90 and rows["news"]["share"] is None and abs(sum(x["share"] for x in r["rows"] if x["share"]) - 100) < 1e-9     # a missing module's weight is shared out
    assert r["score"] == round((-30 - 21 + 15) / 90 * 100) and r["label"] == "LEAN BEARISH" and abs(rows["scanner"]["points"] + 100 / 3) < 1e-9
    assert desk.compose("swing", {k: (None, "") for k in desk.MODULES})["score"] is None


def test_summary_links_plans_without_changing_them(desk):
    import copy
    before = copy.deepcopy(ST)
    s = desk.summary(ST, SCAN, INTEL, [])
    assert ST == before                                                         # read-only: the signals are never touched
    assert s["reads"]["intraday"]["score"] <= -50 and s["reads"]["intraday"]["label"] == "BEARISH" and s["reads"]["swing"]["label"] in ("BEARISH", "LEAN BEARISH")
    scalp, swing = s["plans"]
    assert scalp["fit"] == "NO_SIGNAL" and "SHORT signal would have the wider picture behind it" in scalp["text"] and scalp["to"] == "/scalping"
    assert swing["fit"] == "AGAINST" and swing["status"] == "BUY" and swing["direction"] == "LONG" and swing["levels"]["entry"] == 64000 and "signal and its checks are unchanged" in swing["text"]
    assert scalp["playbook"] == {"day_type": "Trend down", "text": "Trend down: follow SHORT signals.", "to": "/scanner"} and "playbook" not in swing
    assert "this SHORT plan" in desk.plan_link({"direction": "SHORT", "status": "PLAN ONLY"}, {"score": -60, "label": "BEARISH"}, "x", "/x")["text"]
    read = {"score": 40, "label": "LEAN BULLISH"}
    assert desk.plan_link({"direction": "LONG"}, read, "x", "/x")["fit"] == "AGREES" and desk.plan_link({"direction": "SHORT"}, read, "x", "/x")["fit"] == "AGAINST"
    assert desk.plan_link({"direction": "LONG"}, {"score": 5, "label": "NEUTRAL"}, "x", "/x")["fit"] == "MIXED" and desk.plan_link(None, {"score": None, "label": None}, "x", "/x")["fit"] == "UNKNOWN"
    c = {x["key"]: x for x in s["cards"]}
    assert list(c) == ["scanner", "intel", "pillars", "news"] and all(x["to"].startswith("/") for x in s["cards"])
    assert c["intel"]["headline"] == "Crash risk 58 (Elevated) · Recovery 4 (Low)" and c["scanner"]["headline"] == "Today SELL · Swing WAIT"
    assert {p["key"]: p["valid"] for p in c["pillars"]["pillars"]}["technical"] and not {p["key"]: p["valid"] for p in c["pillars"]["pillars"]}["macro"]
    assert any(l["k"] == "Likely 24h range" and l["v"] == "62,155 – 66,638" for l in c["intel"]["lines"])
    empty = desk.summary({}, {}, {}, [])
    assert empty["reads"]["intraday"]["score"] is None and empty["plans"][0]["fit"] == "UNKNOWN" and not any(x["ok"] for x in empty["cards"])


def test_upcoming_events(desk):
    from core import now_utc, iso
    now = now_utc()
    ev = [{"title": "CPI", "time": iso(now + timedelta(hours=5)), "impact": "HIGH"}, {"title": "old", "time": iso(now - timedelta(hours=1)), "impact": "HIGH"},
          {"title": "far", "time": iso(now + timedelta(days=9)), "impact": "HIGH"}, {"title": "manual", "scheduled_utc": iso(now + timedelta(hours=30)), "impact": "MEDIUM"}]
    up = desk.upcoming(ev, now)
    assert [x["title"] for x in up] == ["CPI", "manual"] and up[0]["hours"] == 5.0
    s = desk.summary(ST, SCAN, INTEL, ev)
    assert "CPI" in s["event_risk"] and desk.summary(ST, SCAN, INTEL, ev[1:])["event_risk"] is None
