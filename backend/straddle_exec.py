"""Market data and order adapters for the straddle bot (straddle.py).

Checked against Delta Exchange India's own API description (docs.india.delta.exchange, swagger_v2.json) and against live
data on 26 Nov 2026:
  market data   wss://socket.india.delta.exchange – channels mark_price (mark + price band, about every 2 s),
                l1_orderbook (best bid/ask, about every 1-2 s), l2_orderbook (depth, about every 3 s), spot_price
                (.DEXBTUSD, about every second). v2/ticker is NOT used: it only comes every 5 s.
                REST /v2/tickers and /v2/l2orderbook are the fallback when the socket is stale.
  orders        POST /v2/orders (limit_order / market_order, time_in_force gtc / ioc, reduce_only, client_order_id,
                stop_order_type stop_loss_order + stop_price + stop_trigger_method). DELETE /v2/orders cancels.
                DELETE /v2/orders/all {product_id, cancel_limit_orders, cancel_stop_orders}. GET /v2/positions?product_id=.
                POST /v2/products/{id}/orders/leverage {leverage}.
  connection    one keep-alive HTTPS connection per broker (a new connection costs about 0.5 s, a kept one about 0.05 s);
                it is warmed a few seconds before the entry.
  NOT possible  one batch request for both legs: "/v2/orders/batch" takes one contract only and refuses IOC. The two legs
                are therefore sent as two requests at the same instant (asyncio.gather).
  NOT in the API  a margin / order-preview endpoint. Margin is estimated (notional / order leverage, which the bot sets
                itself) and the exchange's own answer to the order is the real check.
"""
import asyncio
import json
import logging
import math
import socket
import struct
import time
from urllib.parse import urlencode

import httpx

import delta_broker

log = logging.getLogger("straddle")
WS = {"LIVE": "wss://socket.india.delta.exchange", "TESTNET": "wss://socket-ind.testnet.deltaex.org"}
INDEX = ".DEXBTUSD"
UA = "MyBTCPartner-straddle"


def env_of(mode):
    """PAPER reads the real exchange's prices; TESTNET reads (and trades on) the practice exchange."""
    return "TESTNET" if mode == "TESTNET" else "LIVE"


def tick_round(price, tick, how="near"):
    n = price / tick
    n = math.ceil(n - 1e-9) if how == "up" else math.floor(n + 1e-9) if how == "down" else round(n)
    return max(tick, round(n * tick, 8))


def px(price):
    return f"{price:.8f}".rstrip("0").rstrip(".")


# ---------------- clock ----------------
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
        if t2 < 1_600_000_000:
            return None
        return ((t2 - t1) + (t3 - t4)) / 2 * 1000
    except Exception:
        return None


def _https_time(timeout):
    """Fallback when UDP time servers are blocked: Cloudflare's trace page carries its clock in milliseconds."""
    try:
        with httpx.Client(timeout=timeout) as c:
            c.get("https://time.cloudflare.com/cdn-cgi/trace")              # first call opens the connection
            t1 = time.time()
            r = c.get("https://time.cloudflare.com/cdn-cgi/trace")
            t4 = time.time()
        ts = next((float(x[3:]) for x in r.text.splitlines() if x.startswith("ts=")), None)
        if r.status_code != 200 or not ts or t4 - t1 > 0.4:                # a slow answer is not a usable clock
            return None
        return (ts - (t1 + t4) / 2) * 1000
    except Exception:
        return None


def clock_offset_ms(hosts=("time.google.com", "time.cloudflare.com", "pool.ntp.org"), timeout=1.5):
    """(offset in ms, source). Positive = our clock is behind. (None, None) when no time source answers."""
    for host in hosts:
        ms = _sntp(host, timeout)
        if ms is not None:
            return ms, f"time server {host}"
    ms = _https_time(3.0)
    return (ms, "time.cloudflare.com over HTTPS") if ms is not None else (None, None)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------- prices ----------------
