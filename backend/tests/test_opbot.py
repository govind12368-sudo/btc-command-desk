"""Option Picks bot (owner, 26 Nov 2026): Option Picks pick 5 rules – ★ Recommended with compounded capital – on the straddle
bot's engine, paper by default, Live gated like the Straddle Bot. Replay of Option Picks' own backtest to the cent, strike from
BTCUSD futures, the two skip rules, half the equity per leg, each leg stopped on its own, restarts, one bot on the exchange at a
time, and the Testnet round trip with a single-leg exit."""
import asyncio
import sys
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from test_straddle import env, run, market, C, P, K  # noqa: F401  (env is the shared fixture)
from test_straddle_hardening import FakeDelta

UTC = timezone.utc
C2, P2, K2 = "C-BTC-61200-261126", "P-BTC-61200-261126", 61200.0


def op_engine(env, futures=61040.0, fut_prev=60500.0, broker_factory=None):
    async def sleep(sec):
        env.now[0] += timedelta(seconds=sec)
        env.mono[0] += sec
    eng = env.st.Engine(clock=lambda: env.now[0], sleep=sleep, feed_factory=lambda e: env.feed, broker_factory=broker_factory, bot="op")
    eng.mono = lambda: env.mono[0]

    async def wait_update(timeout):
        await sleep(timeout)
        if env.on_wait:
            env.on_wait.pop(0)()
    eng.wait_update = wait_update
    eng.fut = {"now": futures, "prev": fut_prev}

    async def fut():
        return eng.fut["now"]

    async def fut_1731(day):
        return eng.fut["prev"]
    eng.futures, eng.fut_1731 = fut, fut_1731
    eng.feed, eng.broker = env.feed, env.sx.PaperBroker(env.feed, equity=lambda: 4000.0)
    return eng


def opcfg(env, **kw):
    return {**env.st.defaults("op"), "_bot": "op", **kw}


def opdoc():
    return {"_id": "2026-11-26:PAPER", "date": "2026-11-26", "mode": "PAPER", "status": "PREFLIGHT", "fut_1731": 60500.0}


async def op_open(env, **kw):
    eng, cfg = op_engine(env), opcfg(env, **kw)
    market(env)
    doc = opdoc()
    assert await eng.enter(doc, env.legs, cfg)
    env.now[0] = datetime(2026, 11, 26, 11, 31, 5, tzinfo=UTC)
    return eng, doc, cfg


# ---------------- the backtest is Option Picks' own, to the cent ----------------
def test_replay_matches_option_picks_fixed_and_compounded(env):
    sys.modules.pop("optpicks", None)
    import optpicks as op
    raw = op.history()["trades"]["SSTR"]
    days = env.sc.op_days(raw, op.fee_rates)
    inputs = {"starting_capital": 4000.0, "leverage": 25.0, "gst": 0.18, "stop_multiple": 2.0, "leg_share": 0.5}
    trades = [r for r in raw if not r.get("skip")]
    for comp, sizing in ((0.0, "fixed"), (1.0, "compounded")):
        want = op.stats(op.price_seq("SSTR", trades, {**op.SIZING_DEFAULT, "compound": comp}), 4000.0, bool(comp))
        rows = env.sc.journal(days, inputs, sizing)
        m = env.sc.metrics(rows, 4000.0, rows[0]["date"], rows[-1]["date"])
        assert m["trades"] == want["trades"] and m["wins"] == want["wins"]
        assert round(m["net"], 2) == want["net"] and round(m["fees"], 2) == want["fees"] and round(m["gross"], 2) == want["gross"]
    stopped = sum(1 for t in trades for leg in t["legs"] if leg.get("exit") == "Stop")
    assert m["stop_exits"] == stopped and m["final_equity"] > 4000 * 15                     # compounded grows from the equity after each trade
    one = next(d for d in days if d.get("stopped_legs") == 1)
    leg = "call" if one["call_exit"] == "Stop" else "put"
    assert one["exit_reason"] == "Stop+Expiry" and one[f"exit_spot_{leg}"] == one["ref_price"] and one["maker"] == one["taker"]


