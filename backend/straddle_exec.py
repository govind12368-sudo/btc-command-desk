"""Market data and order adapters for the straddle bot (straddle.py).

Checked against Delta Exchange India's own API description (docs.india.delta.exchange, swagger_v2.json) and against live
data on 26 Nov 2026:
  market data   wss://socket.india.delta.exchange – channels v2/ticker (mark, best bid/ask, spot), l2_orderbook (depth),
                spot_price (.DEXBTUSD). REST /v2/tickers and /v2/l2orderbook are the fallback when the socket is stale.
  orders        POST /v2/orders (limit_order / market_order, time_in_force gtc / ioc, reduce_only, client_order_id,
                stop_order_type stop_loss_order + stop_price + stop_trigger_method). DELETE /v2/orders cancels.
  NOT possible  one batch request for both legs: "/v2/orders/batch" takes one contract only and refuses IOC. The two legs
                are therefore sent as two requests at the same instant (asyncio.gather).
  NOT in the API  a margin / order-preview endpoint. Margin is estimated (notional / the contract's leverage) and the
                exchange's own answer to the order is the real check (the size is halved and re-sent when it says no).
"""
import asyncio
import json
import logging
import math
import socket
import struct
import time

import httpx

import delta_broker

log = logging.getLogger("straddle")
WS = {"LIVE": "wss://socket.india.delta.exchange", "TESTNET": "wss://socket-ind.testnet.deltaex.org"}
INDEX = ".DEXBTUSD"


def env_of(mode):
    """PAPER reads the real exchange's prices; TESTNET reads (and trades on) the practice exchange."""
    return "TESTNET" if mode == "TESTNET" else "LIVE"


def tick_round(price, tick, how="near"):
    n = price / tick
    n = math.ceil(n - 1e-9) if how == "up" else math.floor(n + 1e-9) if how == "down" else round(n)
    return max(tick, round(n * tick, 8))


def ntp_offset_ms(hosts=("time.google.com", "time.cloudflare.com", "pool.ntp.org"), timeout=1.5):
    """Clock error of this server in ms (positive = our clock is behind), from one SNTP exchange. None when no time server answers."""
    for host in hosts:
        ms = _sntp(host, timeout)
        if ms is not None:
            return ms
    return None


def _sntp(host, timeout):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            t1 = time.time()
            s.sendto(b"\x1b" + 47 * b"\0", (host, 123))
            data, _ = s.recvfrom(64)
            t4 = time.time()
        if len(data) < 48:
            return None
        unpack = lambda o: struct.unpack("!II", data[o:o + 8])  # noqa: E731
        to_unix = lambda x: x[0] - 2208988800 + x[1] / 2 ** 32  # noqa: E731
        t2, t3 = to_unix(unpack(32)), to_unix(unpack(40))
        return ((t2 - t1) + (t3 - t4)) / 2 * 1000
    except Exception:
        return None


