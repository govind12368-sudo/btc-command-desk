"""Option Picks (only pick 5 on the site: short ATM BTC straddle 17:00 IST, Delta India, paper only).
Trades are stored as facts and priced at the size settings ($2,000 x 25 per leg by default): qty per leg, gross, Delta fee record +
GST at entry / stop buy-back / settlement (worthless legs pay no settlement fee), equity from put + call capital; rules unchanged;
paper engine (entry at the end of the 17:00 minute, stops, settlement, fill check); re-sizing from the admin settings."""
import asyncio
import importlib
import os
import sys
from datetime import date, datetime

import pytest

pytest.importorskip("mongomock_motor")
import motor.motor_asyncio as mm  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_db")
DAY = date(2026, 11, 24)
SZ = {"put_capital": 2000.0, "put_leverage": 25.0, "call_capital": 2000.0, "call_leverage": 25.0}


@pytest.fixture()
def op(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "optpicks"):
        sys.modules.pop(m, None)
    return importlib.import_module("optpicks")


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


def prods(strikes, settle=60050.0):
    out = {}
    for k in strikes:
        for typ in "PC":
            out[f"{typ}-BTC-{k}-241126"] = {"k": float(k), "type": typ, "settle": settle, "taker": 0.0001, "cap": 0.035, "launch": 0}
    return out


def flat(t0, t1, step, px, vol=0.0):
    return {t: (px, px, px, px, vol) for t in range(t0, t1, step)}


def test_setup(op):
    st = op.settle_t(DAY)
    assert datetime.fromtimestamp(st, op.IST).strftime("%H:%M") == "17:30"
    assert st - op.live_entry_t("SSTR", DAY) == 1740                         # filled at 17:01 = close of the 17:00 minute
    assert op.ENABLED == ["SSTR"] and op.SIZING_DEFAULT == SZ
    assert op.leg_qty("P", 61390.5, SZ) == pytest.approx(0.814)               # owner's example: $50,000 / 61,390.5 -> 0.814 BTC
    assert op.capital_for("SSTR", SZ) == 4000.0 and op.size_label(SZ) == "$2,000 × 25x per leg (put + call)"


def straddle_day(op, call_high=None, put_mark=100.0, fut=60020.0, fut_prev=60500.0, settle=59000.0):
    st = op.settle_t(DAY); et = op.entry_t("SSTR", DAY)
    m1p = flat(et, st, 60, put_mark)
    m1c = flat(et, st, 60, 80.0)
    if call_high:
        m1c[et + 600] = (80.0, call_high, 79.0, 120.0, 0.0)
    t1 = {et: (100.0, 100.0, 99.0, 99.0, 5.0)}
    return {"day": DAY, "prods": prods([59800, 60000, 60200], settle), "settle": settle, "index": {}, "fut": {et: (0, 0, 0, fut, 0)},
            "futp": {st - 86400 + 60: (0, 0, 0, fut_prev, 0)}, "m5": {}, "t5": {},
            "m1": {"P-BTC-60000-241126": m1p, "C-BTC-60000-241126": m1c}, "t1": {"P-BTC-60000-241126": t1}}


def test_straddle_facts_stop_and_skips(op):
    t, why = op.build_trade("SSTR", straddle_day(op, call_high=160.0))
    assert why is None and t["skip"] is None and "net" not in t and "qty" not in t          # facts only
    put, call = t["legs"]
    assert put["fill"] == 99.0 and call["fill"] == 79.2 and call["stop"] == 158.4
    assert call["exit"] == "Stop" and call["exit_price"] == pytest.approx(160.0) and put["exit"] == "Settle" and put["exit_price"] == pytest.approx(1000.0)
    assert put["real"] is True and call["real"] is False
    assert op.build_trade("SSTR", straddle_day(op, put_mark=15.0))[0]["skip"] == "put mark below $20"
    assert "up more than 2%" in op.build_trade("SSTR", straddle_day(op, fut_prev=58500.0))[0]["skip"]