class Feed:
    """Live prices of the option legs and the BTC index. One websocket with auto-reconnect, REST fallback on a kept connection."""
    transport = None                                   # tests put a simulated exchange here

    def __init__(self, env):
        self.env, self.symbols = env, []
        self.q, self.books = {}, {}
        self.spot, self.spot_at = None, 0.0
        self.updated = None
        self.connected, self.reconnects = False, 0
        self._task, self._http, self._rest_at = None, None, {}

    def _event(self):
        if self.updated is None:
            self.updated = asyncio.Event()
        return self.updated

    # -- reading
    def quote(self, sym):
        return self.q.get(sym)

    def book(self, sym):
        return self.books.get(sym) or {"buy": [], "sell": [], "at": 0.0}

    def age(self, sym):
        """Seconds since the last MARK price of this contract (the number the stop is checked on)."""
        q = self.q.get(sym)
        return time.time() - q["mark_at"] if q and q.get("mark_at") else float("inf")

    def spot_age(self):
        return time.time() - self.spot_at if self.spot_at else float("inf")

    # -- writing (also used by tests)
    def put_quote(self, sym, mark=None, bid=None, ask=None, bid_size=None, ask_size=None, spot=None, at=None, band=None):
        q, now = self.q.setdefault(sym, {}), at or time.time()
        if mark is not None:
            q["mark"], q["mark_at"] = mark, now
        for k, v in (("bid", bid), ("ask", ask), ("bid_size", bid_size), ("ask_size", ask_size)):
            if v is not None:
                q[k] = v
        if bid is not None or ask is not None:
            q["quote_at"] = now
        if band:
            q["band"] = band
        if spot is not None:
            self.spot, self.spot_at = spot, now
        self._event().set()

    def put_book(self, sym, buy, sell, at=None):
        self.books[sym] = {"buy": sorted([x for x in buy if x[0]], key=lambda x: -x[0]), "sell": sorted([x for x in sell if x[0]], key=lambda x: x[0]), "at": at or time.time()}
        self._event().set()

    def _on(self, m):
        t = m.get("type")
        if t == "mark_price":
            sym = str(m.get("symbol") or "").replace("MARK:", "")
            pb = m.get("price_band") or {}
            self.put_quote(sym, _f(m.get("price")), _f(m.get("best_bid")), _f(m.get("best_ask")), _f(m.get("bid_qty")), _f(m.get("ask_qty")),
                           band=(_f(pb.get("lower_limit")), _f(pb.get("upper_limit"))) if pb else None)
        elif t == "l1_orderbook":
            self.put_quote(m["symbol"], None, _f(m.get("best_bid")), _f(m.get("best_ask")), _f(m.get("bid_qty")), _f(m.get("ask_qty")))
        elif t == "l2_orderbook":
            lv = lambda side: [(_f(x.get("limit_price") or x.get("price")), _f(x.get("size"))) for x in m.get(side) or []]  # noqa: E731
            self.put_book(m["symbol"], lv("buy"), lv("sell"))
        elif t == "spot_price" and m.get("symbol") == INDEX and _f(m.get("price")):
            self.spot, self.spot_at = _f(m["price"]), time.time()
            self._event().set()

    async def get(self, path, params=None):
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=delta_broker.BASES[self.env], timeout=8, transport=self.transport, headers={"User-Agent": UA, "Accept": "application/json"})
        r = await self._http.get(path, params=params)
        j = r.json()
        if r.status_code >= 400 or not j.get("success"):
            raise RuntimeError(f"Delta {r.status_code} on {path}")
        return j["result"]

    async def rest_refresh(self, symbols=None, min_gap=1.0):
        """Fallback when the socket is stale: tickers + order books over REST (at most once a second per contract)."""
        async def one(sym):
            if time.time() - self._rest_at.get(sym, 0) < min_gap:
                return
            self._rest_at[sym] = time.time()
            t = await self.get(f"/v2/tickers/{sym}")
            qq, pb = t.get("quotes") or {}, t.get("price_band") or {}
            self.put_quote(sym, _f(t.get("mark_price")), _f(qq.get("best_bid")), _f(qq.get("best_ask")), _f(qq.get("bid_size")), _f(qq.get("ask_size")), _f(t.get("spot_price")),
                           band=(_f(pb.get("lower_limit")), _f(pb.get("upper_limit"))) if pb else None)
            ob = await self.get(f"/v2/l2orderbook/{sym}")
            self.put_book(sym, [(_f(x["price"]), _f(x["size"])) for x in ob.get("buy") or []], [(_f(x["price"]), _f(x["size"])) for x in ob.get("sell") or []])
        for r in await asyncio.gather(*(one(s) for s in (symbols or self.symbols)), return_exceptions=True):
            if isinstance(r, Exception):
                log.warning("straddle REST price refresh failed: %s", r)

    async def start(self, symbols):
        if self._task:
            self._task.cancel()
        self.symbols = list(symbols)
        self._task = asyncio.create_task(self._run())
        await self.rest_refresh(min_gap=0)

    async def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:
                pass
            self._http = None
        self.connected = False

    async def _run(self):
        import websockets
        wait = 0.5
        while True:
            try:
                async with websockets.connect(WS[self.env], open_timeout=10, ping_interval=10, ping_timeout=8) as ws:
                    await ws.send(json.dumps({"type": "subscribe", "payload": {"channels": [
                        {"name": "mark_price", "symbols": ["MARK:" + s for s in self.symbols]}, {"name": "l1_orderbook", "symbols": self.symbols},
                        {"name": "l2_orderbook", "symbols": self.symbols}, {"name": "spot_price", "symbols": [INDEX]}]}}))
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


