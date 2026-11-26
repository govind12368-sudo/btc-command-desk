"""Straddle bot hardening (26 Nov 2026): what must hold on a real account – restarts, stuck exits, stale prices, a shared
account, the exchange API plumbing, and the rehearsals that gate Live. The 'exchange' here is a small simulator of Delta's REST API."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from test_straddle import env, run, market, _open, C, P, K  # noqa: F401  (env is the shared fixture)

UTC = timezone.utc


# ---------------- exits ----------------
def test_a_trade_is_never_marked_closed_while_a_leg_is_still_open(env):
    async def go():
        doc = await _open(env)                                                            # P = 199, stop 398
        env.on_wait.append(lambda: (market(env, call=(390.0, 389.0, 391.0), put=(12.0, 11.5, 12.5)), env.feed.put_book(P, [(11.5, 5000)], [])))
        assert await env.eng.monitor(doc, env.cfg) == "expiry"                            # the put had no sellers: it could not be bought back
        assert env.now[0] >= datetime(2026, 11, 26, 12, 0, tzinfo=UTC)                    # it kept trying until the expiry, never gave up
        assert doc["status"] == "EXITING" and doc["bought"]["call"] == {"lots": 409, "avg": 391.0} and "put" not in doc["bought"]
        assert env.eng.broker.pos == {C: 0, P: -409} and P in env.eng.broker.stops        # the put's backup stop is still on the exchange
        assert any(a == "NOT FLAT" for a, _ in env.alerts)

        async def settlement(symbol):
            return 60950.0                                                                # put settles 50 in the money
        env.eng.settlement = settlement
        assert await env.eng.settle(doc, env.cfg)
        assert (doc["status"], doc["exit_reason"], doc["exit_call"], doc["exit_put"]) == ("CLOSED", "Stop", 391.0, 50.0)
        r = (await env.st.live_journal("PAPER", env.cfg))[-1]
        assert r["gross"] == pytest.approx((199.0 - 441.0) * 0.409)
    run(go())


def test_backup_stop_on_one_leg_closes_only_what_is_left_and_flags_the_estimate(env):
    async def go():
        doc = await _open(env, stop_multiple=5.0, max_daily_loss_pct=100.0)
        env.on_wait.append(lambda: market(env, call=(600.0, 599.0, 601.0)))               # the exchange-side stop buys the call back
        assert await env.eng.monitor(doc, env.cfg) == "stop"
        assert doc["status"] == "CLOSED" and "call" not in doc["bought"] and doc["bought"]["put"]["lots"] == 409
        assert doc["exit_call"] == 601.0 and "estimated" in doc["exit_note"]
    run(go())


def test_stale_prices_are_never_used_for_the_stop(env):
    async def go():
        doc = await _open(env)
        market(env, call=(390.0, 389.0, 391.0), put=(12.0, 11.5, 12.5))                   # far above the stop ...
        for s in (C, P):
            env.feed.q[s]["mark_at"] -= 60                                                # ... but a minute old, and nothing newer can be fetched

        async def dead(symbols=None, min_gap=1.0):
            pass
        env.feed.rest_refresh = dead
        assert env.eng.combined(doc) is None
        assert await env.eng.monitor(doc, env.cfg) == "expiry" and doc["status"] == "OPEN"   # no stop on dead data; the backup stops stay
        assert any(a == "No prices" for a, _ in env.alerts) and env.eng.broker.pos == {C: -409, P: -409}
    run(go())


# ---------------- a shared account ----------------
def test_another_position_on_the_contract_is_reported_and_never_closed_by_the_bot(env):
    async def go():
        doc = await _open(env)
        env.eng.broker.pos = {C: -459, P: -459}                                           # Delta's own automation sold 50 lots on the same contracts
        env.on_wait.append(lambda: market(env, call=(390.0, 389.0, 391.0), put=(12.0, 11.5, 12.5)))
        assert await env.eng.monitor(doc, env.cfg) == "stop"
        assert doc["foreign"] and any(a == "Other position on the contract" for a, _ in env.alerts)
        assert env.eng.broker.pos == {C: -50, P: -50}                                     # only the bot's own 409 lots were bought back
    run(go())


def test_entry_is_skipped_when_the_contract_already_has_a_position_or_order(env):
    async def go():
        market(env)
        doc = {**env.doc(), "mode": "TESTNET", "_id": "2026-11-26:TESTNET"}
        env.eng.broker.pos = {P: -5}
        with pytest.raises(env.st.Skip, match="already a position or an open order"):
            await env.eng.arm(doc, env.legs, env.cfg)
        env.eng.broker.pos = {}
        a = await env.eng.arm(doc, env.legs, env.cfg)
        assert a["lev"] == 93.0 and doc["wallet_before"] == 1000.0                        # 61010 / (1.3 x (600 - 100)) = 93.9 -> backup stop before liquidation
    run(go())


# ---------------- restarts ----------------
def _engine2(env, mode="PAPER"):
    """A fresh engine, as after a server restart, on the same database and price feed."""
    async def sleep(sec):
        env.now[0] += timedelta(seconds=sec)
        env.mono[0] += sec
    brokers = []

    def factory(m, feed, cfg):
        brokers.append(env.sx.PaperBroker(feed))
        return brokers[-1]
    eng = env.st.Engine(clock=lambda: env.now[0], sleep=sleep, feed_factory=lambda e: env.feed, broker_factory=factory)
    eng.mono = lambda: env.mono[0]

    async def wait_update(timeout):
        await sleep(timeout)
        if env.on_wait:
            env.on_wait.pop(0)()
    eng.wait_update = wait_update
    return eng, brokers


def test_restart_with_an_open_trade_carries_on_and_restores_the_backup_stops(env):
    async def go():
        doc = await _open(env)
        eng, brokers = _engine2(env)

        async def settlement(symbol):
            return 61080.0
        eng.settlement = settlement
        await eng.step()                                                                  # finds the OPEN trade, rebuilds, monitors to expiry, settles
        d = await env.db.straddle_trades.find_one({"_id": doc["_id"]})
        assert d["status"] == "CLOSED" and d["exit_reason"] == "Expiry" and d["exit_call"] == 80.0
        assert any(a == "Bot restarted mid-trade" for a, _ in env.alerts)
        assert len([o for o in d["order_ids"] if "stop" in o]) == 2
    run(go())


def test_trade_whose_expiry_passed_while_the_bot_was_down_is_settled_not_faked(env):
    async def go():
        doc = await _open(env)
        env.now[0] = datetime(2026, 11, 27, 4, 0, tzinfo=UTC)                             # the server comes back the next morning
        eng, _ = _engine2(env)

        async def settlement(symbol):
            return 60900.0
        eng.settlement = settlement
        await eng.step()
        d = await env.db.straddle_trades.find_one({"_id": doc["_id"]})
        assert (d["status"], d["exit_reason"], d["exit_call"], d["exit_put"], d["exit_spot"]) == ("CLOSED", "Expiry", 0.0, 100.0, 60900.0)
    run(go())


def test_unpublished_settlement_never_blocks_the_next_day_and_falls_back_to_the_index_average(env):
    async def go():
        doc = await _open(env)
        await env.eng._save(doc, status="SETTLING")
        eng, _ = _engine2(env)
        ran = []

        async def settlement(symbol):
            return None

        async def run_day(d, st, cfg):
            ran.append(d["_id"])
            await eng._save(d, status="SKIPPED", skip_reason="test")
        eng.settlement, eng.run_day = settlement, run_day
        env.now[0] = datetime(2026, 11, 27, 11, 25, 5, tzinfo=UTC)                        # next day's pre-flight time, 23 h after the expiry
        calls = []

        async def twap(day):
            calls.append(day)
            return 61040.0
        eng.twap = twap
        await eng.step()
        d = await env.db.straddle_trades.find_one({"_id": doc["_id"]})
        assert d["status"] == "CLOSED" and d["exit_spot"] == 61040.0 and "index 30-minute average" in d["settle_source"]
        assert any(a == "Settlement not official" for a, _ in env.alerts)
        assert ran == ["2026-11-27:PAPER"]                                                # today's trade still ran in the same pass
    run(go())


def test_restart_during_the_entry_adopts_a_hedged_position_and_evens_out_the_rest(env):
    async def go():
        market(env)
        legs = env.legs[K]
        doc = {"_id": "2026-11-26:TESTNET", "date": "2026-11-26", "mode": "TESTNET", "status": "ENTERING", "spot": 61010.0, "strike": K, "call": C, "put": P,
               "legs": legs, "maker": 0.0001, "taker": 0.0001, "cap": 0.035, "mark_call": 100.0, "mark_put": 100.0, "stop_multiple": 2.0, "lots_wanted": 409}
        await env.db.straddle_trades.insert_one(dict(doc))
        env.now[0] = datetime(2026, 11, 26, 11, 30, 20, tzinfo=UTC)
        eng, brokers = _engine2(env)
        real = eng.broker_factory

        def factory(m, feed, cfg):
            b = real(m, feed, cfg)
            b.pos, b.entry = {C: -409, P: -300}, {C: 99.5, P: 99.0}                       # the exchange filled the call fully, the put partly, then the server died
            return b
        eng.broker_factory = factory

        async def settlement(symbol):
            return 61000.0
        eng.settlement = settlement
        await eng.step()
        d = await env.db.straddle_trades.find_one({"_id": doc["_id"]})
        assert d["lots"] == 300 and d["entry_call"] == 99.5 and d["entry_put"] == 99.0 and d["entry_note"] == "adopted after a restart"
        assert d["leg_risk"][0]["leg"] == "call" and d["leg_risk"][0]["lots"] == 109 and d["leg_risk_cost"] > 0
        assert d["status"] == "CLOSED" and d["exit_reason"] == "Expiry"
    run(go())


# ---------------- plumbing ----------------
def test_feed_reads_the_fast_channels(env):
    f = env.sx.Feed("LIVE")
    f._on({"type": "mark_price", "symbol": "MARK:" + C, "price": "282.4", "best_bid": "282", "best_ask": "284", "bid_qty": "10", "ask_qty": "12",
           "price_band": {"lower_limit": "0.1", "upper_limit": "1318.1"}})
    assert f.quote(C)["mark"] == 282.4 and f.quote(C)["band"] == (0.1, 1318.1) and f.age(C) < 1
    f._on({"type": "l1_orderbook", "symbol": C, "best_bid": "283.0", "best_ask": "283.5", "bid_qty": "5", "ask_qty": "6"})
    assert (f.quote(C)["bid"], f.quote(C)["ask"], f.quote(C)["mark"]) == (283.0, 283.5, 282.4)
    f._on({"type": "l2_orderbook", "symbol": C, "buy": [{"limit_price": "282.0", "size": 9}, {"limit_price": "283.0", "size": 4}], "sell": [{"limit_price": "284.0", "size": 3}]})
    assert f.book(C)["buy"][0] == (283.0, 4.0) and f.book(C)["sell"] == [(284.0, 3.0)]
    f._on({"type": "spot_price", "symbol": ".DEXBTUSD", "price": 61030.9})
    assert f.spot == 61030.9 and f.age("unknown") == float("inf")
    f._on({"type": "v2/ticker", "symbol": C, "mark_price": "1"})                          # not used: it only comes every 5 s
    assert f.quote(C)["mark"] == 282.4


def test_real_event_log_does_not_repeat_itself(env):
    async def go():
        for _ in range(5):
            await env.real_event("Bot error: database is down", "error", None, "PAPER", {"alerts": 0})
        assert await env.db.straddle_events.count_documents({}) == 1
    run(go())


class FakeDelta:
    """A small simulator of the parts of Delta's REST API the bot uses."""

    def __init__(self, bid=100.0, ask=100.5, mark=100.0, spot=61010.0):
        self.spot, self.q = spot, {"bid": bid, "ask": ask, "mark": mark}
        self.products, self.orders, self.pos, self.lev, self.calls, self.n, self.busy = {}, {}, {}, {}, [], 100, 0
        for i, k in enumerate((60600, 60800, 61000, 61200, 61400)):
            for t in ("C", "P"):
                pid = 500 + i * 2 + (t == "P")
                self.products[pid] = {"id": pid, "symbol": f"{t}-BTC-{k}-271126", "strike": k, "type": "call_options" if t == "C" else "put_options"}
        self.by_symbol = {p["symbol"]: p for p in self.products.values()}

    def _order(self, o):
        return {"id": o["id"], "size": o["size"], "unfilled_size": o["size"] - o["filled"], "average_fill_price": str(o["avg"]) if o["avg"] else None, "state": o["state"],
                "side": o["side"], "client_order_id": o.get("coid"), "product_id": o["pid"], "stop_order_type": o.get("stop"), "limit_price": o.get("price"), "updated_at": "1795670782259341"}

    def _fill(self, o, n, price):
        o["avg"] = ((o["avg"] or 0) * o["filled"] + price * n) / (o["filled"] + n)
        o["filled"] += n
        p = self.pos.setdefault(o["pid"], {"size": 0, "entry": None})
        p["size"] += n if o["side"] == "buy" else -n
        if o["side"] == "sell":
            p["entry"] = price

    def handler(self, req):
        path, body = req.url.path, (json.loads(req.content) if req.content else None)
        self.calls.append((req.method, path, body))
        ok = lambda r: httpx.Response(200, json={"success": True, "result": r})  # noqa: E731
        err = lambda code, status=400: httpx.Response(status, json={"success": False, "error": {"code": code}})  # noqa: E731
        private = not any(path.startswith(x) for x in ("/v2/tickers", "/v2/products/C-", "/v2/products/P-", "/v2/l2orderbook", "/v2/history"))
        if private and not (req.headers.get("api-key") and len(req.headers.get("signature", "")) == 64 and req.headers.get("timestamp", "").isdigit()):
            return err("unauthorized", 401)
        if self.busy and req.method == "POST" and path == "/v2/orders":
            self.busy -= 1
            return httpx.Response(429, json={"success": False, "error": {"code": "rate_limit"}}, headers={"X-RATE-LIMIT-RESET": "10"})
        tick = lambda p: {"symbol": p["symbol"], "product_id": p["id"], "strike_price": str(p["strike"]), "contract_type": p["type"], "spot_price": str(self.spot),  # noqa: E731
                          "mark_price": str(self.q["mark"]), "quotes": {"best_bid": str(self.q["bid"]), "best_ask": str(self.q["ask"]), "bid_size": "5000", "ask_size": "5000"},
                          "price_band": {"lower_limit": "0.1", "upper_limit": "900"}}
        if path == "/v2/tickers":
            return ok([tick(p) for p in self.products.values()])
        if path.startswith("/v2/tickers/"):
            return ok(tick(self.by_symbol[path.rsplit("/", 1)[1]]))
        if path.startswith("/v2/l2orderbook/"):
            return ok({"buy": [{"price": str(self.q["bid"]), "size": 5000}], "sell": [{"price": str(self.q["ask"]), "size": 5000}]})
        if path.startswith("/v2/products/") and path.endswith("/orders/leverage"):
            pid = int(path.split("/")[3])
            self.lev[pid] = body["leverage"]
            return ok({"leverage": body["leverage"], "product_id": pid})
        if path.startswith("/v2/products/"):
            p = self.by_symbol[path.rsplit("/", 1)[1]]
            return ok({"id": p["id"], "symbol": p["symbol"], "tick_size": "0.1", "maker_commission_rate": "0.0001", "taker_commission_rate": "0.0001", "contract_value": "0.001",
                       "state": "live", "trading_status": "operational", "default_leverage": "200", "position_size_limit": 50000, "product_specs": {"premium_commission_rate": 0.035}})
        if path == "/v2/wallet/balances":
            return ok([{"asset_symbol": "INR", "balance": "0", "available_balance": "0"}, {"asset_symbol": "USD", "balance": "900", "available_balance": "850"}])
        if path == "/v2/positions":
            p = self.pos.get(int(req.url.params["product_id"]), {"size": 0, "entry": None})
            return ok({"size": p["size"], "entry_price": p["entry"], "margin": "0.61" if p["size"] else "0", "liquidation_price": "1500" if p["size"] else None})
        if path == "/v2/orders" and req.method == "GET":
            ids = {int(x) for x in req.url.params["product_ids"].split(",")}
            return ok([self._order(o) for o in self.orders.values() if o["pid"] in ids and o["state"] in ("open", "pending")])
        if path == "/v2/orders" and req.method == "POST":
            self.n += 1
            o = {"id": self.n, "pid": body["product_id"], "size": body["size"], "side": body["side"], "filled": 0, "avg": None, "coid": body.get("client_order_id"),
                 "state": "open", "price": body.get("limit_price"), "stop": body.get("stop_order_type")}
            held = self.pos.get(o["pid"], {"size": 0})["size"]
            if o["stop"]:
                o["state"] = "pending"
            else:
                if body.get("reduce_only") == "true":
                    if held >= 0:
                        return err("no_position_for_reduce_only")
                    o["size"] = min(o["size"], -held)
                px = float(body["limit_price"]) if body["order_type"] == "limit_order" else None
                if o["side"] == "sell" and px is not None and px <= self.q["bid"]:
                    self._fill(o, o["size"], self.q["bid"])
                elif o["side"] == "buy" and (px is None or px >= self.q["ask"]):
                    self._fill(o, o["size"], self.q["ask"])
                if o["filled"] == o["size"]:
                    o["state"] = "closed"
                elif body.get("time_in_force") == "ioc" or px is None:
                    o["state"] = "cancelled"
            self.orders[o["id"]] = o
            return ok(self._order(o))
        if path == "/v2/orders" and req.method == "DELETE":
            o = self.orders[body["id"]]
            if o["state"] not in ("open", "pending"):
                return err("order_not_open")
            o["state"] = "cancelled"
            return ok(self._order(o))
        if path == "/v2/orders/all":
            for o in self.orders.values():
                if o["pid"] == body["product_id"] and o["state"] in ("open", "pending"):
                    o["state"] = "cancelled"
            return ok({})
        if path.startswith("/v2/orders/client_order_id/"):
            o = next((o for o in self.orders.values() if o["coid"] == path.rsplit("/", 1)[1]), None)
            return ok(self._order(o)) if o else err("not_found", 404)
        if path.startswith("/v2/orders/"):
            return ok(self._order(self.orders[int(path.rsplit("/", 1)[1])]))
        return err("unknown_route", 404)