async def public_get(env, path, params=None):
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(delta_broker.BASES[env] + path, params=params, headers={"User-Agent": "MyBTCPartner-straddle", "Accept": "application/json"})
    j = r.json()
    if r.status_code >= 400 or not j.get("success"):
        raise RuntimeError(f"Delta {r.status_code} on {path}")
    return j["result"]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class Feed:
    """Live prices of the option legs and the BTC index. One websocket, auto-reconnect with backoff, REST fallback."""

    def __init__(self, env):
        self.env, self.symbols = env, []
        self.q, self.books = {}, {}
        self.spot, self.spot_at = None, 0.0
        self.updated = asyncio.Event()
        self.connected, self.reconnects, self.offset_ms = False, 0, None
        self._task = None

    # -- reading
    def quote(self, sym):
        return self.q.get(sym)

    def book(self, sym):
        return self.books.get(sym) or {"buy": [], "sell": [], "at": 0.0}

    def age(self, sym):
        q = self.q.get(sym)
        return time.time() - q["at"] if q else float("inf")

    def spot_age(self):
        return time.time() - self.spot_at if self.spot_at else float("inf")

    # -- writing (also used by tests)
    def put_quote(self, sym, mark=None, bid=None, ask=None, bid_size=None, ask_size=None, spot=None, at=None):
        q = self.q.setdefault(sym, {})
        for k, v in (("mark", mark), ("bid", bid), ("ask", ask), ("bid_size", bid_size), ("ask_size", ask_size)):
            if v is not None:
                q[k] = v
        q["at"] = at or time.time()
        if spot is not None:
            self.spot, self.spot_at = spot, q["at"]
        self.updated.set()

    def put_book(self, sym, buy, sell, at=None):
        self.books[sym] = {"buy": sorted(buy, key=lambda x: -x[0]), "sell": sorted(sell, key=lambda x: x[0]), "at": at or time.time()}
        self.updated.set()

    def _on(self, m):
        t = m.get("type")
        if t == "v2/ticker":
            qq = m.get("quotes") or {}
            self.put_quote(m["symbol"], _f(m.get("mark_price")), _f(qq.get("best_bid")), _f(qq.get("best_ask")), _f(qq.get("bid_size")), _f(qq.get("ask_size")), _f(m.get("spot_price")))
            if m.get("timestamp"):
                self.offset_ms = m["timestamp"] / 1000 - time.time() * 1000   # exchange clock minus ours (includes network delay)
        elif t == "l2_orderbook":
            lv = lambda side: [(_f(x.get("limit_price") or x.get("price")), _f(x.get("size"))) for x in m.get(side) or []]  # noqa: E731
            self.put_book(m["symbol"], lv("buy"), lv("sell"))
        elif t == "spot_price" and m.get("symbol") == INDEX and _f(m.get("price")):
            self.spot, self.spot_at = _f(m["price"]), time.time()
            self.updated.set()

    async def rest_refresh(self, symbols=None):
        """Fallback when the socket is stale: tickers + order books over REST."""
        async def one(sym):
            t = await public_get(self.env, f"/v2/tickers/{sym}")
            qq = t.get("quotes") or {}
            self.put_quote(sym, _f(t.get("mark_price")), _f(qq.get("best_bid")), _f(qq.get("best_ask")), _f(qq.get("bid_size")), _f(qq.get("ask_size")), _f(t.get("spot_price")))
            ob = await public_get(self.env, f"/v2/l2orderbook/{sym}")
            self.put_book(sym, [(_f(x["price"]), _f(x["size"])) for x in ob.get("buy") or []], [(_f(x["price"]), _f(x["size"])) for x in ob.get("sell") or []])
        await asyncio.gather(*(one(s) for s in (symbols or self.symbols)), return_exceptions=True)

    async def start(self, symbols):
        self.symbols = list(symbols)
        await self.stop()
        self._task = asyncio.create_task(self._run())
        await self.rest_refresh()

    async def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
        self.connected = False

    async def _run(self):
        import websockets
        wait = 0.5
        while True:
            try:
                async with websockets.connect(WS[self.env], open_timeout=10, ping_interval=15, ping_timeout=10) as ws:
                    await ws.send(json.dumps({"type": "subscribe", "payload": {"channels": [
                        {"name": "v2/ticker", "symbols": self.symbols}, {"name": "l2_orderbook", "symbols": self.symbols}, {"name": "spot_price", "symbols": [INDEX]}]}}))
                    self.connected, wait = True, 0.5
                    async for raw in ws:
                        try:
                            self._on(json.loads(raw))
                        except Exception as e:  # one bad message must not drop the feed
                            log.debug("straddle feed message skipped: %s", e)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("straddle feed %s disconnected: %s", self.env, e)
            self.connected = False
            self.reconnects += 1
            await asyncio.sleep(wait)
            wait = min(wait * 2, 8)


def walk(levels, size, limit=None, buy=True):
    """Fill `size` against book levels [(price, size)], best first, not beyond `limit`. Returns (filled, average price)."""
    got, cost = 0, 0.0
    for price, avail in levels:
        if price is None or not avail:
            continue
        if limit is not None and ((buy and price > limit + 1e-9) or (not buy and price < limit - 1e-9)):
            break
        take = min(size - got, int(avail))
        got += take
        cost += take * price
        if got >= size:
            break
    return got, (cost / got if got else None)


