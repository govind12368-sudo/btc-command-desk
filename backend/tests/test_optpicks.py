"""Option Picks: the owner's confirmed BTC option picks (iron fly 17:15, short put 16:00, short straddle 17:00) on Delta India data;
rules as written (nearest strike, mark -/+ max($0.10, 1%), fee record + GST incl. settlement fee, 2x stop on the 1-minute mark,
skip filters), paper engine (entry at the end of the entry bar, stops, settlement)."""
import asyncio
import importlib
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

pytest.importorskip("mongomock_motor")
import motor.motor_asyncio as mm  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_db")
DAY = date(2026, 11, 24)


@pytest.fixture()
def op(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "optpicks"):
        sys.modules.pop(m, None)
    return importlib.import_module("optpicks")


def prods(strikes, launch=0, settle=60050.0):
    out = {}
    for k in strikes:
        for typ in "PC":
            out[f"{typ}-BTC-{k}-241126"] = {"k": float(k), "type": typ, "settle": settle, "taker": 0.0001, "cap": 0.035, "launch": launch}
    return out


def flat(t0, t1, step, px, hi=None):
    return {t: (px, hi if hi is not None else px, px, px, 0.0) for t in range(t0, t1, step)}


def test_entry_times(op):
    st = op.settle_t(DAY)
    assert datetime.fromtimestamp(st, op.IST).strftime("%H:%M") == "17:30"
    assert st - op.entry_t("IFLY", DAY) == 900 and op.live_entry_t("IFLY", DAY) == st - 600          # 17:15 bar, filled 17:20
    assert st - op.live_entry_t("SP16", DAY) == 5340 and st - op.live_entry_t("SSTR", DAY) == 1740   # 16:01 and 17:01
    assert st - op.entry_t("BPS", DAY) == 72000                                                      # 21:30 the day before
    assert op.ENABLED == ["IFLY", "SP16", "SSTR"] and not op.STRATS["BPS"]["enabled"] and not op.STRATS["BCS"]["enabled"]


def test_iron_fly_hold_to_settlement(op):
    st = op.settle_t(DAY); et = op.entry_t("IFLY", DAY)
    P = prods([58800, 59000, 60000, 61200, 61400])
    D = {"day": DAY, "prods": P, "settle": 60050.0, "index": {et: (0, 0, 0, 60010.0, 0)}, "fut": {}, "futp": {}, "m1": {}, "t1": {}, "t5": {},
         "m5": {"P-BTC-60000-241126": {et: (0, 0, 0, 40.0, 0)}, "C-BTC-60000-241126": {et: (0, 0, 0, 50.0, 0)},
                "P-BTC-58800-241126": {et: (0, 0, 0, 0.1, 0)}, "C-BTC-61200-241126": {et: (0, 0, 0, 0.1, 0)}}}
    t, why = op.build_trade("IFLY", D)
    assert why is None and [l["sym"] for l in t["legs"]] == ["P-BTC-60000-241126", "C-BTC-60000-241126", "P-BTC-58800-241126", "C-BTC-61200-241126"]
    q = 0.166                                                     # floor(10000 / 60010 / 0.001) x 0.001
    assert t["qty"] == q
    assert t["legs"][0]["fill"] == pytest.approx(39.6) and t["legs"][2]["fill"] == pytest.approx(0.2)
    gross = (39.6 - 0) * q + (49.5 - 50.0) * q + (0 - 0.2) * q * 2
    assert t["gross"] == pytest.approx(gross)
    entry_fees = sum(min(0.0001 * 60010 * q, 0.035 * px * q) * 1.18 for px in (39.6, 49.5, 0.2, 0.2))
    settle_fee = min(0.0001 * 60050 * q, 0.035 * 50.0 * q) * 1.18       # the ITM call pays a settlement fee, worthless legs do not
    assert t["fees"] == pytest.approx(entry_fees + settle_fee) and t["net"] == pytest.approx(gross - entry_fees - settle_fee)