@pytest.fixture()
def exchange(env, monkeypatch):
    import delta_broker
    fx = FakeDelta()
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
    env.st.ENGINE.lock = None
    return fx


def test_connection_test_checks_everything_and_leaves_nothing_behind(env, exchange):
    async def go():
        rec = await env.st.connection_test("LIVE", "a@x")
        assert rec["ok"], rec["steps"]
        names = [s["name"] for s in rec["steps"]]
        assert names == ["API key", "Sign in and read the wallet", "Server clock", "Option list and ATM contracts", "Live prices", "Read positions and open orders",
                         "Set order leverage", "Place and cancel a resting order (cannot fill)"]
        assert "$900.00 balance, $850.00 free" in rec["steps"][1]["detail"] and "ATM 61,000" in rec["steps"][3]["detail"]
        o = next(o for o in exchange.orders.values())
        assert (o["side"], o["size"], o["price"], o["state"], o["filled"]) == ("sell", 1, "882", "cancelled", 0)          # 98% of the upper price band: cannot fill
        assert exchange.lev == {504: "100", 505: "100"} and not any(p["size"] for p in exchange.pos.values())
        assert (await env.st.get_state())["conn_ok_LIVE"] == rec["at"]
        assert (await env.st.dashboard())["checks"][0]["kind"] == "connection"
    run(go())