def test_dashboard_backtest_and_journals_of_the_option_picks_bot(env):
    async def go():
        sys.modules.pop("optpicks", None)
        d = await env.st.dashboard("op")
        assert d["bot"] == "op" and d["bot_name"] == "Option Picks bot" and d["mode"] == "PAPER" and d["entry_ist"] == "17:01:00"
        assert d["settings"]["sizing_mode"] == "compounded" and d["settings"]["starting_capital"] == 4000.0 and d["settings"]["stop_mode"] == "tick"
        assert d["leg_share"] == 0.5 and [b["key"] for b in d["bots"]] == ["sb", "op"] and d["variants"] == ["op_compound", "op_fixed"]
        assert any("$20" in r for r in d["rules"]) and (await env.st.dashboard())["settings"]["starting_capital"] == 1000.0      # the Straddle Bot keeps its own
        v = await env.st.backtest_view("op")
        assert v["order"] == ["op_compound", "op_fixed"] and "★ Recommended" in v["variants"]["op_compound"]["name"]
        assert v["variants"]["op_compound"]["metrics"]["net"] > v["variants"]["op_fixed"]["metrics"]["net"] > 0
        page = await env.st.journal_page("op_compound", 0, 3, bot="op")
        keys = [c["key"] for c in page["columns"]]
        assert "stop_call" in keys and "put_stop_time" in keys and page["rows"][0]["date"] >= "2026-11-20" and page["rows"][0]["equity"] > 4000
        assert (await env.st.journal_csv("op_fixed", "op")).splitlines()[0].startswith("Date,Day,")
        with pytest.raises(ValueError):
            await env.st.journal_page("mark_fixed", bot="op")
        await env.st.save_settings({"starting_capital": 2000.0}, "a@x", "op")                  # re-sized at the bot's own capital
        v2 = await env.st.backtest_view("op")
        assert v2["variants"]["op_fixed"]["metrics"]["net"] == pytest.approx(v["variants"]["op_fixed"]["metrics"]["net"] / 2, rel=0.01)
        assert (await env.db.opbot_changes.find_one({}))["key"] == "starting_capital" and not await env.db.straddle_changes.find_one({})
    run(go())


# ---------------- entry ----------------
def test_entry_at_futures_strike_with_half_the_equity_per_leg_and_per_leg_backup_stops(env):
    async def go():
        eng, doc, cfg = await op_open(env)
        lots = env.sc.lots_for(4000 * 0.5, 25, 61040.0)
        assert doc["lots"] == lots == 819 and doc["ref_price"] == 61040.0 and doc["strike"] == K and doc["spot"] == 61010.0
        assert (doc["entry_call"], doc["entry_put"]) == (99.5, 99.5) and doc["stop_call"] == pytest.approx(199.0) and doc["stop_put"] == pytest.approx(199.0)
        assert eng.broker.stops[C]["stop"] == pytest.approx(298.5) and eng.broker.stops[P]["size"] == 819        # 3 x each leg's own sell price
        assert (await env.db.opbot_trades.find_one({"_id": doc["_id"]}))["status"] == "OPEN" and not await env.db.straddle_trades.find_one({})
        assert any(a == "Entry" and "each leg on its own" in t for a, t in env.alerts)
    run(go())


def test_strike_is_the_one_nearest_btc_futures_not_the_index(env):
    async def go():
        leg = lambda s: {**env.legs[K]["call"], "symbol": s, "product_id": hash(s) % 1000}  # noqa: E731
        legs = {K: env.legs[K], K2: {"call": leg(C2), "put": leg(P2)}}
        market(env)
        for s in (C2, P2):
            env.feed.put_quote(s, 100.0, 99.5, 100.5, 5000, 5000, 61010.0)
            env.feed.put_book(s, [(99.5, 5000)], [(100.5, 5000)])
        eng = op_engine(env, futures=61150.0)
        doc = opdoc()
        assert await eng.enter(doc, legs, opcfg(env))
        assert doc["strike"] == K2 and doc["call"] == C2 and doc["put"] == P2                   # index 61,010 would pick 61,000
    run(go())


def test_the_two_option_picks_skip_rules(env):
    async def go():
        Skip = env.st.Skip
        eng = op_engine(env)
        market(env, put=(15.0, 14.5, 15.5))
        with pytest.raises(Skip, match=r"put mark \$15.00 is below \$20"):
            await eng.enter(opdoc(), env.legs, opcfg(env))
        market(env)
        eng = op_engine(env, futures=61900.0, fut_prev=60500.0)                                 # +2.31% since 17:31 IST yesterday
        with pytest.raises(Skip, match=r"up \+2.31% since 17:31 IST yesterday"):
            await eng.enter({**opdoc(), "fut_1731": 60500.0}, env.legs, opcfg(env))
        eng = op_engine(env, futures=61600.0)                                                  # +1.82%: traded
        assert await eng.enter({**opdoc(), "fut_1731": 60500.0}, env.legs, opcfg(env))
        await env.db.opbot_trades.delete_many({})
        market(env, put=(15.0, 14.9, 15.1))
        eng = op_engine(env)
        assert await eng.enter(opdoc(), env.legs, opcfg(env, _drill=True))                      # a rehearsal does not apply them
    run(go())