def test_price_at_size(op):
    t, _ = op.build_trade("SSTR", straddle_day(op, call_high=160.0))
    p = op.price(t, SZ)
    q = 0.833                                                                     # floor(50000 / 60020 / 0.001) x 0.001
    assert p["qty"] == q and all(l["qty"] == q for l in p["legs"])
    gross = (99.0 - 1000.0) * q + (79.2 - 160.0) * q
    fee = lambda px, npx: min(0.0001 * npx * q, 0.035 * px * q) * 1.18
    fees = fee(99.0, 60020) + fee(1000.0, 59000) + fee(79.2, 60020) + fee(160.0, 60020)  # put settles in the money: settlement fee; call: stop buy-back fee
    assert p["gross"] == pytest.approx(gross) and p["fees"] == pytest.approx(fees) and p["net"] == pytest.approx(gross - fees)
    # a worthless leg pays no settlement fee
    t2, _ = op.build_trade("SSTR", straddle_day(op, settle=60500.0))
    p2 = op.price(t2, SZ)
    put = p2["legs"][0]
    assert put["exit_price"] == 0 and put["fees"] == pytest.approx(fee(99.0, 60020), abs=1e-3)
    # put and call sized separately; P&L is linear in size
    p3 = op.price(t, {**SZ, "call_capital": 1000.0})
    assert p3["qty"] is None and p3["legs"][1]["qty"] == pytest.approx(0.416) and p3["legs"][0]["qty"] == q
    small = op.price(t, {k: (v / 2 if k.endswith("capital") else v) for k, v in SZ.items()})
    assert small["net"] == pytest.approx(p["net"] * 0.416 / 0.833, rel=1e-9)


def test_hold_kind_still_priced(op):
    st = op.settle_t(DAY); et = op.entry_t("IFLY", DAY)
    P = prods([58800, 60000, 61200])
    D = {"day": DAY, "prods": P, "settle": 60050.0, "index": {et: (0, 0, 0, 60010.0, 0)}, "fut": {}, "futp": {}, "m1": {}, "t1": {}, "t5": {},
         "m5": {s: {et: (0, 0, 0, m, 0)} for s, m in (("P-BTC-60000-241126", 40.0), ("C-BTC-60000-241126", 50.0), ("P-BTC-58800-241126", 0.1), ("C-BTC-61200-241126", 0.1))}}
    t, why = op.build_trade("IFLY", D)
    p = op.price(t, {"put_capital": 1000.0, "put_leverage": 10.0, "call_capital": 1000.0, "call_leverage": 10.0})
    q = 0.166
    assert why is None and p["qty"] == q
    assert p["gross"] == pytest.approx((39.6 - 0) * q + (49.5 - 50.0) * q + (0 - 0.2) * q * 2)


def test_stats_from_capital(op):
    r = lambda d, net: {"day": d, "entry_ist": d + " 17:00", "net": net, "gross": net + 1, "fees": 1, "net_v2": net - 1, "real": True}
    s = op.stats([r("2025-12-30", 100), r("2026-01-02", -300), r("2026-01-03", 50)], 4000.0)
    assert s["trades"] == 3 and s["max_dd"] == 300 and s["lowest_balance"] == 3800 and s["return"] == pytest.approx(-150 / 4000) and s["max_dd_pct"] == pytest.approx(300 / 4000)
    tb = op.tables([r("2025-12-30", 100), r("2026-01-02", -300)], 4000.0)
    assert [y["year"] for y in tb["yearly"]] == ["2025", "2026", "Total till date"]


