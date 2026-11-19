"""Strategy Observer: statistics, reasons, factor table, forward page recording, full compute (read-only)."""
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
def ob(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "observer", "plan_a", "regime", "support", "daytype"):
        sys.modules.pop(m, None)
    return importlib.import_module("observer")


def test_stats(ob):
    lo, hi = ob.wilson(50, 100)
    assert 0.40 < lo < 0.41 and 0.59 < hi < 0.60 and ob.wilson(0, 0) == (None, None)
    assert ob.z_vs_rest(30, 100, 50, 300) > 0 > ob.z_vs_rest(5, 100, 50, 300)
    t = [{"net": 90.0, "win": True, "r": 1.4}] * 30 + [{"net": -60.0, "win": False, "r": -1.0}] * 10
    s = ob.book_stats(t)
    assert s["n"] == 40 and s["win"] == 0.75 and abs(s["breakeven"] - 0.4) < 1e-9 and s["verdict"] == "Proven profitable (95%)" and s["r_lo"] > 0 and s["pf"] == 4.5
    s = ob.book_stats([{"net": 90.0, "win": True, "r": 1.4}] * 3 + [{"net": -60.0, "win": False, "r": -1.0}] * 2)
    assert s["verdict"] == "Too few trades to judge"
    s = ob.book_stats([{"net": 90.0, "win": True, "r": 1.4}] * 4 + [{"net": -60.0, "win": False, "r": -1.0}] * 36)
    assert s["verdict"] == "Losing (proven)" and s["max_dd"] < 0
    assert ob.book_stats([]) == {"n": 0}


def test_why_buckets(ob):
    assert ob.why_bucket("TARGET", "Target hit in 5 min. Price moved 2x the stop gap our way; at worst it was 0.2x against first.") == "Win: clean (never 0.5R against)"
    assert ob.why_bucket("TARGET", "Target hit in 5 min. ...; at worst it was 0.9x against first.").startswith("Win: had to sit")
    assert ob.why_bucket("STOP", "Went against at once and hit the stop in 3 min (best only 0.1x our way). Later in the 6 hours price reached the target level anyway: the stop was too close for the swings.") == \
        "Loss: went against at once; target reached later (stop too close)"
    assert ob.why_bucket("STOP", "Went 1.2x our way first, then turned and hit the stop after 40 min. Price kept going against us: the direction was wrong.") == \
        "Loss: was in profit, then turned; kept going against (wrong direction)"
    assert ob.why_bucket("TIME", "No stop or target").startswith("6-hour exit")
    r = ob.reasons([{"res": "TIME", "why": ""}, {"res": "TIME", "why": ""}, {"res": "TARGET", "why": "at worst it was 0.1x"}])
    assert r[0]["n"] == 2 and abs(sum(x["share"] for x in r) - 1) < 1e-9


def _rows(ob, n=24):
    base = 1_795_000_000
    rows = []
    for k in range(n):
        side = "SHORT" if k % 2 else "LONG"
        fw = k % 3 == 0
        rows.append({"signal_id": f"S{k}", "cap_t": base + 3600 * k, "captured_ist": ob._ist(base + 3600 * k), "fill_status": "FILLED", "model_dir": side,
                     "entry": 80000.0, "model_stop": 80400.0 if side == "SHORT" else 79600.0, "qty": 0.125, "day_type": "RANGE" if k % 4 else "TREND_DOWN",
                     "playbook": "PLAN_A" if k % 4 else ("FOLLOW" if side == "SHORT" else "STAND_ASIDE"),
                     "follow_result": "TARGET" if fw else "STOP", "follow_net": 91.0 if fw else -61.5,
                     "follow_why": "Target hit in 9 min. Price moved 2x the stop gap our way; at worst it was 0.3x against first." if fw else
                     "Went against at once and hit the stop in 4 min (best only 0.2x our way). Price kept going against us: the direction was wrong.",
                     "plan_result": "STOP" if fw else "TARGET", "net": -61.5 if fw else 91.0, "plan_why": "x"})
    return rows


