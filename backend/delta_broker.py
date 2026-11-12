"""Delta Exchange India order adapter for auto-trade (TESTNET and LIVE only – PAPER never comes here).

Written against https://docs.delta.exchange (India): signed REST, market entry with bracket stop/target,
real-time position, fills, cancel-all, leverage. API keys are read ONLY from the server environment
(never stored in the database, never sent to the browser, never logged):
    LIVE     DELTA_TRADE_API_KEY / DELTA_TRADE_API_SECRET        (Trading permission only, server IP whitelisted)
    TESTNET  DELTA_TESTNET_API_KEY / DELTA_TESTNET_API_SECRET

Order safety: an entry is never re-sent. If the outcome of the POST is unknown, the order is looked up by
its client_order_id (and the position is checked) before anything else happens.
"""
import asyncio
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode

import httpx

from autotrade import NotPlaced, CV, TICK
from core import parse_iso

BASES = {"LIVE": "https://api.india.delta.exchange", "TESTNET": "https://cdn-ind.testnet.deltaex.org"}
ENV = {"LIVE": ("DELTA_TRADE_API_KEY", "DELTA_TRADE_API_SECRET"), "TESTNET": ("DELTA_TESTNET_API_KEY", "DELTA_TESTNET_API_SECRET")}
SYMBOL = "BTCUSD"


def keys(mode):
    k, s = (os.environ.get(x, "").strip() for x in ENV[mode])
    return (k, s) if k and s else None


def sign(secret, method, ts, path, qs, body):
    """HMAC-SHA256 hex of METHOD + timestamp + path + query string (with '?') + body."""
    return hmac.new(secret.encode(), (method + ts + path + qs + body).encode(), hashlib.sha256).hexdigest()


class DeltaError(Exception):
    def __init__(self, status, detail):
        self.status = status
        super().__init__(f"Delta {status}: {detail}")


def _px(x):
    return f"{round(x / TICK) * TICK:.1f}"