def test_resize_everything_and_skips_left_out(op, monkeypatch):
    t, _ = op.build_trade("SSTR", straddle_day(op, call_high=160.0))
    skip, _ = op.build_trade("SSTR", straddle_day(op, put_mark=15.0))
    monkeypatch.setattr(op, "history", lambda: {"trades": {"SSTR": [t]}})

    async def go():
        await op.db.opt_backtest.insert_one({"_id": "2026-11-23", "trades": {"SSTR": {**skip, "day": "2026-11-23"}}, "skips": {}})
        await op.compute()
        d = await op.dashboard()
        assert d["backtest"]["SSTR"]["stats"]["trades"] == 1 and d["order_notional"] == 100000
        net25 = d["backtest"]["SSTR"]["stats"]["net"]
        rd = d["backtest"]["SSTR"]["realdata"][0]
        assert rd["size"].startswith("$2,000 × 25x") and rd["verdict"].startswith("Profitable in 2024, 2025 and 2026")
        with pytest.raises(ValueError):
            await op.set_sizing({"put_leverage": 500}, "owner")
        await op.set_sizing({"put_capital": 1000.0, "call_capital": 1000.0}, "owner")
        d = await op.dashboard()
        assert d["backtest"]["SSTR"]["stats"]["net"] == pytest.approx(net25 * 0.416 / 0.833, abs=0.02)
        assert d["backtest"]["SSTR"]["stats"]["capital"] == 2000.0 and "workbook" not in str(d).lower()
    run(go())


def test_live_paper_engine(op, monkeypatch):
    st = op.settle_t(DAY)
    le = op.live_entry_t("SSTR", DAY)
    now = [le + 5]
    eng = op.Engine(clock=lambda: now[0])
    eng.started = now[0] - 3600
    ch = {"P-BTC-60000-241126": {"k": 60000.0, "type": "P", "mark": 100.0, "bid": 98.0, "ask": 102.0, "spot": 60010.0},
          "C-BTC-60000-241126": {"k": 60000.0, "type": "C", "mark": 80.0, "bid": 78.0, "ask": 82.0, "spot": 60010.0}}

    async def fake_chain(d):
        return ch

    async def fake_candles(sym, res, s, e):
        if sym == "BTCUSD":
            return {st - 86400 + 60: (0, 0, 0, 60000.0, 0)}
        if sym.startswith("MARK:C"):
            hi = 170.0 if now[0] > le + 300 else 90.0
            return {le: (80.0, hi, 79.0, 85.0, 0)}
        if sym.startswith("MARK:P"):
            return {le: (100.0, 110.0, 99.0, 100.0, 0)}
        et = int(s) + 180                                             # real-trade candles for the fill check
        return {et: (99.5, 99.5, 98.0, 99.0, 3.0)} if sym.startswith("P-") else {et: (78.0, 78.5, 77.0, 78.0, 2.0)}

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
        t = await op.db.opt_live.find_one({"_id": f"SSTR-{DAY.isoformat()}"})
        assert t["status"] == "OPEN" and [l["fill"] for l in t["legs"]] == [99.0, 79.2] and t["legs"][0]["bid"] == 98.0 and t["price"] == 60020.0
        now[0] += 600                                                 # call mark high 170 >= 2 x 79.2: call bought back, put stays open
        await eng.tick()
        t = await op.db.opt_live.find_one({"_id": t["_id"]})
        assert t["legs"][1]["status"] == "STOPPED" and t["legs"][1]["exit_price"] == 160.0 and t["legs"][0]["status"] == "OPEN"
        assert t["fill_checked"] and t["legs"][0]["real"] is True and t["legs"][1]["real"] is False      # bid-side print at 99.5 >= 99.0 only for the put
        now[0] = st + 120                                             # after 17:30: the put settles at 100 (60000 - 59900)
        await eng.tick()
        t = await op.db.opt_live.find_one({"_id": t["_id"]})
        assert t["status"] == "CLOSED" and t["legs"][0]["exit_price"] == pytest.approx(100.0)
        await op.compute()
        d = await op.dashboard()
        lt = d["live"][0]
        q = 0.833
        assert lt["qty"] == q and lt["gross"] == pytest.approx((99.0 - 100.0) * q + (79.2 - 160.0) * q)
        assert d["live_stats"]["SSTR"]["trades"] == 1 and d["live_stats"]["SSTR"]["capital"] == 4000.0
    run(go())