def test_connection_test_names_the_step_that_failed(env, exchange, monkeypatch):
    async def go():
        monkeypatch.setattr(env.sx, "clock_offset_ms", lambda: (240.0, "test clock"))
        rec = await env.st.connection_test("TESTNET", "a@x")
        bad = [s for s in rec["steps"] if not s["ok"]]
        assert not rec["ok"] and [s["name"] for s in bad] == ["Server clock"] and "+240 ms off" in bad[0]["detail"] and "chrony" in bad[0]["detail"]
        assert "conn_ok_TESTNET" not in await env.st.get_state()
        monkeypatch.setattr(env.st, "now_utc", lambda: datetime(2026, 11, 27, 11, 31, tzinfo=UTC))
        with pytest.raises(ValueError, match="Not between 16:45 and 17:40 IST"):
            await env.st.connection_test("TESTNET", "a@x")
    run(go())


def test_testnet_round_trip_runs_the_real_entry_stops_and_exit_through_the_api(env, exchange):
    async def go():
        exchange.busy = 1                                                                 # the first order request is refused for the rate limit and re-sent
        rec = await env.st.round_trip("a@x")
        assert rec["ok"], rec["steps"]
        assert [s["name"] for s in rec["steps"]] == ["Pre-flight", "Arming", "Entry: sell 1 lot of each leg", "Backup stops and position on the exchange",
                                                    "Exit: buy both legs back", "Nothing left behind"]
        assert "2 backup stops resting; position -1/-1" in rec["steps"][3]["detail"] and "bought back at 100.50 + 100.50" in rec["steps"][4]["detail"]
        posts = [b for m, p, b in exchange.calls if m == "POST" and p == "/v2/orders"]
        sells = [b for b in posts if b["side"] == "sell"]
        assert len(sells) == 3 and all(b["size"] == 1 and b["order_type"] == "limit_order" and b["limit_price"] == "100" and b["time_in_force"] == "gtc" for b in sells)
        stops = [b for b in posts if b.get("stop_order_type")]
        assert len(stops) == 2 and all(b["stop_price"] == "600" and b["stop_trigger_method"] == "mark_price" and b["reduce_only"] == "true" and b["side"] == "buy" for b in stops)
        buys = [b for b in posts if b["side"] == "buy" and not b.get("stop_order_type")]
        assert len(buys) == 2 and all(b["time_in_force"] == "ioc" and b["reduce_only"] == "true" and b["limit_price"] == "105.6" for b in buys)     # ask x 1.05, rounded up
        assert set(exchange.lev.values()) == {"93"} and len(exchange.lev) == 6               # leverage set on the 3 nearest strikes, capped for the backup stop
        assert not any(p["size"] for p in exchange.pos.values()) and not any(o["state"] in ("open", "pending") for o in exchange.orders.values())
        st = await env.st.get_state()
        assert st["drill_ok_TESTNET"] == rec["at"]
        d = await env.db.straddle_trades.find_one({"drill": True})
        assert d["status"] == "DRILL" and d["date"] == "2026-11-27"
        assert await env.st.live_journal("TESTNET", env.cfg) == []                         # a rehearsal is never in the journal
        assert (await env.st.dashboard())["ready"]["drill_ok_TESTNET"] == rec["at"]
    run(go())