class PaperBroker:
    """Simulated fills at the real bid/ask and depth of the live order book (never at mark). No orders leave the server."""
    name = "PAPER"

    def __init__(self, feed, equity=lambda: 1000.0):
        self.feed, self.equity = feed, equity
        self.orders, self.stops, self.pos, self._n = {}, {}, {}, 0

    async def balance(self):
        e = self.equity()
        return {"equity": e, "available": e}

    def _match(self, o):
        if not o["open"]:
            return
        b = self.feed.book(o["leg"]["symbol"])
        buy = o["side"] == "buy"
        got, avg = walk(b["sell"] if buy else b["buy"], o["size"] - o["filled"], o["price"], buy)
        if got:
            o["avg"] = ((o["avg"] or 0) * o["filled"] + avg * got) / (o["filled"] + got)
            o["filled"] += got
            o["fill_ts"] = int(time.time() * 1000)
            self.pos[o["leg"]["symbol"]] = self.pos.get(o["leg"]["symbol"], 0) + (got if buy else -got)
        if o["filled"] >= o["size"] or o["tif"] == "ioc":
            o["open"] = False

    async def place(self, leg, side, size, price=None, tif="gtc", reduce_only=False, coid=None):
        self._n += 1
        if reduce_only:
            size = min(size, abs(self.pos.get(leg["symbol"], 0)))
        o = {"id": f"paper-{self._n}", "coid": coid, "leg": leg, "side": side, "size": int(size), "price": price, "tif": "ioc" if price is None else tif,
             "filled": 0, "avg": None, "open": size > 0, "latency_ms": 0.0, "fill_ts": None}
        self.orders[o["id"]] = o
        self._match(o)
        return dict(o)

    async def status(self, o):
        cur = self.orders[o["id"]]
        self._match(cur)
        return dict(cur)

    async def cancel(self, o):
        cur = self.orders[o["id"]]
        self._match(cur)
        cur["open"] = False
        return dict(cur)

    async def place_stop(self, leg, size, stop_price, coid=None):
        self.stops[leg["symbol"]] = {"leg": leg, "size": size, "stop": stop_price}
        return f"paper-stop-{leg['symbol']}"

    async def cancel_stops(self, legs):
        for leg in legs:
            self.stops.pop(leg["symbol"], None)

    async def check_stops(self):
        """Exchange-side stop simulation: a leg whose mark reaches its stop is bought back at market."""
        fired = []
        for sym, s in list(self.stops.items()):
            q = self.feed.quote(sym)
            if q and q.get("mark") is not None and q["mark"] >= s["stop"]:
                del self.stops[sym]
                fired.append(await self.place(s["leg"], "buy", s["size"], None, reduce_only=True))
        return fired

    async def positions(self, legs):
        return {leg["symbol"]: self.pos.get(leg["symbol"], 0) for leg in legs}

    async def cancel_all(self, legs):
        for o in self.orders.values():
            if o["leg"]["symbol"] in {x["symbol"] for x in legs}:
                o["open"] = False
        await self.cancel_stops(legs)

    async def leverage(self, leg):
        return None