# ---------------- brokers ----------------
class PaperBroker:
    """Simulated fills at the real bid/ask and depth of the live order book (never at mark). No orders leave the server."""
    name = "PAPER"

    def __init__(self, feed, equity=lambda: 1000.0):
        self.feed, self.equity = feed, equity
        self.orders, self.stops, self.pos, self.entry, self._n = {}, {}, {}, {}, 0

    async def warm(self):
        return 0.0

    async def close(self):
        pass

    async def balance(self):
        e = self.equity()
        return {"equity": e, "available": e}

    async def set_leverage(self, leg, leverage):
        return leverage

    def _match(self, o):
        if not o["open"]:
            return
        sym, buy = o["leg"]["symbol"], o["side"] == "buy"
        b = self.feed.book(sym)
        got, avg = walk(b["sell"] if buy else b["buy"], o["size"] - o["filled"], o["price"], buy)
        if got:
            o["avg"] = ((o["avg"] or 0) * o["filled"] + avg * got) / (o["filled"] + got)
            o["filled"] += got
            o["fill_ts"] = int(time.time() * 1000)
            self.pos[sym] = self.pos.get(sym, 0) + (got if buy else -got)
            if not buy:
                self.entry[sym] = avg
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

    async def position_details(self, legs):
        return {leg["symbol"]: {"size": self.pos.get(leg["symbol"], 0), "entry": self.entry.get(leg["symbol"]), "margin": None, "liquidation": None} for leg in legs}

    async def open_orders(self, legs):
        syms = {x["symbol"] for x in legs}
        out = [{"id": o["id"], "symbol": o["leg"]["symbol"], "stop": False} for o in self.orders.values() if o["open"] and o["leg"]["symbol"] in syms]
        return out + [{"id": f"paper-stop-{s}", "symbol": s, "stop": True} for s in self.stops if s in syms]

    async def cancel_all(self, legs):
        syms = {x["symbol"] for x in legs}
        for o in self.orders.values():
            if o["leg"]["symbol"] in syms:
                o["open"] = False
        await self.cancel_stops(legs)