def test_round_trip_is_waived_only_when_the_practice_exchange_has_no_market(env, exchange):
    async def go():
        exchange.q["bid"] = 40.0                                                          # a market this wide is no market: nothing is sent
        exchange.q["ask"] = 400.0
        rec = await env.st.round_trip("a@x")
        assert not rec["ok"] and rec["waived"] and (await env.st.get_state()).get("drill_waived") is True
        assert "bid-ask spread" in rec["steps"][-1]["detail"] and not any(m == "POST" and p == "/v2/orders" for m, p, _ in exchange.calls)
        assert not any(p["size"] for p in exchange.pos.values())
    run(go())


def test_order_adapter_keeps_one_connection_and_reads_positions_and_orders(env, exchange):
    async def go():
        br = env.sx.DeltaOptions("TESTNET")
        leg = {"symbol": "C-BTC-61000-271126", "product_id": 504, "tick": 0.1}
        assert await br.warm() >= 0 and br._http is br._client()                          # one kept connection, not one per request
        assert await br.balance() == {"equity": 900.0, "available": 850.0}                # the funded USD row wins over the empty INR row
        assert await br.set_leverage(leg, 100.0) == 100.0
        o = await br.place(leg, "sell", 3, 100.0, "gtc", coid="X1")
        assert (o["filled"], o["avg"], o["open"]) == (3, 100.0, False)
        await br.place_stop(leg, 3, 600.0, coid="X2")
        assert await br.positions([leg]) == {leg["symbol"]: -3}
        assert (await br.position_details([leg]))[leg["symbol"]] == {"size": -3, "entry": 100.0, "margin": 0.61, "liquidation": 1500.0}
        assert [(x["symbol"], x["stop"]) for x in await br.open_orders([leg])] == [(leg["symbol"], True)]
        await br.cancel_stops([leg])
        assert await br.open_orders([leg]) == []
        back = await br.place(leg, "buy", 3, None, "ioc", reduce_only=True)
        assert back["filled"] == 3 and await br.positions([leg]) == {leg["symbol"]: 0}
        with pytest.raises(Exception, match="no_position_for_reduce_only"):
            await br.place(leg, "buy", 1, None, "ioc", reduce_only=True)
        await br.close()
        assert br._http is None
    run(go())