def test_short_put_stop_and_filters(op):
    st = op.settle_t(DAY); et = op.entry_t("SP16", DAY)
    P = prods([59800, 60000, 60200])
    fut = {et: (0, 0, 0, 60020.0, 0)}
    futp = {st - 86400 + 60: (0, 0, 0, 60500.0, 0)}
    m1 = flat(et, st, 60, 100.0)
    m1[et + 600] = (100.0, 199.5, 99.0, 150.0, 0)                     # high reaches 2x the 99.0 sell price 10 minutes after entry
    D = {"day": DAY, "prods": P, "settle": 59000.0, "index": {}, "fut": fut, "futp": futp, "m5": {}, "t1": {}, "t5": {}, "m1": {"P-BTC-60000-241126": m1}}
    t, why = op.build_trade("SP16", D)
    leg = t["legs"][0]
    assert leg["fill"] == 99.0 and leg["stop"] == 198.0 and leg["exit"] == "Stop" and leg["exit_price"] == pytest.approx(200.0)
    assert t["skip"] is None and t["gross"] == pytest.approx((99.0 - 200.0) * 0.166)
    # no stop: settles at max(strike - settlement, 0)
    D["m1"]["P-BTC-60000-241126"] = flat(et, st, 60, 100.0)
    t, _ = op.build_trade("SP16", D)
    assert t["legs"][0]["exit"] == "Settle" and t["legs"][0]["exit_price"] == pytest.approx(1000.0)
    # filters
    D["m1"]["P-BTC-60000-241126"] = flat(et, st, 60, 15.0)
    assert op.build_trade("SP16", D)[0]["skip"] == "put mark below $20"
    D["m1"]["P-BTC-60000-241126"] = flat(et, st, 60, 100.0)
    D["futp"] = {st - 86400 + 60: (0, 0, 0, 58500.0, 0)}
    assert "up more than 2%" in op.build_trade("SP16", D)[0]["skip"]


def test_stats_and_tables(op):
    r = lambda d, net: {"day": d, "entry_ist": d + " 16:00", "net": net, "gross": net + 1, "fees": 1, "net_v2": net - 1, "real": True}
    tb = op.tables([r("2025-12-30", 10), r("2026-01-02", -30), r("2026-01-03", 5)])
    assert tb["stats"]["trades"] == 3 and tb["stats"]["max_dd"] == 30 and tb["stats"]["lowest_balance"] == 980
    assert [y["year"] for y in tb["yearly"]] == ["2025", "2026", "Total till date"] and tb["monthly"][-1]["net"] == -25


def test_live_paper_engine(op, monkeypatch):
    st = op.settle_t(DAY)
    now = [op.live_entry_t("SP16", DAY) + 5]
    eng = op.Engine(clock=lambda: now[0])
    eng.started = now[0] - 3600
    ch = {"P-BTC-60000-241126": {"k": 60000.0, "type": "P", "mark": 100.0, "bid": 98.0, "ask": 102.0, "spot": 60010.0},
          "C-BTC-60000-241126": {"k": 60000.0, "type": "C", "mark": 80.0, "bid": 78.0, "ask": 82.0, "spot": 60010.0}}
    calls = {"stop": 0}

    async def fake_chain(d):
        return ch

    async def fake_candles(sym, res, s, e):
        if sym.startswith("BTCUSD"):
            return {st - 86400 + 60: (0, 0, 0, 60000.0, 0)}
        calls["stop"] += 1
        hi = 199.0 if now[0] > op.live_entry_t("SP16", DAY) + 300 else 120.0
        return {op.live_entry_t("SP16", DAY): (100.0, hi, 99.0, 110.0, 0)}

    async def fake_products(d, states="expired"):
        return {"P-BTC-60000-241126": {"k": 60000.0, "type": "P", "settle": 59900.0}}

    async def fake_fee(sym):
        return 0.0001, 0.035

    async def fake_fut(self=None):
        return 60020.0
    monkeypatch.setattr(op, "chain", fake_chain); monkeypatch.setattr(op, "candles", fake_candles); monkeypatch.setattr(op, "products", fake_products)
    monkeypatch.setattr(eng, "fee_info", fake_fee); monkeypatch.setattr(eng, "futures_price", fake_fut)

    async def go():
        await eng.tick()
        t = await op.db.opt_live.find_one({"_id": f"SP16-{DAY.isoformat()}"})
        assert t["status"] == "OPEN" and t["legs"][0]["fill"] == 99.0 and t["legs"][0]["stop"] == 198.0 and t["legs"][0]["bid"] == 98.0
        now[0] += 600                                               # the mark high reaches the stop
        await eng.tick()
        t = await op.db.opt_live.find_one({"_id": t["_id"]})
        assert t["legs"][0]["status"] == "STOPPED" and t["legs"][0]["exit_price"] == 200.0
        now[0] = st + 120                                           # after 17:30: settle
        await eng.tick()
        t = await op.db.opt_live.find_one({"_id": t["_id"]})
        assert t["status"] == "CLOSED" and t["gross"] == pytest.approx((99.0 - 200.0) * 0.166) and t["net"] < t["gross"]
        miss = await op.db.opt_live.find_one({"_id": f"SSTR-{DAY.isoformat()}"})        # the 17:01 entry window passed without a tick
        assert miss["status"] == "MISSED" and "missed" in miss["reason"]
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(go())
