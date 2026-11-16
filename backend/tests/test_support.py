"""Support data for the Day-type switch: information only, saved once while the signal is fresh."""
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
def sup(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "support", "daytype"):
        sys.modules.pop(m, None)
    return importlib.import_module("support")


INTEL = {"tf": {"1h": {"score": -2, "adx": 18.0}, "4h": {"score": -4}}, "pressure": {"windows": {"1h": {"label": "SELLERS"}}}, "deriv": {"positioning": {"label": "NEUTRAL"}}}


def test_checks_and_levels(sup):
    assert sup.trade_dir("FOLLOW", "SHORT") == "SHORT" and sup.trade_dir("PLAN_A", "SHORT") == "LONG" and sup.trade_dir("STAND_ASIDE", "LONG") is None
    cs = sup.checks("FOLLOW", "SHORT", 5, INTEL, -60, None, None)                       # trend day, short with everything behind it
    assert [c["key"] for c in cs] == ["day_clear", "higher_tf", "market_read", "order_flow", "no_shock"] and all(c["ok"] for c in cs)
    assert sup.grade(cs) == {"passed": 5, "of": 5, "level": "STRONG"}
    cs = sup.checks("FOLLOW", "LONG", 3, INTEL, -60, {"title": "CPI"}, {"side": "long"})  # a long against all of it
    assert [c["ok"] for c in cs] == [False, False, False, False, False] and sup.grade(cs)["level"] == "WEAK" and "CPI" in cs[4]["text"]
    cs = sup.checks("PLAN_A", "LONG", 0, INTEL, -10, None, {"side": "short"})            # range day: quiet 1h, read mixed, sellers in the last hour
    assert [c["ok"] for c in cs] == [True, True, True, False, True] and sup.grade(cs)["level"] == "STRONG"
    assert sup.checks("PLAN_A", "LONG", 1, INTEL, -10, None, None)[0]["ok"] is False        # one trend measure on: not a clear range
    cs = sup.checks("PLAN_A", "SHORT", None, {}, None, None, None)                        # no data is never counted as a pass
    assert [c["ok"] for c in cs] == [None, None, None, None, True] and sup.grade(cs) == {"passed": 1, "of": 5, "level": "WEAK"} and cs[0]["text"] == "No data"
    assert sup.grade([{"ok": True}] * 3 + [{"ok": False}, {"ok": None}])["level"] == "MEDIUM"


def test_now_view(sup):
    v = sup.now_view({"type": "RANGE", "score": 0}, INTEL, 0, None, None)
    assert [(x["signal"], x["plan"], x["trade_dir"]) for x in v] == [("LONG", "PLAN_A", "SHORT"), ("SHORT", "PLAN_A", "LONG")] and v[0]["level"] in ("STRONG", "MEDIUM", "WEAK")
    v = sup.now_view({"type": "TREND_DOWN", "score": 4}, INTEL, -50, None, None)
    assert v[0]["plan"] == "STAND_ASIDE" and v[0]["level"] is None and v[1]["plan"] == "FOLLOW" and v[1]["trade_dir"] == "SHORT" and v[1]["passed"] == 5


def test_snapshot_is_fresh_once_and_harmless(sup):
    from core import db, now_utc
    now = now_utc().timestamp()

    async def ctx():
        return INTEL, -60, None, None
    sup.context = ctx
    tag = {"playbook": "FOLLOW", "score": 5}
    d = run(sup.snapshot("s1", now - 60, "SHORT", tag))
    assert d["level"] == "STRONG" and d["trade_dir"] == "SHORT" and run(db.switch_support.count_documents({})) == 1
    assert run(sup.snapshot("s1", now - 60, "SHORT", {"playbook": "FOLLOW", "score": 0})) is None          # stored once, never rewritten
    assert run(db.switch_support.find_one({"_id": "s1"}))["passed"] == 5
    assert run(sup.snapshot("old", now - 3600, "SHORT", tag)) is None                                         # an old signal is not rebuilt with hindsight
    assert run(sup.snapshot("aside", now - 60, "LONG", {"playbook": "STAND_ASIDE", "score": 4})) is None

    async def boom():
        raise RuntimeError("source down")
    sup.context = boom
    assert run(sup.snapshot("s2", now - 60, "SHORT", tag)) is None and run(db.switch_support.count_documents({})) == 1   # a failure never reaches the caller