class DeltaOptions:
    """Real orders on Delta Exchange India (LIVE) or its practice exchange (TESTNET). Keys and signing come from delta_broker."""
    default_transport = None                           # tests put a simulated exchange here

    def __init__(self, mode, usd_inr=85.0, transport=None):
        self.name, self.mode, self.usd_inr, self.transport = mode, mode, usd_inr, transport or self.default_transport
        self._http = None

    def _client(self):
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=delta_broker.BASES[self.mode], timeout=8, transport=self.transport,
                                           headers={"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"})
        return self._http

    async def close(self):
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:
                pass
            self._http = None

    async def _send(self, method, path, params=None, body=None, auth=True):
        """One signed request on the kept connection. Returns (result, milliseconds). Raises DeltaError on a refusal."""
        qs = ("?" + urlencode(params)) if params else ""
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {}
        if auth:
            k = delta_broker.keys(self.mode)
            if not k:
                raise RuntimeError(f"{self.mode} API key is not set on the server")
            ts = str(int(time.time()))                       # Delta accepts a signature for 5 seconds
            headers = {"api-key": k[0], "timestamp": ts, "signature": delta_broker.sign(k[1], method, ts, path, qs, payload)}
        t0 = time.perf_counter()
        r = await self._client().request(method, path + qs, content=payload or None, headers=headers)
        ms = (time.perf_counter() - t0) * 1000
        j = r.json() if "json" in r.headers.get("content-type", "") else {}
        if r.status_code >= 400 or not j.get("success", False):
            err = j.get("error") if isinstance(j, dict) else None
            detail = (err.get("code") if isinstance(err, dict) else err) or r.text[:160]
            e = delta_broker.DeltaError(r.status_code, detail)
            e.retry_ms = _f(r.headers.get("x-rate-limit-reset"))
            raise e
        return j.get("result"), ms

    async def _req(self, method, path, params=None, body=None, safe=None):
        """Retries what is safe to repeat: every request refused for the rate limit (429, nothing happened on the exchange)
        and reads (GET) that failed on the network or with a server error. An order whose answer is lost is never re-sent here."""
        safe = method == "GET" if safe is None else safe
        last = None
        for i in range(4):
            try:
                return await self._send(method, path, params, body)
            except delta_broker.DeltaError as e:
                if e.status == 429:
                    await asyncio.sleep(min(2.0, (getattr(e, "retry_ms", None) or 300) / 1000))
                elif safe and e.status >= 500:
                    await asyncio.sleep(0.2 * 2 ** i)
                else:
                    raise
                last = e
            except (httpx.HTTPError, asyncio.TimeoutError) as e:
                if not safe:
                    raise
                last = e
                await asyncio.sleep(0.2 * 2 ** i)
        raise last

    async def warm(self):
        """Open the HTTPS connection ahead of the entry. Returns the round trip in ms."""
        _, ms = await self._req("GET", "/v2/wallet/balances")
        return ms

    @staticmethod
    def _shape(o, leg, ms):
        size, unfilled = int(o.get("size") or 0), int(o.get("unfilled_size") or 0)
        return {"id": o.get("id"), "coid": o.get("client_order_id"), "leg": leg, "side": o.get("side"), "size": size, "filled": size - unfilled,
                "avg": _f(o.get("average_fill_price")), "open": o.get("state") in ("open", "pending"), "latency_ms": ms,
                "fill_ts": int(int(o["updated_at"]) / 1000) if str(o.get("updated_at") or "").isdigit() else int(time.time() * 1000), "price": _f(o.get("limit_price"))}

    async def balance(self):
        rows, _ = await self._req("GET", "/v2/wallet/balances")
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

    async def set_leverage(self, leg, leverage):
        """Order leverage of one contract (decides the margin blocked). Returns what the exchange now holds."""
        r, _ = await self._req("POST", f"/v2/products/{leg['product_id']}/orders/leverage", body={"leverage": px(leverage)}, safe=True)
        return _f((r or {}).get("leverage")) or leverage

    async def place(self, leg, side, size, price=None, tif="gtc", reduce_only=False, coid=None):
        body = {"product_id": leg["product_id"], "size": int(size), "side": side, "order_type": "market_order" if price is None else "limit_order",
                "reduce_only": "true" if reduce_only else "false"}
        if price is not None:
            body.update(limit_price=px(price), time_in_force=tif)
        if coid:
            body["client_order_id"] = coid
        try:
            o, ms = await self._req("POST", "/v2/orders", body=body)
        except (httpx.HTTPError, asyncio.TimeoutError):
            if not coid:
                raise
            # never re-send blind: the order may have reached the exchange. Look it up by its client id.
            o, ms = await self._req("GET", f"/v2/orders/client_order_id/{coid}")
        return self._shape(o, leg, ms)

    async def status(self, o):
        r, ms = await self._req("GET", f"/v2/orders/{o['id']}")
        return self._shape(r, o["leg"], ms)

    async def cancel(self, o):
        try:
            r, ms = await self._req("DELETE", "/v2/orders", body={"id": o["id"], "product_id": o["leg"]["product_id"]}, safe=True)
            return self._shape(r, o["leg"], ms)
        except delta_broker.DeltaError:
            return await self.status(o)       # already filled or cancelled: read the final state

    async def place_stop(self, leg, size, stop_price, coid=None):
        body = {"product_id": leg["product_id"], "size": int(size), "side": "buy", "order_type": "market_order", "stop_order_type": "stop_loss_order",
                "stop_price": px(stop_price), "stop_trigger_method": "mark_price", "reduce_only": "true"}
        if coid:
            body["client_order_id"] = coid
        o, _ = await self._req("POST", "/v2/orders", body=body)
        return o.get("id")

    async def open_orders(self, legs):
        by = {leg["product_id"]: leg["symbol"] for leg in legs}
        rows, _ = await self._req("GET", "/v2/orders", params={"product_ids": ",".join(str(i) for i in by), "states": "open,pending"})
        return [{"id": o.get("id"), "symbol": by.get(o.get("product_id")) or o.get("product_symbol"), "stop": bool(o.get("stop_order_type")), "product_id": o.get("product_id")} for o in rows or []]

    async def cancel_stops(self, legs):
        for o in await self.open_orders(legs):
            if o["stop"]:
                try:
                    await self._req("DELETE", "/v2/orders", body={"id": o["id"], "product_id": o["product_id"]}, safe=True)
                except delta_broker.DeltaError:
                    pass

    async def check_stops(self):
        return []                                  # the exchange holds the stops; the engine sees them through positions()

    async def position_details(self, legs):
        async def one(leg):
            r, _ = await self._req("GET", "/v2/positions", params={"product_id": leg["product_id"]})
            r = r or {}
            return leg["symbol"], {"size": int(r.get("size") or 0), "entry": _f(r.get("entry_price")), "margin": _f(r.get("margin")), "liquidation": _f(r.get("liquidation_price"))}
        return dict(await asyncio.gather(*(one(leg) for leg in legs)))

    async def positions(self, legs):
        return {k: v["size"] for k, v in (await self.position_details(legs)).items()}

    async def cancel_all(self, legs):
        """Cancel every open order on the two option contracts only (other strategies on the account are not touched)."""
        for leg in legs:
            try:
                await self._req("DELETE", "/v2/orders/all", body={"product_id": leg["product_id"], "cancel_limit_orders": "true", "cancel_stop_orders": "true"}, safe=True)
            except delta_broker.DeltaError as e:
                log.warning("straddle cancel-all %s: %s", leg["symbol"], e)