def test_database_proxy_never_asks_a_motor_database_for_its_truth_value(env):
    class MotorLike:                                                                      # a real Motor database raises on bool(); mongomock does not
        straddle_state = "the thread's own collection"

        def __bool__(self):
            raise NotImplementedError("Database objects do not implement truth value testing")
    env.st._TLS.db = MotorLike()
    try:
        assert env.st.db.straddle_state == "the thread's own collection"
    finally:
        del env.st._TLS.db
    assert env.st.db.straddle_state is not None                                           # back on the website's connection


def test_bot_thread_runs_the_scheduler_with_its_own_loop_and_database(env, monkeypatch):
    import time
    st = env.st
    seen = []

    async def step():
        seen.append((asyncio.get_running_loop(), st._TLS.db is not None))
        st.ENGINE.ok_at = time.time()
        return 0.01

    async def no_wait(sec):
        pass
    monkeypatch.setattr(st.ENGINE, "step", step)
    real_sleep = asyncio.sleep

    async def fast(sec):
        await real_sleep(0 if sec >= 1 else sec)
    monkeypatch.setattr(st.asyncio, "sleep", fast)
    main = asyncio.new_event_loop()
    t = st.start(main)
    for _ in range(200):
        if len(seen) >= 3:
            break
        time.sleep(0.02)
    time.sleep(0.2)
    assert len(seen) >= 3 and t.is_alive() and t.daemon and t.name == "straddle-bot"
    assert seen[0][0] is not main and seen[0][1] and st.BOT_LOOP is seen[0][0] and st.alive()
    assert st.LAG["day"] is not None
    st.stop()
    t.join(3)
    assert not t.is_alive() and st.BOT_LOOP is None
    main.close()