class DeltaOptions:
    """Real orders on Delta Exchange India (LIVE) or its practice exchange (TESTNET). Signing and keys come from delta_broker."""

    def __init__(self, mode, usd_inr=85.0, transport=None):
        self.name, self.mode, self.usd_inr = mode, mode, usd_inr
        self.api = delta_broker.DeltaBroker(mode, transport=transport)

    async def _timed(self, method, path, body=None, params=None):
        t0 = time.perf_counter()
        j = await self.api._req(method, path, params=params, body=body)
        return j.get("result"), (time.perf_counter() - t0) * 1000

    @staticmethod
    def _shape(o, leg, ms):
        size, unfilled = int(o.get("size") or 0), int(o.get("unfilled_size") or 0)
        return {"id": o.get("id"), "coid": o.get("client_order_id"), "leg": leg, "side": o.get("side"), "size": size, "filled": size - unfilled,
                "avg": _f(o.get("average_fill_price")), "open": o.get("state") in ("open", "pending"), "latency_ms": ms,
                "fill_ts": int(int(o["updated_at"]) / 1000) if str(o.get("updated_at") or "").isdigit() else int(time.time() * 1000), "price": _f(o.get("limit_price"))}

    async def balance(self):
        rows = (await self.api._get("/v2/wallet/balances"))["result"]
        usd = None
        for r in rows or []:
            sym = str(r.get("asset_symbol", "")).upper()
            if sym in ("USD", "USDT", "INR"):
                k = 1.0 if sym != "INR" else 1 / self.usd_inr
                eq, av = _f(r.get("balance")) or 0.0, _f(r.get("available_balance")) or 0.0
                if usd is None or eq * k > usd["equity"]:
                    usd = {"equity": eq * k, "available": av * k}
        if usd is None:
            raise RuntimeError("no USD or INR balance found in the Delta wallet")
        return usd

    async def leverage(self, leg):
        try:
            r = (await self.api._get(f"/v2/products/{leg['product_id']}/orders/leverage"))["result"]
            return _f(r.get("leverage"))
        except Exception:
            return None

    async def place(self, leg, side, size, price=None, tif="gtc", reduce_only=False, coid=None):
        body = {"product_id": leg["product_id"], "size": int(size), "side": side, "order_type": "market_order" if price is None else "limit_order",
                "reduce_only": "true" if reduce_only else "false"}
        if price is not None:
            body.update(limit_price=f"{price:.8f}".rstrip("0").rstrip("."), time_in_force=tif)
        if coid:
            body["client_order_id"] = coid
        try:
            o, ms = await self._timed("POST", "/v2/orders", body)
        except (httpx.HTTPError, asyncio.TimeoutError):
            if not coid:
                raise
            # never re-send blind: the order may have reached the exchange. Look it up by its client id.
            o, ms = await self._timed("GET", f"/v2/orders/client_order_id/{coid}")
        return self._shape(o, leg, ms)

    async def status(self, o):
        r, ms = await self._timed("GET", f"/v2/orders/{o['id']}")
        return self._shape(r, o["leg"], ms)

    async def cancel(self, o):
        try:
            r, ms = await self._timed("DELETE", "/v2/orders", {"id": o["id"], "product_id": o["leg"]["product_id"]})
            return self._shape(r, o["leg"], ms)
        except delta_broker.DeltaError:
            return await self.status(o)       # already filled or cancelled: read the final state

    async def place_stop(self, leg, size, stop_price, coid=None):
        body = {"product_id": leg["product_id"], "size": int(size), "side": "buy", "order_type": "market_order", "stop_order_type": "stop_loss_order",
                "stop_price": f"{stop_price:.8f}".rstrip("0").rstrip("."), "stop_trigger_method": "mark_price", "reduce_only": "true"}
        if coid:
            body["client_order_id"] = coid
        o, _ = await self._timed("POST", "/v2/orders", body)
        return o.get("id")

    async def _open_orders(self, leg):
        return (await self.api._get("/v2/orders", {"product_ids": str(leg["product_id"]), "states": "open,pending"}))["result"] or []

    async def cancel_stops(self, legs):
        for leg in legs:
            for o in await self._open_orders(leg):
                if o.get("stop_order_type"):
                    try:
                        await self.api._req("DELETE", "/v2/orders", body={"id": o["id"], "product_id": leg["product_id"]})
                    except delta_broker.DeltaError:
                        pass

    async def check_stops(self):
        return []                                  # the exchange holds the stops; the engine sees them through positions()

    async def positions(self, legs):
        out = {}
        for leg in legs:
            r = (await self.api._get("/v2/positions", {"product_id": leg["product_id"]}))["result"] or {}
            out[leg["symbol"]] = int(r.get("size") or 0)
        return out

    async def cancel_all(self, legs):
        """Cancel every open order on the two option contracts only (other strategies on the account are not touched)."""
        for leg in legs:
            try:
                await self.api._req("DELETE", "/v2/orders/all", body={"product_id": leg["product_id"], "cancel_limit_orders": "true", "cancel_stop_orders": "true"})
            except delta_broker.DeltaError as e:
                log.warning("straddle cancel-all %s: %s", leg["symbol"], e)