# ---------------- each leg on its own ----------------
def test_one_leg_stops_the_other_settles_and_the_journal_matches_the_formula(env):
    async def go():
        eng, doc, cfg = await op_open(env)
        env.on_wait.append(lambda: market(env, call=(205.0, 204.0, 206.0)))                    # call mark 205 >= 2 x 99.5; tick mode acts at once
        assert await eng.monitor(doc, cfg) == "expiry"
        assert doc["call_exit"] == "Stop" and doc["call_stop_time"] == "17:01" and doc["exit_call"] == 206.0 and doc["stopped_legs"] == 1
        assert eng.broker.pos == {C: 0, P: -819} and C not in eng.broker.stops and P in eng.broker.stops
        assert any(a == "Call stop" and "The put stays open to 17:30" in t for a, t in env.alerts)

        async def settlement(symbol):
            return 60900.0                                                                      # put in the money by 100
        eng.settlement = settlement
        assert await eng.settle(doc, cfg)
        assert (doc["exit_reason"], doc["put_exit"], doc["exit_put"], doc["exit_call"], doc["status"]) == ("Stop+Expiry", "Settle", 100.0, 206.0, "CLOSED")
        assert doc["exit_slip"] == pytest.approx((206.0 - 205.0) / 205.0)                       # the stopped leg against its own mark
        r = (await env.st.live_journal("PAPER", cfg))[-1]
        pos = 0.819
        f = lambda n, px: min(0.0001 * n * pos, 0.035 * px * pos) * 1.18  # noqa: E731
        assert r["gross"] == pytest.approx((99.5 + 99.5 - 206.0 - 100.0) * pos)
        assert r["fees"] == pytest.approx(f(61010.0, 99.5) * 2 + f(61010.0, 206.0) + f(60900.0, 100.0))       # each leg's exit on its own notional
        assert r["stopped_legs"] == 1 and r["sizing_equity"] == 4000.0 and r["equity"] == pytest.approx(4000 + r["net"])
        m = env.sc.metrics([r], 4000.0)
        assert m["stop_exits"] == 1 and m["expiry_exits"] == 0
    run(go())


def test_both_legs_stop_on_their_own_and_the_trade_closes(env):
    async def go():
        eng, doc, cfg = await op_open(env)
        env.on_wait += [lambda: market(env, call=(205.0, 204.0, 206.0)), lambda: None, lambda: market(env, call=(80.0, 79.5, 80.5), put=(210.0, 209.0, 211.0))]
        assert await eng.monitor(doc, cfg) == "stop"
        assert doc["status"] == "CLOSED" and doc["exit_reason"] == "Stop" and doc["stopped_legs"] == 2
        assert (doc["exit_call"], doc["exit_put"]) == (206.0, 211.0) and eng.broker.pos == {C: 0, P: 0} and eng.broker.stops == {}
        assert doc["exit_slip"] == pytest.approx(((206.0 - 205.0) / 205.0 + (211.0 - 210.0) / 210.0) / 2)
        r = (await env.st.live_journal("PAPER", cfg))[-1]
        assert r["net"] < 0 and r["result"] == "Loss"
    run(go())


def test_daily_loss_guard_and_kill_switch_close_what_is_open(env):
    async def go():
        eng, doc, cfg = await op_open(env, max_daily_loss_pct=1.0)                              # $40 on $4,000
        env.on_wait.append(lambda: market(env, call=(150.0, 149.5, 150.5)))                    # call -50.5 x 0.819 = -$41, under its own stop
        assert await eng.monitor(doc, cfg) == "stop" and "daily max loss" in doc["exit_note"] and eng.broker.pos == {C: 0, P: 0}
        await env.db.opbot_trades.delete_many({})
        eng, doc, cfg = await op_open(env)
        eng.kill = True
        assert await eng.monitor(doc, cfg) == "stop" and doc["exit_note"] == "kill switch" and eng.broker.pos == {C: 0, P: 0}
        assert doc["exit_slip"] == pytest.approx(0.005) and doc["stopped_legs"] == 2                 # both bought at the ask 100.5 against mark 100
    run(go())


def test_restart_after_one_leg_stopped_keeps_the_other_leg_and_its_backup_stop(env):
    async def go():
        eng, doc, cfg = await op_open(env)
        env.feed.put_quote(C, 205.0, 204.0, 206.0)
        env.feed.put_book(C, [(204.0, 5000)], [(206.0, 5000)])
        await eng.close_leg(doc, cfg, "call", "test")
        brokers = []

        def factory(m, feed, c):
            brokers.append(env.sx.PaperBroker(feed))
            return brokers[-1]
        eng2 = op_engine(env, broker_factory=factory)                                            # as after a server restart
        eng2.broker = None

        async def settlement(symbol):
            return 61080.0
        eng2.settlement = settlement
        await eng2.step()
        d = await env.db.opbot_trades.find_one({"_id": doc["_id"]})
        assert d["status"] == "CLOSED" and d["exit_reason"] == "Stop+Expiry" and d["exit_call"] == 206.0 and d["exit_put"] == 0.0
        assert any(a == "Bot restarted mid-trade" for a, _ in env.alerts)
        b = brokers[0]
        assert b.pos == {C: 0, P: -819}                                                          # the put was never bought back: it expired worthless
        assert "Option Picks bot" in env.st.BOTS["op"]["name"]
    run(go())