def test_split_by_support_level(sup):
    trades = [{"signal_id": "a", "net": 91.3, "result": "TARGET"}, {"signal_id": "b", "net": -61.5, "result": "STOP"}, {"signal_id": "c", "net": 91.4, "result": "TARGET"},
              {"signal_id": "d", "net": 5.0, "result": "OPEN"}, {"signal_id": "e", "net": 12.0, "result": "TIME"}]
    before = [dict(t) for t in trades]
    s = sup.split(trades, {"a": {"level": "STRONG"}, "b": {"level": "WEAK"}, "d": {"level": "STRONG"}, "e": {"level": "MEDIUM"}})
    assert trades == before                                                                 # the trade list itself is never changed
    r = {x["level"]: x for x in s["rows"]}
    assert list(r) == ["STRONG", "MEDIUM", "WEAK", "NONE"]
    assert (r["STRONG"]["trades"], r["STRONG"]["closed"], r["STRONG"]["wins"], r["STRONG"]["win_rate"]) == (2, 1, 1, 1.0) and abs(r["STRONG"]["net"] - 96.3) < 1e-9
    assert r["WEAK"]["win_rate"] == 0.0 and r["MEDIUM"]["wins"] == 1 and r["NONE"]["trades"] == 1 and abs(r["NONE"]["net"] - 91.4) < 1e-9
    assert s["recorded"] == 4 and s["need"] == 30 and not s["ready"] and abs(sum(x["net"] for x in s["rows"]) - sum(t["net"] for t in trades)) < 1e-9
    assert sup.split([], None)["recorded"] == 0


def test_adx_is_read_from_the_real_analysis_output(sup):
    import ta
    bars = [{"t": 1700000000 + i * 3600, "o": 100 + i * 0.1, "h": 101 + i * 0.1, "l": 99 + i * 0.1, "c": 100.5 + i * 0.1, "v": 10.0} for i in range(300)]
    a = ta.analyze(bars, 3600)
    assert a["adx"] is not None
    cs = sup.checks("PLAN_A", "LONG", 0, {"tf": {"1h": a}}, 0, None, None)
    assert cs[1]["ok"] is not None and "ADX" in cs[1]["text"]             # the range check finds the 1h ADX in what the engine really produces


def test_filter_lookups(sup):
    from core import db
    run(db.signal_tags.insert_many([{"_id": "a", "type": "RANGE", "playbook": "PLAN_A"}, {"_id": "b", "type": "TREND_DOWN", "playbook": "FOLLOW"},
                                    {"_id": "c", "type": "TREND_DOWN", "playbook": "STAND_ASIDE"}, {"_id": "d", "type": "RANGE", "playbook": "PLAN_A"}]))
    run(db.switch_support.insert_many([{"_id": "a", "level": "STRONG", "passed": 4, "of": 5, "checks": []}, {"_id": "b", "level": "WEAK", "passed": 2, "of": 5, "checks": []}]))
    ids = lambda **k: run(sup.filter_ids(**k))
    assert ids() == {"a", "b", "c", "d"} and ids(day="RANGE") == {"a", "d"} and ids(day="TREND_DOWN", playbook="FOLLOW") == {"b"}
    assert ids(level="STRONG") == {"a"} and ids(day="RANGE", level="WEAK") == set() and ids(playbook="STAND_ASIDE") == {"c"}
    snaps = run(sup.all_snaps())
    assert snaps["a"]["level"] == "STRONG" and snaps["b"]["passed"] == 2 and set(snaps) == {"a", "b"}
    rows = [{"signal_id": "a", "fill_status": "FILLED", "follow_result": "STOP", "follow_net": -61.5}, {"signal_id": "d", "fill_status": "FILLED", "follow_result": "TARGET", "follow_net": 91.3},
            {"signal_id": "b", "fill_status": "FILLED", "follow_result": "TARGET", "follow_net": 91.4}, {"signal_id": "x", "fill_status": "NOT FILLED", "follow_result": None}]
    before = [dict(r) for r in rows]
    f = sup.follow_summary(rows, {"a", "d"})
    assert (f["signals"], f["trades"], f["wins"], f["win_rate"]) == (2, 2, 1, 0.5) and abs(f["net"] - 29.8) < 1e-9 and rows == before     # a viewing filter never alters a row
    assert sup.follow_summary(rows, set())["win_rate"] is None