class DeltaBroker:
    def __init__(self, mode, transport=None):
        self.mode, self.base, self.transport = mode, BASES[mode], transport
        self.name = mode
        self._product = None
        self._prepared = None
        self._polled = 0.0
        self.notes = []

    # ---- transport
    async def _req(self, method, path, params=None, body=None, auth=True):
        qs = ("?" + urlencode(params)) if params else ""
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {"User-Agent": "VantageBTC-autotrade", "Content-Type": "application/json", "Accept": "application/json"}
        if auth:
            k = keys(self.mode)
            if not k:
                raise RuntimeError(f"{self.mode} API key is not set on the server")
            ts = str(int(time.time()))  # Delta accepts a signature for 5 seconds
            headers.update({"api-key": k[0], "timestamp": ts, "signature": sign(k[1], method, ts, path, qs, payload)})
        async with httpx.AsyncClient(timeout=10, transport=self.transport) as c:
            r = await c.request(method, self.base + path + qs, content=payload or None, headers=headers)
        j = r.json() if "json" in r.headers.get("content-type", "") else {}
        if r.status_code >= 400 or not j.get("success", False):
            err = j.get("error") if isinstance(j, dict) else None
            raise DeltaError(r.status_code, (err.get("code") if isinstance(err, dict) else err) or r.text[:160])
        return j

    async def _get(self, path, params=None, auth=True):
        """Reads are safe to retry: back off on network errors, 429 and 5xx."""
        last = None
        for i in range(3):
            try:
                return await self._req("GET", path, params=params, auth=auth)
            except DeltaError as e:
                if e.status != 429 and e.status < 500:
                    raise
                last = e
            except httpx.HTTPError as e:
                last = e
            await asyncio.sleep(0.5 * 2 ** i)
        raise last

    # ---- account
    async def product(self):
        """Product is looked up by symbol (the ID differs between production and testnet) and its
        contract size / tick are verified before anything is traded."""
        if not self._product:
            p = (await self._get(f"/v2/products/{SYMBOL}", auth=False))["result"]
            if abs(float(p["contract_value"]) - CV) > 1e-12 or abs(float(p["tick_size"]) - TICK) > 1e-9:
                raise RuntimeError(f"{SYMBOL} contract changed (value {p['contract_value']}, tick {p['tick_size']}) – auto-trade stopped")
            self._product = {"id": int(p["id"]), "symbol": p["symbol"]}
        return self._product

    async def available_inr(self, cfg):
        rows = (await self._get("/v2/wallet/balances"))["result"]
        for sym in ("INR", "USD", "USDT"):
            for r in rows:
                if str(r.get("asset_symbol", "")).upper() == sym:
                    if r.get("available_balance_inr") not in (None, ""):
                        return float(r["available_balance_inr"])
                    v = float(r.get("available_balance") or 0)
                    return v if sym == "INR" else v * cfg["usd_inr"]
        raise RuntimeError("no INR/USD balance found in the Delta wallet")

    async def prepare(self, cfg):
        """Isolated margin + leverage, set and verified before the first trade of each IST day."""
        from core import ist_date
        key = (ist_date(), int(cfg["leverage"]))
        if self._prepared == key:
            return
        p = await self.product()
        try:
            await self._req("PUT", "/v2/users/margin_mode", body={"margin_mode": "isolated"})
        except DeltaError as e:  # typically "already isolated"; shown on the dashboard, not fatal
            self.notes.append(f"margin mode call: {e}")
        await self._req("POST", f"/v2/products/{p['id']}/orders/leverage", body={"leverage": str(int(cfg["leverage"]))})
        got = (await self._get(f"/v2/products/{p['id']}/orders/leverage"))["result"]
        if int(float(got["leverage"])) != int(cfg["leverage"]):
            raise RuntimeError(f"leverage is {got['leverage']}x on Delta, expected {int(cfg['leverage'])}x")
        self._prepared = key

    async def preflight(self, cfg):
        """Run when an admin switches to this mode: proves the key works from this server and the account is clean."""
        await self.product()
        bal = await self.available_inr(cfg)  # fails here if the key is wrong or the server IP is not whitelisted
        if await self.position():
            raise ValueError("The Delta account already has an open BTCUSD position – close it first")
        if await self._open_orders():
            raise ValueError("The Delta account has open BTCUSD orders – cancel them first")
        await self.prepare(cfg)
        return {"available_inr": bal}

    async def position(self):
        p = await self.product()
        r = (await self._get("/v2/positions", {"product_id": p["id"]}))["result"] or {}
        sz = int(float(r.get("size") or 0))
        return {"size": sz, "entry": float(r.get("entry_price") or 0)} if sz else None

    async def _open_orders(self):
        p = await self.product()
        return (await self._get("/v2/orders", {"product_ids": p["id"], "states": "open,pending"}))["result"] or []

    # ---- orders
    @staticmethod
    def _filled(o):
        n = int(float(o.get("size") or 0)) - int(float(o.get("unfilled_size") or 0))
        return {"price": float(o.get("average_fill_price") or 0), "contracts": n, "role": "taker", "order_id": o.get("id")}

    async def find_entry(self, coid):
        """Entry order by client_order_id. NotPlaced only when the exchange says it does not exist AND there is no position."""
        for i in range(3):
            try:
                r = self._filled((await self._req("GET", f"/v2/orders/client_order_id/{coid}"))["result"])
                if not r["price"] and r["contracts"]:
                    pos = await self.position()
                    r["price"] = pos["entry"] if pos else 0
                return r
            except DeltaError as e:
                if e.status == 404:
                    pos = await self.position()
                    if pos:
                        return {"price": pos["entry"], "contracts": abs(pos["size"]), "role": "taker"}
                    raise NotPlaced("exchange has no such order and no position")
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1 + i)
        raise RuntimeError("could not reach Delta to check the entry order")

    async def enter(self, side, contracts, stop, target, coid, price, cfg):
        p = await self.product()
        body = {"product_id": p["id"], "size": int(contracts), "side": side, "order_type": "market_order", "client_order_id": coid,
                "bracket_stop_loss_price": _px(stop), "bracket_take_profit_price": _px(target), "bracket_stop_trigger_method": "last_traded_price"}
        if cfg.get("tp_limit"):
            body["bracket_take_profit_limit_price"] = _px(target)
        try:
            r = self._filled((await self._req("POST", "/v2/orders", body=body))["result"])
        except DeltaError as e:
            if e.status == 429 or e.status < 500:
                raise NotPlaced(str(e))  # rejected: nothing was placed
            return await self.find_entry(coid)
        except httpx.HTTPError:
            return await self.find_entry(coid)  # outcome unknown → look it up, never re-send
        if r["contracts"] and not r["price"]:
            pos = await self.position()
            r["price"] = pos["entry"] if pos else price
        return r

    async def confirm_bracket(self, trade, timeout=5):
        want = "sell" if trade["bot_dir"] == "LONG" else "buy"
        end = time.monotonic() + timeout
        while True:
            try:
                kinds = {o.get("stop_order_type") for o in await self._open_orders() if o.get("side") == want}
                if {"stop_loss_order", "take_profit_order"} <= kinds:
                    return True
            except Exception:
                pass
            if time.monotonic() >= end:
                return False
            await asyncio.sleep(0.7)

    async def resolve_exit(self, t, price):
        """Position is flat: read the closing fills to get the real exit price and which leg fired."""
        p = await self.product()
        side = "sell" if t["bot_dir"] == "LONG" else "buy"
        start = int(parse_iso(t["fill_time"]).timestamp() * 1e6) + 1
        for _ in range(3):
            try:
                rows = (await self._get("/v2/fills", {"product_ids": p["id"], "start_time": start, "page_size": 50}))["result"] or []
            except Exception:
                rows = []
            ex = [f for f in rows if f.get("side") == side]
            qty = sum(float(f["size"]) for f in ex)
            if qty:
                px = sum(float(f["price"]) * float(f["size"]) for f in ex) / qty
                role = "maker" if all(f.get("role") == "maker" for f in ex) else "taker"
                return {"reason": "TARGET" if abs(px - t["target"]) < abs(px - t["stop"]) else "STOP", "price": px, "role": role}
            await asyncio.sleep(1)
        near_t = abs(price - t["target"]) < abs(price - t["stop"])  # fills not available yet: nearest leg, flagged as taker
        return {"reason": "TARGET" if near_t else "STOP", "price": t["target"] if near_t else t["stop"], "role": "taker"}

    async def poll_exit(self, t, price, hi, lo, cfg):
        if time.monotonic() - self._polled < 2:
            return None
        self._polled = time.monotonic()
        if await self.position():
            return None
        return await self.resolve_exit(t, price)

    async def cancel_all(self):
        p = await self.product()
        await self._req("DELETE", "/v2/orders/all", body={"product_id": p["id"], "cancel_limit_orders": True, "cancel_stop_orders": True, "cancel_reduce_only_orders": True})

    async def close(self, t, coid, price):
        """Market-close whatever is really open on the exchange (reduce-only), then the caller cancels leftovers."""
        p = await self.product()
        pos = await self.position()
        if not pos:
            return await self.resolve_exit(t, price)  # the bracket got there first
        body = {"product_id": p["id"], "size": abs(pos["size"]), "side": "sell" if pos["size"] > 0 else "buy",
                "order_type": "market_order", "reduce_only": True, "client_order_id": coid}
        try:
            o = (await self._req("POST", "/v2/orders", body=body))["result"]
        except DeltaError as e:
            if e.status >= 500:
                raise
            await self.cancel_all()  # bracket legs may be holding the size: cancel them, then close
            o = (await self._req("POST", "/v2/orders", body={**body, "client_order_id": (coid + "b")[:32]}))["result"]
        return {"price": float(o.get("average_fill_price") or 0) or price, "role": "taker"}


_BROKERS = {}


def get(mode):
    if mode not in _BROKERS:
        _BROKERS[mode] = DeltaBroker(mode)
    return _BROKERS[mode]