def test_compute_end_to_end(ob, monkeypatch):
    import plan_a
    import regime
    from core import db
    rows = _rows(ob)
    plan_a.STATE["rows"] = rows
    regime.STATE["data"] = {"regime": {"rows": [{"status": "CLOSED", "net": 66.0, "r": 1.0, "result": "Target", "signal_id": "S1", "rule": "Trend follow", "cap_t": 1_795_200_000}]}, "reverse": {"rows": []}}
    for r in rows:
        run(db.regime_feat.update_one({"_id": r["signal_id"]}, {"$set": {"v": regime.FEAT_V, "rsi": 25.0 if r["follow_result"] == "TARGET" else 50.0, "t15": "Down", "t1h": "Down",
                                                                         "t4h": "Down", "stop_atr": 0.7, "vr": 1.3, "pos24": 0.4, "chg4h": -0.004}}, upsert=True))
    run(db.validation_snapshots.insert_many([{"_id": f"v{i}", "model": "FIVE_PILLAR_CONFIRMED_V2", "outcome": "TARGET FIRST" if i < 3 else "STOP FIRST", "target_ratio": 2.0} for i in range(5)]))
    run(db.autotrade_trades.insert_one({"_id": "S4", "signal_id": "S4", "status": "CLOSED", "net_usd": -62.0, "exit_reason": "STOP", "mode": "TESTNET", "bot_dir": "LONG",
                                        "plan": "PLAN_A", "trigger_entry": 80000.0, "entry": 80010.0, "stop": 79600.0, "contracts": 125, "fees": 12.0,
                                        "fill_time": "2026-11-19T10:00:00Z"}))
    run(db.autotrade_signals.insert_one({"_id": "S5", "status": "SKIPPED", "reason": "entry order did not fill in time"}))
    d = run(ob.compute())["data"]
    B = {b["key"]: b for b in d["board"]}
    assert B["follow"]["recent"]["n"] == 24 and B["follow"]["recent"]["wins"] == 8 and B["fade"]["recent"]["wins"] == 16
    assert B["switch"]["recent"]["n"] == 18 and B["regime"]["recent"]["n"] == 1 and B["auto"]["recent"]["n"] == 1
    assert B["follow"]["history"]["n"] > 3000                                       # 26-month evidence is shipped with the code
    rsi = next(f for f in d["factors"] if f["key"] == "rsi")
    u30 = next(b for b in rsi["buckets"] if b["bucket"] == "under 30")
    assert u30["site_follow"] == {"n": 8, "wins": 8, "net": 728.0} and u30["hist_follow"]["n"] > 100
    assert d["validation"][0]["wins"] == 3 and abs(d["validation"][0]["breakeven"] - 1 / 3) < 1e-9
    assert {p["page"] for p in d["pages"]} >= {"BTC Dashboard", "Live Market", "Scalping Signal", "Swing Signal", "Five Pillars", "BTC Scanner", "Market Intelligence",
                                               "News & Events", "Trade Journal", "Plan A", "Day-type switch", "Regime Journal", "Trade Validation", "Auto-Trade"}
    assert d["conclusions"][0]["title"] == "Main finding" and "before fees" in d["conclusions"][0]["text"]
    assert d["reasons"]["follow"][0]["reason"] == "Loss: went against at once; kept going against (wrong direction)"
    assert d["extremes"]["best"][0]["avg_r"] >= d["extremes"]["worst"][0]["avg_r"]
    assert run(db.observer_log.count_documents({})) == 1 and d["log"][0]["board"]
    rs = [b["recent"].get("avg_r") for b in d["board"] if b["recent"].get("avg_r") is not None]
    assert rs == sorted(rs, reverse=True)                                          # ranked by R after fees, not by win rate
    X = d["execution"]
    assert X["n"] == 1 and X["trades"][0]["slip_usd"] == 10.0 and abs(X["trades"][0]["slip_r"] - 0.025) < 1e-9 and X["missed"] == [{"signal_id": "S5", "reason": "entry order did not fill in time"}]
    assert X["trades"][0]["paper_result"] == "TARGET" and X["trades"][0]["same_result"] is False and X["compared"] == 1
    W = d["whatif"]
    assert W["cells"] and any(c["live"] for c in W["cells"]) and W["best"]["avg_r"] == max(c["avg_r"] for c in W["cells"])
    dr = {x["key"]: x for x in d["drift"]}
    assert dr["follow"]["n"] == 24 and dr["follow"]["window"] == 24 and dr["follow"]["status"] != "Not enough trades yet" and dr["follow"]["points"][-1]["n"] == 24
    reg = {r["key"]: r for r in d["register"]["rules"]}
    assert reg["regime"]["forward"]["n"] == 1 and d["register"]["tests"] > 50 and d["register"]["expected_by_luck"] > 0
    assert d["today"]["action"].startswith("No strategy is proven") and any("treated as luck" in x for x in d["today"]["lines"])


def test_forward_snapshot_once(ob, monkeypatch):
    from core import db, iso, now_utc
    run(db.signals.insert_one({"_id": "SCALP-SHORT-9", "strategy": "scalp", "direction": "SHORT", "captured_at": iso(now_utc())}))
    run(ob.record_snapshots()); run(ob.record_snapshots())
    assert run(db.observer_snaps.count_documents({})) == 1
    L = [{"signal_id": "SCALP-SHORT-9", "side": "SHORT", "follow": {"win": True}, "fade": {"win": False}}]
    snaps = {"SCALP-SHORT-9": {"pages": {"scanner": {"value": -1.0}, "news": {"value": 0.5}, "pillars": {"value": 0.0}}}}
    f = {x["key"]: x for x in ob.forward_pages(L, snaps)}
    assert f["scanner"]["agreed"] == {"n": 1, "follow_wins": 1, "fade_wins": 0} and f["news"]["against"]["n"] == 1 and f["pillars"]["neutral"]["n"] == 1