# ---------------- one bot on the exchange at a time; Live gate ----------------
def test_only_one_bot_on_testnet_or_live_and_live_needs_its_own_paper_days(env, monkeypatch):
    async def go():
        import delta_broker
        monkeypatch.setattr(delta_broker, "keys", lambda mode: ("k", "s"))
        await env.st.set_state("sb", mode="TESTNET")
        with pytest.raises(ValueError, match="Only one bot may trade on the exchange"):
            await env.st.ENGINES["op"].set_mode("TESTNET", None, "a@x")
        await env.st.set_state("sb", mode="PAPER")
        for i in range(30):                                                                     # the Straddle Bot's paper days do not count
            await env.db.straddle_trades.insert_one({"_id": f"d{i}", "date": f"2026-10-{i + 1:02d}", "mode": "PAPER", "status": "CLOSED"})
        await env.st.set_state("op", conn_ok_LIVE="x", drill_ok_TESTNET="y")
        with pytest.raises(ValueError, match="Option Picks bot must run on Paper for 30 trading days first"):
            await env.st.ENGINES["op"].set_mode("LIVE", "LIVE", "a@x")
        assert (await env.st.get_state("op"))["mode"] == "PAPER" and (await env.st.dashboard("op"))["ready"]["other_on_exchange"] is None
    run(go())


# ---------------- the Testnet round trip, through a simulated exchange ----------------
class FakeDeltaOP(FakeDelta):
    def handler(self, req):
        if req.url.path == "/v2/tickers/BTCUSD":
            return httpx.Response(200, json={"success": True, "result": {"symbol": "BTCUSD", "close": "61050.5", "mark_price": "61049.0"}})
        if req.url.path == "/v2/history/candles":
            return httpx.Response(200, json={"success": True, "result": []})
        return super().handler(req)


def test_testnet_round_trip_exits_one_leg_on_its_own(env, monkeypatch):
    import delta_broker
    fx = FakeDeltaOP()
    t = httpx.MockTransport(fx.handler)
    monkeypatch.setattr(env.sx.Feed, "transport", t)
    monkeypatch.setattr(env.sx.DeltaOptions, "default_transport", t)
    monkeypatch.setattr(delta_broker, "keys", lambda mode: ("key123", "secret"))
    monkeypatch.setattr(env.sx, "clock_offset_ms", lambda: (3.2, "test clock"))
    monkeypatch.setattr(env.st, "now_utc", lambda: datetime(2026, 11, 27, 5, 0, tzinfo=UTC))

    async def connected(self):
        self.connected = True
        await asyncio.sleep(3600)
    monkeypatch.setattr(env.sx.Feed, "_run", connected)

    async def go():
        rec = await env.st.round_trip("a@x", "op")
        assert rec["ok"], rec["steps"]
        assert [s["name"] for s in rec["steps"]] == ["Pre-flight", "Arming", "Entry: sell 1 lot of each leg", "Backup stops and position on the exchange",
                                                    "Leg exit: buy the call back on its own", "Exit: buy the put back", "Nothing left behind"]
        posts = [b for m, p, b in fx.calls if m == "POST" and p == "/v2/orders"]
        assert all(b["client_order_id"].startswith("OP") for b in posts if b.get("client_order_id"))
        assert len([b for b in posts if b.get("stop_order_type")]) == 2 and all(b["stop_price"] == "300" for b in posts if b.get("stop_order_type"))   # 3 x each leg
        assert not any(p["size"] for p in fx.pos.values()) and not any(o["state"] in ("open", "pending") for o in fx.orders.values())
        assert (await env.st.get_state("op"))["drill_ok_TESTNET"] == rec["at"] and "drill_ok_TESTNET" not in await env.st.get_state("sb")
        assert (await env.db.opbot_checks.find_one({}))["bot"] == "op" and not await env.db.straddle_checks.find_one({})
        conn = await env.st.connection_test("TESTNET", "a@x", "op")
        assert conn["ok"] and "BTC futures price (Option Picks strike)" in [s["name"] for s in conn["steps"]]
    run(go())
