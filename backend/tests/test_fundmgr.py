"""Fund Manager: sizes from the allocation, the fund's risk rules (utilisation, strategy stop, fund stop, month stop, target),
decisions frozen once made, the monthly table, candle-gap check, and the paper fund's sync / controls (owner request 23 Nov 2026)."""
import asyncio
import importlib
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("mongomock_motor")
import motor.motor_asyncio as mm  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_db")
IST = timezone(timedelta(hours=5, minutes=30))
UNIT = {"elite": {"capital": 5000.0, "margin": 1000.0, "position": 10000.0}, "optpicks": {"capital": 4000.0, "margin": 4000.0, "position": 100000.0},
        "flush": {"capital": 1000.0, "margin": 1000.0, "position": 10000.0}}
run = lambda c: asyncio.new_event_loop().run_until_complete(c)


def ts(y, m, d, h=12, mi=0):
    return int(datetime(y, m, d, h, mi, tzinfo=IST).timestamp())


def tr(i, strat, t_in, t_out, net, status="CLOSED"):
    return {"id": f"{strat}:{i}", "strat": strat, "entry_t": t_in, "exit_t": t_out, "net": net, "status": status, "label": f"{strat} {i}"}


@pytest.fixture()
def fm(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for k in ("ELITE_DELTA_API_KEY", "ELITE_DELTA_API_SECRET", "DELTA_TRADE_API_KEY", "DELTA_TRADE_API_SECRET",
              "ELITE_DELTA_TESTNET_API_KEY", "ELITE_DELTA_TESTNET_API_SECRET", "DELTA_TESTNET_API_KEY", "DELTA_TESTNET_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    for m in ("core", "autotrade", "delta_broker", "daytype", "broker_keys", "flush", "runs", "trio", "elite", "regime", "plan_a", "support", "optpicks", "fundmgr"):
        sys.modules.pop(m, None)
    return importlib.import_module("fundmgr")


def test_settings_are_checked(fm):
    s = fm.clean_settings({})
    assert s["capital"] == 10000 and s["alloc"] == {"elite": 50.0, "optpicks": 40.0, "flush": 10.0} and s["target_pct"] == 10 and s["lock_at_target"] is False
    s = fm.clean_settings({"capital": 20000, "alloc": {"elite": 30}, "strat_dd_pct": {"flush": 40}, "lock_at_target": 1})
    assert s["capital"] == 20000 and s["alloc"]["elite"] == 30 and s["alloc"]["optpicks"] == 40 and s["strat_dd_pct"]["flush"] == 40 and s["lock_at_target"] is True
    with pytest.raises(ValueError, match="more than 100%"):
        fm.clean_settings({"alloc": {"elite": 70, "optpicks": 40}})
    with pytest.raises(ValueError):
        fm.clean_settings({"capital": 5})
    with pytest.raises(ValueError):
        fm.clean_settings({"strat_dd_pct": {"elite": 0}})


def test_size_follows_the_allocation_and_pnl_scales(fm):
    cfg = fm.clean_settings({"capital": 20000, "alloc": {"elite": 50, "optpicks": 20, "flush": 0}})
    res = fm.replay([tr(1, "elite", ts(2026, 1, 5), ts(2026, 1, 6), 100.0), tr(2, "optpicks", ts(2026, 1, 7), ts(2026, 1, 7, 13), -40.0),
                     tr(3, "flush", ts(2026, 1, 8), ts(2026, 1, 8, 15), 50.0)], cfg, UNIT)
    by = {r["id"]: r for r in res["rows"]}
    assert by["elite:1"]["k"] == 2.0 and by["elite:1"]["net"] == 200.0 and by["elite:1"]["margin"] == 2000.0     # $10,000 of capital = 2 x the $5,000 page size
    assert by["optpicks:2"]["k"] == 1.0 and by["optpicks:2"]["net"] == -40.0
    assert by["flush:3"]["decision"] == "SKIPPED" and "no capital" in by["flush:3"]["reason"]
    assert res["state"]["closed"] == 160.0 and res["state"]["strategies"]["elite"]["pnl"] == 200.0 and res["state"]["in_use"] == 0


def test_a_strategy_cannot_use_more_than_its_capital(fm):
    cfg = fm.clean_settings({})
    a, b = ts(2026, 2, 3, 10), ts(2026, 2, 3, 11)
    res = fm.replay([tr(1, "flush", a, a + 6 * 3600, 57.0), tr(2, "flush", b, b + 6 * 3600, 57.0), tr(3, "flush", a + 7 * 3600, a + 9 * 3600, -58.0)], cfg, UNIT)
    d = [(r["decision"], r["reason"]) for r in res["rows"]]
    assert d[0][0] == "TAKEN" and d[1][0] == "SKIPPED" and "fully in use" in d[1][1] and d[2][0] == "TAKEN"   # the third starts after the first closed
    assert res["state"]["closed"] == -1.0
    cfg2 = fm.clean_settings({"max_use_pct": 45})
    t = ts(2026, 2, 4, 17)
    res = fm.replay([tr(1, "elite", t - 3600, t + 86400, 10.0), tr(2, "optpicks", t, t + 1800, 10.0)], cfg2, UNIT)     # 1,000 + 4,000 = 50% > 45%
    assert res["rows"][1]["decision"] == "SKIPPED" and "utilisation cap" in res["rows"][1]["reason"]
    assert fm.replay([tr(1, "elite", t - 3600, t + 86400, 10.0), tr(2, "optpicks", t, t + 1800, 10.0)], cfg, UNIT)["rows"][1]["decision"] == "TAKEN"


def test_month_stop_blocks_the_rest_of_the_month_only(fm):
    cfg = fm.clean_settings({})                               # -10% of 10,000 = -1,000
    rows = [tr(1, "elite", ts(2026, 3, 2), ts(2026, 3, 3), -600.0), tr(2, "elite", ts(2026, 3, 4), ts(2026, 3, 5), -450.0),
            tr(3, "elite", ts(2026, 3, 6), ts(2026, 3, 7), 900.0), tr(4, "optpicks", ts(2026, 3, 8, 17), ts(2026, 3, 8, 17, 30), 80.0),
            tr(5, "elite", ts(2026, 4, 1), ts(2026, 4, 2), 300.0)]
    res = fm.replay(rows, cfg, UNIT, monthly_resume=True)
    d = [r["decision"] for r in res["rows"]]
    assert d == ["TAKEN", "TAKEN", "SKIPPED", "SKIPPED", "TAKEN"] and "monthly loss limit" in res["rows"][2]["reason"]
    assert [e["kind"] for e in res["events"]] == ["month_stop"] and res["state"]["closed"] == -750.0
    plain = fm.replay(rows, cfg, UNIT, rules=False)
    assert all(r["decision"] == "TAKEN" for r in plain["rows"]) and plain["state"]["closed"] == 230.0
    s = fm.month_stats(res, "2026-02", "2026-05", cfg["target_pct"])
    assert [m["month"] for m in s["months"]] == ["2026-02", "2026-03", "2026-04", "2026-05"] and [m["pct"] for m in s["months"]] == [0.0, -10.5, 3.0, 0.0]
    assert s["summary"]["negative"] == 1 and s["summary"]["hit"] == 0 and s["summary"]["worst_month"] == "2026-03" and s["summary"]["max_dd"] == -1050.0
    assert s["months"][1]["skipped"] == 2 and len(s["months"][1]["stops"]) == 1


def test_strategy_stop_needs_a_resume_and_fund_stop_stops_everything(fm):
    cfg = fm.clean_settings({"month_loss_pct": 100})          # Option Picks: 22% of 4,000 = 880
    rows = [tr(1, "optpicks", ts(2026, 5, 1, 17), ts(2026, 5, 1, 17, 30), -500.0), tr(2, "optpicks", ts(2026, 5, 2, 17), ts(2026, 5, 2, 17, 30), -400.0),
            tr(3, "optpicks", ts(2026, 5, 3, 17), ts(2026, 5, 3, 17, 30), 100.0), tr(4, "elite", ts(2026, 5, 3), ts(2026, 5, 4), 50.0),
            tr(5, "optpicks", ts(2026, 5, 6, 17), ts(2026, 5, 6, 17, 30), 100.0)]
    res = fm.replay(rows, cfg, UNIT)
    by = {r["id"]: r for r in res["rows"]}
    assert by["optpicks:3"]["decision"] == "SKIPPED" and "paused" in by["optpicks:3"]["reason"] and by["elite:4"]["decision"] == "TAKEN"
    assert by["optpicks:5"]["decision"] == "SKIPPED" and res["state"]["strategies"]["optpicks"]["paused"]
    res = fm.replay(rows, cfg, UNIT, controls=[{"t": ts(2026, 5, 5), "action": "resume", "strat": "optpicks"}])
    by = {r["id"]: r for r in res["rows"]}
    assert by["optpicks:5"]["decision"] == "TAKEN" and res["state"]["strategies"]["optpicks"]["paused"] is None and res["state"]["strategies"]["optpicks"]["dd"] == 0.0
    cfg = fm.clean_settings({"month_loss_pct": 100, "fund_dd_pct": 5})                # fund stop at -500
    rows = [tr(1, "elite", ts(2026, 6, 1), ts(2026, 6, 2), -520.0), tr(2, "optpicks", ts(2026, 6, 3, 17), ts(2026, 6, 3, 17, 30), 90.0),
            tr(3, "elite", ts(2026, 7, 2), ts(2026, 7, 3), 40.0)]
    res = fm.replay(rows, cfg, UNIT)
    assert [r["decision"] for r in res["rows"]] == ["TAKEN", "SKIPPED", "SKIPPED"] and "fund stopped" in res["rows"][1]["reason"] and res["state"]["stop_all"]
    assert [r["decision"] for r in fm.replay(rows, cfg, UNIT, monthly_resume=True)["rows"]] == ["TAKEN", "SKIPPED", "TAKEN"]      # history: released on the 1st
    res = fm.replay(rows, cfg, UNIT, controls=[{"t": ts(2026, 6, 2, 18), "action": "resume_all"}, {"t": ts(2026, 7, 1), "action": "pause", "strat": "elite"}])
    assert [r["decision"] for r in res["rows"]] == ["TAKEN", "TAKEN", "SKIPPED"] and "admin" in res["rows"][2]["reason"]


def test_target_is_tracked_and_can_lock_the_month(fm):
    rows = [tr(1, "elite", ts(2026, 8, 1), ts(2026, 8, 2), 1100.0), tr(2, "elite", ts(2026, 8, 5), ts(2026, 8, 6), -200.0), tr(3, "elite", ts(2026, 9, 5), ts(2026, 9, 6), 10.0)]
    res = fm.replay(rows, fm.clean_settings({}), UNIT)
    assert [e["kind"] for e in res["events"]] == ["target"] and all(r["decision"] == "TAKEN" for r in res["rows"])
    res = fm.replay(rows, fm.clean_settings({"lock_at_target": True}), UNIT)
    assert [r["decision"] for r in res["rows"]] == ["TAKEN", "SKIPPED", "TAKEN"] and "target reached" in res["rows"][1]["reason"]


def test_decisions_already_made_are_never_rewritten(fm):
    cfg = fm.clean_settings({"alloc": {"elite": 20, "optpicks": 40, "flush": 10}})       # elite would now be 0.4x
    rows = [tr(1, "elite", ts(2026, 10, 1), ts(2026, 10, 2), 100.0), tr(2, "elite", ts(2026, 10, 3), ts(2026, 10, 4), 100.0), tr(3, "elite", ts(2026, 10, 5), None, 30.0, "OPEN")]
    frozen = {"elite:1": {"decision": "TAKEN", "reason": None, "k": 1.0}, "elite:2": {"decision": "SKIPPED", "reason": "strategy paused (paused by an admin)", "k": 1.0}}
    res = fm.replay(rows, cfg, UNIT, frozen=frozen)
    by = {r["id"]: r for r in res["rows"]}
    assert by["elite:1"]["net"] == 100.0 and by["elite:2"]["decision"] == "SKIPPED" and by["elite:2"]["net"] is None
    assert by["elite:3"]["k"] == 0.4 and by["elite:3"]["net"] == 12.0 and res["state"]["open"] == 12.0 and res["state"]["in_use"] == 400.0 and res["state"]["closed"] == 100.0


def test_history_starts_when_option_picks_starts_and_gaps_are_counted(fm):
    cfg = fm.clean_settings({})
    trades = [tr(0, "elite", ts(2023, 6, 1), ts(2023, 6, 2), 999.0), tr(1, "optpicks", ts(2024, 1, 10, 17), ts(2024, 1, 10, 17, 30), 200.0),
              tr(2, "elite", ts(2024, 2, 1), ts(2024, 2, 3), 500.0), tr(3, "optpicks", ts(2024, 3, 2, 17), ts(2024, 3, 2, 17, 30), -100.0)]
    stress = [dict(x, net=x["net"] - 150 if x["strat"] == "optpicks" else x["net"]) for x in trades]
    h = fm.history(cfg, UNIT, trades=trades, stress_trades=stress, now_t=ts(2024, 3, 20))
    r = h["views"]["rules"]
    assert h["from"] == "2024-01" and [m["pct"] for m in r["months"]] == [2.0, 5.0, -1.0] and r["summary"]["net"] == 600.0 and h["counts"] == {"elite": 1, "optpicks": 2, "flush": 0}
    assert h["views"]["stress"]["summary"]["net"] == 300.0 and [x["size"] for x in h["scale"]] == [0.5, 1.0, 1.25, 1.5, 2.0] and h["scale"][4]["avg_pct"] == 4.0
    b = [{"t": 0}, {"t": 300}, {"t": 1500}, {"t": 1800}, {"t": 2400}]
    assert fm.candle_gaps(b) == (4, 3, 600) and fm.candle_gaps(b[:2]) == (0, 0, None)


def test_paper_fund_sync_controls_and_checks(fm):
    async def go():
        from core import db
        d = await fm.dashboard(True)
        assert d["loading"] is True
        d = await fm.sync()
        assert d["paper_only"] and d["fund"]["value"] == 10000.0 and d["fund"]["in_use"] == 0 and [s["key"] for s in d["strategies"]] == ["elite", "optpicks", "flush"]
        assert d["strategies"][0]["position"] == 10000.0 and d["strategies"][1]["capital"] == 4000.0 and d["fund"]["month"]["target"] == 1000.0
        assert any(x["key"] == "real" and "no order is sent" in x["text"] for x in d["decisions"])
        start = (await db.fund_settings.find_one({"_id": "cfg"}))["start"]
        t = int(datetime.now(timezone.utc).timestamp())
        await db.elite_trades.insert_one({"_id": "pl1-1", "status": "CLOSED", "mode": "PAPER", "wb": "P&L #1", "side": "LONG", "entry_t": t + 5, "exit_t": t + 50, "net": -200.0})
        await db.elite_trades.insert_one({"_id": "old", "status": "CLOSED", "mode": "PAPER", "wb": "Row 9", "side": "LONG", "entry_t": t - 9000, "exit_t": t - 100, "net": 999.0})
        d = await fm.sync()
        assert d["fund"]["closed"] == -200.0 and len(d["ledger"]) == 1 and d["ledger"][0]["decision"] == "TAKEN"       # trades from before the fund started are not counted
        assert await db.fund_trades.count_documents({}) == 1
        await fm.save_settings({"alloc": {"elite": 25}}, "me")                   # a later change does not resize the trade already taken
        d = await fm.sync()
        assert d["fund"]["closed"] == -200.0 and d["strategies"][0]["k"] == 0.5
        with pytest.raises(ValueError):
            await fm.save_settings({"alloc": {"elite": 90}}, "me")
        d = await fm.control("pause", "elite", "me")
        assert d["strategies"][0]["paused"] and any(x["key"] == "elite" and x["status"] == "PAUSED" for x in d["decisions"])
        await db.elite_trades.insert_one({"_id": "pl1-2", "status": "CLOSED", "mode": "PAPER", "wb": "P&L #1", "side": "SHORT", "entry_t": t + 3600, "exit_t": t + 7200, "net": 300.0})
        d = await fm.sync()
        assert d["fund"]["closed"] == -200.0 and d["ledger"][0]["decision"] == "SKIPPED"
        out = await fm.dashboard(True)
        assert out["integrity"]["summary"]["total"] >= 4 and any(e["kind"] == "skipped" for e in out["events"]) and "exchange" in out
        assert "exchange" not in await fm.dashboard(False)
        with pytest.raises(ValueError):
            await fm.control("pause", "nope", "me")
        await db.elite_trades.delete_many({})                                    # (the test trades are dated a little in the future)
        d = await fm.control("restart", None, "me")
        assert await db.fund_trades.count_documents({}) == 0 and await db.fund_controls.count_documents({}) == 0
        assert d["fund"]["closed"] == 0.0 and d["ledger"] == [] and (await db.fund_settings.find_one({"_id": "cfg"}))["start"] >= start
    run(go())


def test_only_an_admin_can_change_the_plan_or_press_a_control():
    from test_access_control import load_server
    server = load_server()
    from fastapi.testclient import TestClient
    from auth_core import current_user
    server.app.dependency_overrides[current_user] = lambda: {"id": "u1", "email": "viewer@example.com", "role": "user"}
    try:
        c = TestClient(server.app, raise_server_exceptions=False)
        assert c.put("/api/fund/settings", json={"capital": 5000}).status_code == 403
        assert c.post("/api/fund/control", json={"action": "stop_all"}).status_code == 403
    finally:
        server.app.dependency_overrides.clear()
