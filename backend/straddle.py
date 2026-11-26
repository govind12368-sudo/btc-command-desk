"""Straddle bot (owner's spec, 26 Nov 2026): SELL the ATM call + ATM put of BTC's same-day option on Delta Exchange India
at 17:00:00 IST, buy both back when call + put >= stop multiple x P, otherwise hold to the 17:30 IST expiry.

  16:55 IST  pre-flight: clock, option list, the strikes around spot, live prices, balance and margin estimate
  17:00:00   entry: both legs sent at the same instant as limit SELLs at mark; re-priced toward the bid every 3 s in 0.5%
             steps, never below mark x (1 - max entry slip); cancelled at the deadline. A single filled leg is bought back
             at once (never a naked leg); uneven fills are evened out.
  after fill exchange-side stop-market BUY on each leg at disaster multiple x P (protection if this server dies)
  17:01-29   stop: "parity" = the mark at each 1-minute close (the backtest's rule), "tick" = every price update.
             Exit: IOC limit BUYs at ask x (1 + max exit slip) every 0.5 s, market orders after 5 s. Being flat beats price.
  17:30      expiry: the settlement price is read from the contract's record; the in-the-money leg pays |settle - strike|.

Modes: PAPER (default, fills simulated at the real bid/ask and depth) · TESTNET · LIVE (typed confirmation, 30 paper days first).
Hard guards: kill switch, daily max loss, drawdown / losing-streak / slippage halts (manual re-enable), max lots, skip rules.
The maths is in straddle_core.py, market data and orders in straddle_exec.py. Admin only.
"""
import asyncio
import csv
import io
import logging
import time
from datetime import datetime, timedelta, timezone

import straddle_core as sc
import straddle_exec as sx
from core import db, iso, now_utc, IST, APP_NAME

log = logging.getLogger("straddle")
MODES = ("PAPER", "TESTNET", "LIVE")
PRE_T, ENTRY_T, EXPIRY_T = (11, 25), (11, 30), (12, 0)          # UTC = 16:55, 17:00, 17:30 IST
REPRICE_SEC, REPRICE_STEP = 3.0, 0.005
EXIT_RETRY_SEC, EXIT_MARKET_AFTER = 0.5, 5.0
MAX_DRIFT_MS, PAPER_DAYS_BEFORE_LIVE, LIVE_SMALL_LEVERAGE = 100.0, 30, 5.0
ACTIVE = ("ENTERING", "OPEN", "EXITING", "SETTLING")

DEFAULTS = {"starting_capital": 1000.0, "leverage": 25.0, "sizing_mode": "fixed", "stop_multiple": 2.0, "stop_mode": "parity",
            "max_entry_slip": 2.0, "max_exit_slip": 5.0, "entry_deadline_sec": 30.0, "disaster_multiple": 3.0, "max_daily_loss_pct": 15.0,
            "max_dd_pct": 25.0, "max_losing_streak": 6.0, "max_lots": 500.0, "gst": 18.0, "max_spread_pct": 10.0, "max_stale_sec": 2.0,
            "slip_halt_days": 3.0, "live_small_size": 1.0, "usd_inr": 85.0, "alerts": 1.0}
BOUNDS = {"starting_capital": (10, 10_000_000), "leverage": (0.1, 100), "stop_multiple": (1.05, 10), "max_entry_slip": (0, 50), "max_exit_slip": (0, 100),
          "entry_deadline_sec": (5, 120), "disaster_multiple": (1.5, 20), "max_daily_loss_pct": (1, 100), "max_dd_pct": (1, 100), "max_losing_streak": (1, 100),
          "max_lots": (1, 50000), "gst": (0, 50), "max_spread_pct": (0.5, 100), "max_stale_sec": (0.5, 30), "slip_halt_days": (1, 30), "live_small_size": (0, 1),
          "usd_inr": (50, 200), "alerts": (0, 1)}
CHOICES = {"sizing_mode": ("fixed", "compounded"), "stop_mode": ("parity", "tick")}


def clean_settings(patch):
    out = {}
    for k, v in (patch or {}).items():
        if k in CHOICES:
            if v not in CHOICES[k]:
                raise ValueError(f"{k} must be one of: {', '.join(CHOICES[k])}")
            out[k] = v
        elif k in BOUNDS:
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a number")
            lo, hi = BOUNDS[k]
            if not lo <= v <= hi:
                raise ValueError(f"{k} must be between {lo:g} and {hi:g}")
            out[k] = v
    return out


async def settings():
    doc = await db.straddle_settings.find_one({"_id": "settings"}) or {}
    return {**DEFAULTS, **{k: doc[k] for k in DEFAULTS if k in doc}}


async def save_settings(patch, by):
    cur, new = await settings(), clean_settings(patch)
    changed = {k: v for k, v in new.items() if cur[k] != v}
    if changed:
        await db.straddle_settings.update_one({"_id": "settings"}, {"$set": changed}, upsert=True)
        await db.straddle_changes.insert_many([{"at": iso(now_utc()), "by": by, "key": k, "old": cur[k], "new": v} for k, v in changed.items()])
    return await settings()


async def get_state():
    st = await db.straddle_state.find_one({"_id": "state"})
    if not st:
        st = {"_id": "state", "mode": "PAPER", "enabled": True, "halted": False, "halt_reason": None}
        await db.straddle_state.insert_one(st)
    return st


async def set_state(**kw):
    await db.straddle_state.update_one({"_id": "state"}, {"$set": kw}, upsert=True)


def core_cfg(cfg, mode="PAPER"):
    lev = min(cfg["leverage"], LIVE_SMALL_LEVERAGE) if mode == "LIVE" and cfg["live_small_size"] else cfg["leverage"]
    return {"starting_capital": cfg["starting_capital"], "leverage": lev, "gst": cfg["gst"] / 100, "stop_multiple": cfg["stop_multiple"]}


def _ist(t):
    return t.astimezone(IST)


def _at(day, hm, sec=0):
    return datetime(day.year, day.month, day.day, hm[0], hm[1], sec, tzinfo=timezone.utc)


async def mode_days(mode):
    return [d async for d in db.straddle_trades.find({"mode": mode, "status": {"$in": ["CLOSED", "SKIPPED"]}}).sort("date", 1)]


async def live_journal(mode, cfg):
    return sc.journal(await mode_days(mode), core_cfg(cfg, mode), cfg["sizing_mode"])


async def equity_now(mode, cfg):
    rows = await live_journal(mode, cfg)
    return rows[-1]["equity"] if rows else cfg["starting_capital"]


# ---------------- backtest (the owner's workbook) ----------------
_BT = {"key": None, "out": None}


async def backtest_data():
    doc = await db.straddle_backtest.find_one({"_id": "data"})
    return (doc["data"], doc.get("at")) if doc else (sc.load_seed(), "bundled")


async def backtest():
    """Journals, metrics, monthly tables and curves of the four workbook variants (cached until a new workbook is imported)."""
    data, key = await backtest_data()
    if _BT["key"] != key:
        out = {"source": data.get("source"), "inputs": data["inputs"], "variants": {}}
        for v in sc.VARIANTS:
            rows = sc.backtest(data, v)
            start = data["inputs"]["starting_capital"]
            out["variants"][v] = {"name": sc.VARIANT_NAMES[v], "rows": rows, "metrics": sc.metrics(rows, start, rows[0]["date"], rows[-1]["date"]),
                                  "monthly": sc.monthly(rows, start), "curve": sc.curve(rows)}
        _BT.update(key=key, out=out)
    return _BT["out"]


async def import_workbook(blob, name, by):
    data = sc.parse_workbook(blob)
    data["source"] = f"{name} (imported by {by}, {_ist(now_utc()).strftime('%d %b %Y %H:%M IST')})"
    await db.straddle_backtest.update_one({"_id": "data"}, {"$set": {"data": data, "at": iso(now_utc())}}, upsert=True)
    return {"days": len(data["mark"]), "source": data["source"]}


# ---------------- alerts / events ----------------
async def event(text, level="info", alert=None, mode=None, cfg=None):
    (log.warning if level != "info" else log.info)("straddle: %s", text)
    await db.straddle_events.insert_one({"at": iso(now_utc()), "text": text, "level": level, "mode": mode})
    if alert and (cfg or {}).get("alerts", 1):
        try:
            import autotrade
            if mode == "PAPER":                      # paper alerts follow the site's alert switch, like Auto-Trade; testnet and live always send
                import alert_mode
                if not (await alert_mode.get_mode()).get("effective_armed"):
                    return
            t = asyncio.create_task(autotrade._deliver(f"[{mode or 'BOT'}] {APP_NAME} straddle: {alert}", text))
            _BG.add(t)
            t.add_done_callback(_BG.discard)
        except Exception as e:  # alerts must never break the bot
            log.warning("straddle alert error: %s", e)


_BG = set()


class Skip(Exception):
    pass


# ---------------- engine ----------------
class Engine:
    def __init__(self, clock=now_utc, sleep=asyncio.sleep, feed_factory=sx.Feed, broker_factory=None):
        self.clock, self.sleep, self.feed_factory = clock, sleep, feed_factory
        self.broker_factory = broker_factory or self._broker
        self.feed, self.broker, self.kill, self.view, self.heartbeat = None, None, False, {}, 0.0
        self.drift_ms, self.lock, self.mono = None, asyncio.Lock(), time.monotonic

    async def wait_update(self, timeout):
        """Sleep until the next price update or `timeout` seconds, whichever is first."""
        self.feed.updated.clear()
        try:
            await asyncio.wait_for(self.feed.updated.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    def _broker(self, mode, feed, cfg):
        if mode == "PAPER":
            return sx.PaperBroker(feed)
        return sx.DeltaOptions(mode, cfg["usd_inr"])

    async def _save(self, doc, **kw):
        doc.update(kw)
        await db.straddle_trades.update_one({"_id": doc["_id"]}, {"$set": {k: v for k, v in doc.items() if k != "_id"}}, upsert=True)

    async def _skip(self, doc, reason, cfg, alert=True):
        await self._save(doc, status="SKIPPED", skip_reason=reason)
        self.view = {"phase": "SKIPPED", "reason": reason, "date": doc["date"]}
        await event(f"{doc['date']} skipped – {reason}", "warn", "Day skipped" if alert and "switched off" not in reason else None, doc["mode"], cfg)

    # ---- 16:55 pre-flight
    async def clock_drift(self):
        ms = await asyncio.get_running_loop().run_in_executor(None, sx.ntp_offset_ms)
        self.drift_ms = ms
        return ms

    async def chain(self, env, day):
        rows = await sx.public_get(env, "/v2/tickers", {"contract_types": "call_options,put_options", "underlying_asset_symbols": "BTC", "expiry_date": day.strftime("%d-%m-%Y")})
        by = {}
        for r in rows:
            k = sx._f(r.get("strike_price"))
            if k is not None:
                by.setdefault(k, {})["call" if r.get("contract_type") == "call_options" else "put"] = r
        return {k: v for k, v in by.items() if "call" in v and "put" in v}

    async def leg_info(self, env, ticker):
        p = await sx.public_get(env, f"/v2/products/{ticker['symbol']}")
        specs = p.get("product_specs") or {}
        return {"symbol": p["symbol"], "product_id": p["id"], "tick": float(p.get("tick_size") or 0.1), "maker": float(p.get("maker_commission_rate") or 0),
                "taker": float(p.get("taker_commission_rate") or 0), "cap": float(specs.get("premium_commission_rate") or 0),
                "lot": float(p.get("contract_value") or sc.LOT), "tradable": p.get("state") == "live" and p.get("trading_status") == "operational",
                "lev": sx._f(p.get("default_leverage")), "size_limit": int(p.get("position_size_limit") or 0)}

    async def preflight(self, doc, st, cfg):
        """Returns {strike: {'call': leg, 'put': leg}} for the strikes around spot, with the feed running. Raises Skip."""
        mode, env, day = doc["mode"], sx.env_of(doc["mode"]), self.clock().date()
        if not st.get("enabled", True):
            raise Skip("bot is switched off")
        if st.get("halted"):
            raise Skip(f"trading is halted: {st.get('halt_reason')}")
        ms = await self.clock_drift()
        if ms is not None and abs(ms) > MAX_DRIFT_MS:
            raise Skip(f"server clock is off by {ms:.0f} ms (limit {MAX_DRIFT_MS:.0f} ms)")
        if ms is None and mode != "PAPER":
            raise Skip("server clock could not be checked against a time server")
        try:
            chain = await self.chain(env, day)
        except Exception as e:
            raise Skip(f"option list could not be read: {e}")
        if not chain:
            raise Skip("no BTC option pair expiring today is listed")
        spot = sx._f(next(iter(chain.values()))["call"].get("spot_price"))
        near = sorted(chain, key=lambda k: abs(k - spot))[:5]
        legs = {}
        for k in near:
            c, p = await asyncio.gather(self.leg_info(env, chain[k]["call"]), self.leg_info(env, chain[k]["put"]))
            legs[k] = {"call": c, "put": p}
        self.feed = self.feed_factory(env)
        await self.feed.start([x["symbol"] for v in legs.values() for x in v.values()])
        self.broker = self.broker_factory(mode, self.feed, cfg)
        if mode == "PAPER":
            eq = await equity_now(mode, cfg)
            self.broker.equity = lambda: eq
        try:
            bal = await self.broker.balance()
        except Exception as e:
            raise Skip(f"account balance could not be read: {e}")
        doc["wallet_equity"] = bal["equity"]
        await self._save(doc, status="PREFLIGHT", preflight_at=iso(self.clock()), clock_drift_ms=ms, wallet_available=bal["available"])
        self.view = {"phase": "PREFLIGHT", "date": doc["date"], "spot": spot, "strikes": near, "drift_ms": ms, "available": bal["available"]}
        return legs

    def plan_size(self, cfg, mode, sizing_equity, spot, pair, available):
        """Lots per leg after the caps. Returns (lots, notes)."""
        cc = core_cfg(cfg, mode)
        want = sc.lots_for(sizing_equity, cc["leverage"], spot)
        lots, notes = want, {"wanted": want, "liquidity_cap": False, "size_reduced": False, "why": []}
        if lots > cfg["max_lots"]:
            lots = int(cfg["max_lots"])
            notes["why"].append(f"max lots {lots}")
        floor_frac = 1 - cfg["max_entry_slip"] / 100
        for side in ("call", "put"):
            leg = pair[side]
            q, b = self.feed.quote(leg["symbol"]) or {}, self.feed.book(leg["symbol"])
            depth = sum(int(s) for p, s in b["buy"] if p is not None and q.get("mark") and p >= q["mark"] * floor_frac - 1e-9)
            if depth < lots:
                lots, notes["liquidity_cap"] = depth, True
                notes["why"].append(f"{side} bid depth {depth} lots within {cfg['max_entry_slip']:g}%")
            if leg.get("size_limit") and lots > leg["size_limit"]:
                lots = leg["size_limit"]
        lev = min((pair[s].get("lev") or 0) for s in ("call", "put")) or None
        if lev and available is not None:
            per_lot = 2 * spot * sc.LOT / lev                       # both legs; an estimate – the exchange's answer to the order is the real check
            can = int(available / per_lot) if per_lot > 0 else lots
            notes["margin_estimate"] = lots * per_lot
            if can < lots:
                lots, notes["size_reduced"] = can, True
                notes["why"].append(f"margin: about ${per_lot:.2f} a lot against ${available:,.2f} free")
        if lots < want and not notes["liquidity_cap"] and not notes["size_reduced"]:
            notes["size_reduced"] = True
        return max(lots, 0), notes

    # ---- 17:00 entry
    async def enter(self, doc, legs, cfg):
        """Sell both legs. Returns True when a hedged position is open. Raises Skip (flat) otherwise."""
        mode, feed, br = doc["mode"], self.feed, self.broker
        if feed.spot is None or feed.spot_age() > cfg["max_stale_sec"]:
            await feed.rest_refresh()
        spot = feed.spot
        if spot is None:
            raise Skip("no BTC index price")
        strike = min(legs, key=lambda k: abs(k - spot))
        pair = legs[strike]
        call, put = pair["call"], pair["put"]
        for leg in (call, put):
            if feed.age(leg["symbol"]) > cfg["max_stale_sec"]:
                await feed.rest_refresh([leg["symbol"]])
        mk = {}
        for name, leg in (("call", call), ("put", put)):
            q = feed.quote(leg["symbol"]) or {}
            if not leg["tradable"]:
                raise Skip(f"the ATM {name} is not tradable")
            if feed.age(leg["symbol"]) > cfg["max_stale_sec"]:
                raise Skip(f"{name} price data is older than {cfg['max_stale_sec']:g} s")
            if not q.get("mark") or not q.get("bid") or not q.get("ask"):
                raise Skip(f"the ATM {name} has no two-sided market")
            if (q["ask"] - q["bid"]) / q["mark"] * 100 > cfg["max_spread_pct"]:
                raise Skip(f"{name} bid-ask spread is {(q['ask'] - q['bid']) / q['mark'] * 100:.1f}% of mark (limit {cfg['max_spread_pct']:g}%)")
            mk[name] = q["mark"]
        sizing_eq = cfg["starting_capital"] if cfg["sizing_mode"] == "fixed" else await equity_now(mode, cfg)
        try:
            bal = await br.balance()
        except Exception as e:
            raise Skip(f"margin check failed: {e}")
        lots, notes = self.plan_size(cfg, mode, sizing_eq, spot, pair, bal["available"])
        if lots < 1:
            raise Skip("size is below 1 lot after the caps: " + "; ".join(notes["why"] or ["equity too small"]))
        tag = doc["date"].replace("-", "")[2:] + mode[0]
        await self._save(doc, status="ENTERING", spot=spot, strike=strike, call=call["symbol"], put=put["symbol"], maker=call["maker"], taker=call["taker"], cap=call["cap"],
                         mark_call=mk["call"], mark_put=mk["put"], lots_wanted=notes["wanted"], liquidity_cap=notes["liquidity_cap"], size_reduced=notes["size_reduced"],
                         size_notes=notes["why"], margin_estimate=notes.get("margin_estimate"), legs={"call": call, "put": put}, stop_multiple=cfg["stop_multiple"],
                         sizing_equity_used=sizing_eq)
        if notes["size_reduced"] or notes["liquidity_cap"]:
            await event(f"SIZE REDUCED – deviates from backtest: {lots} lots a leg instead of {notes['wanted']} ({'; '.join(notes['why'])})", "warn", "Size reduced", mode, cfg)
        t0, floor = self.mono(), {n: mk[n] * (1 - cfg["max_entry_slip"] / 100) for n in mk}
        orders, fills, lat, n = {}, {"call": [0, 0.0], "put": [0, 0.0]}, [], 0

        async def send(name, leg, size, price, k):
            return await br.place(leg, "sell", size, sx.tick_round(price, leg["tick"]), "gtc", coid=f"ST{tag}{name[0].upper()}{k}")

        for attempt in range(4):
            res = await asyncio.gather(send("call", call, lots, mk["call"], n), send("put", put, lots, mk["put"], n), return_exceptions=True)
            if not any(isinstance(r, Exception) for r in res):
                orders = {"call": res[0], "put": res[1]}
                break
            for r, leg in zip(res, (call, put)):               # one leg accepted, the other refused: take the accepted one back
                if not isinstance(r, Exception):
                    fin = await br.cancel(r)
                    if fin["filled"]:
                        await self._buy_back(leg, fin["filled"], cfg)
            err = next(r for r in res if isinstance(r, Exception))
            if "margin" not in str(err).lower() or lots < 2:
                raise Skip(f"entry order refused: {err}")
            lots, n = lots // 2, n + 1
            doc["size_reduced"] = True
            await event(f"SIZE REDUCED – the exchange refused the size for margin; trying {lots} lots a leg", "warn", "Size reduced", mode, cfg)
        if not orders:
            raise Skip("entry order refused for margin at every size tried")
        step = 0
        while True:
            await self.sleep(0.25)
            for name in ("call", "put"):
                if orders[name]["open"]:
                    orders[name] = await br.status(orders[name])
            done = all(not o["open"] for o in orders.values())
            elapsed = self.mono() - t0
            if done or elapsed >= cfg["entry_deadline_sec"] or self.kill:
                break
            if elapsed >= (step + 1) * REPRICE_SEC:
                step += 1
                for name, leg in (("call", call), ("put", put)):
                    o = orders[name]
                    if not o["open"]:
                        continue
                    fin = await br.cancel(o)
                    if fin["filled"]:
                        fills[name][1] += fin["filled"] * (fin["avg"] or 0)
                        fills[name][0] += fin["filled"]
                    lat.append(fin.get("latency_ms") or 0)
                    left = lots - fills[name][0]
                    if left <= 0:
                        orders[name] = {**fin, "open": False, "filled": 0}
                        continue
                    q = feed.quote(leg["symbol"]) or {}
                    price = max(floor[name], max(q.get("bid") or 0, mk[name] * (1 - REPRICE_STEP * step)))
                    n += 1
                    orders[name] = await br.place(leg, "sell", left, sx.tick_round(price, leg["tick"]), "gtc", coid=f"ST{tag}{name[0].upper()}{n}")
        ids, ts = [], []
        for name in ("call", "put"):
            fin = await br.cancel(orders[name]) if orders[name]["open"] else orders[name]
            if fin["filled"]:
                fills[name][1] += fin["filled"] * (fin["avg"] or 0)
                fills[name][0] += fin["filled"]
            ids.append(str(fin.get("id")))
            ts.append(fin.get("fill_ts"))
            lat.append(fin.get("latency_ms") or 0)
        got = {n_: fills[n_][0] for n_ in fills}
        keep = 0 if self.kill else min(got.values())
        for name, leg in (("call", call), ("put", put)):       # never a naked or uneven leg
            if got[name] > keep:
                extra, sold = got[name] - keep, fills[name][1] / got[name]
                back = await self._buy_back(leg, extra, cfg)
                pos = extra * sc.LOT
                cost = ((back or sold) - sold) * pos + sc.leg_fee(leg["maker"], leg["cap"], spot, sold, pos, cfg["gst"] / 100) + sc.leg_fee(leg["taker"], leg["cap"], spot, back or sold, pos, cfg["gst"] / 100)
                doc.setdefault("leg_risk", []).append({"leg": name, "lots": extra, "sold": sold, "bought": back})
                doc["leg_risk_cost"] = (doc.get("leg_risk_cost") or 0) + cost
        if keep < 1:
            why = "kill switch" if self.kill else "only one leg filled before the deadline – bought back" if max(got.values()) else "no fill before the deadline"
            raise Skip(f"entry not completed: {why}")
        ec, ep = fills["call"][1] / got["call"], fills["put"][1] / got["put"]
        p = ec + ep
        slip = (mk["call"] + mk["put"] - p) / (mk["call"] + mk["put"])
        stops = []
        for leg in (call, put):                                # backup on the exchange: each leg at disaster multiple x P (wide; the bot's own stop comes first)
            try:
                stops.append(str(await br.place_stop(leg, keep, sx.tick_round(p * cfg["disaster_multiple"], leg["tick"], "up"), coid=f"ST{tag}{leg['symbol'][0]}X")))
            except Exception as e:
                await event(f"Disaster stop on {leg['symbol']} could not be placed: {e}", "error", "Backup stop missing", mode, cfg)
        await self._save(doc, status="OPEN", entry_call=ec, entry_put=ep, lots=keep, entry_at=iso(self.clock()), entry_slip=slip, order_ids=ids + stops,
                         fill_ts=[t for t in ts if t], latency_ms=round(max(lat), 1) if lat else None, liquidity_cap=bool(doc.get("liquidity_cap") or keep < lots))
        await event(f"Entry: sold {keep} lots each of {call['symbol']} at {ec:.2f} and {put['symbol']} at {ep:.2f}. P = {p:.2f}, stop at {p * cfg['stop_multiple']:.2f}, "
                    f"break-evens {strike - p:,.1f} / {strike + p:,.1f}. Entry slippage {slip * 100:+.2f}% vs mark.", "info", "Entry", mode, cfg)
        return True

    async def _buy_back(self, leg, size, cfg):
        """Buy `size` lots of one leg until flat: IOC at ask x (1 + max exit slip) every 0.5 s, market after 5 s. Returns the average price."""
        br, feed, t0, got, cost = self.broker, self.feed, self.mono(), 0, 0.0
        while got < size:
            q = feed.quote(leg["symbol"]) or {}
            late = self.mono() - t0 >= EXIT_MARKET_AFTER or not q.get("ask")
            price = None if late else sx.tick_round(q["ask"] * (1 + cfg["max_exit_slip"] / 100), leg["tick"], "up")
            try:
                o = await br.place(leg, "buy", size - got, price, "ioc", reduce_only=True)
                if o["filled"]:
                    got += o["filled"]
                    cost += o["filled"] * (o["avg"] or 0)
                elif o["size"] == 0:                                     # nothing left to close on the exchange
                    break
            except Exception as e:
                log.warning("straddle buy-back %s: %s", leg["symbol"], e)
            if got < size:
                await self.sleep(EXIT_RETRY_SEC)
                if self.mono() - t0 > 60:
                    await event(f"{leg['symbol']}: still not flat after 60 s ({size - got} lots left). Close it by hand on Delta now.", "error", "NOT FLAT", None, cfg)
                    break
        return cost / got if got else None

    # ---- stop / exit
    def combined(self, doc, key="mark"):
        a, b = ((self.feed.quote(doc[s]) or {}).get(key) for s in ("call", "put"))
        return a + b if a is not None and b is not None else None

    async def flatten(self, doc, cfg, why):
        """Buy back both legs now."""
        legs = doc["legs"]
        mark = self.combined(doc)
        await self._save(doc, status="EXITING", exit_note=why)
        try:
            held = {k: abs(v) for k, v in (await self.broker.positions([legs["call"], legs["put"]])).items()}
        except Exception:
            held = {}

        async def back(leg):
            n = held.get(leg["symbol"], doc["lots"])
            return await self._buy_back(leg, n, cfg) if n else None
        c, p = await asyncio.gather(back(legs["call"]), back(legs["put"]))
        try:
            await self.broker.cancel_all([legs["call"], legs["put"]])
        except Exception as e:
            log.warning("straddle cancel after exit: %s", e)
        if c is None or p is None:                                       # a leg was already closed on the exchange (its backup stop fired)
            fb = lambda s: (self.feed.quote(doc[s]) or {}).get("ask") or (self.feed.quote(doc[s]) or {}).get("mark") or 0.0  # noqa: E731
            c, p = c if c is not None else fb("call"), p if p is not None else fb("put")
        now = self.clock()
        slip = ((c + p) - mark) / mark if mark else None
        await self._save(doc, status="CLOSED", exit_reason="Stop", exit_time=_ist(now).strftime("%H:%M"), exit_call=c, exit_put=p, exit_spot=self.feed.spot or doc["spot"],
                         exit_at=iso(now), exit_slip=slip)
        await self.after_close(doc, cfg, f"Stop exit ({why}): bought back at {c + p:.2f} = {(c + p) / (doc['entry_call'] + doc['entry_put']):.2f} × P")

    async def monitor(self, doc, cfg):
        """17:00 -> 17:30: stop rule, daily loss guard, kill switch, backup-stop watch. Returns when flat or at expiry."""
        day = datetime.strptime(doc["date"], "%Y-%m-%d").date()
        expiry, p = _at(day, EXPIRY_T), doc["entry_call"] + doc["entry_put"]
        level, pos = sc.stop_level(p, doc["stop_multiple"]), doc["lots"] * sc.LOT
        eq = doc.get("sizing_equity_used") or cfg["starting_capital"]
        checked, last_pos, legs = set(doc.get("parity_checked") or []), 0.0, [doc["legs"]["call"], doc["legs"]["put"]]
        while True:
            now = self.clock()
            self.heartbeat = time.time()
            if now >= expiry:
                return "expiry"
            for s in (doc["call"], doc["put"]):
                if self.feed.age(s) > cfg["max_stale_sec"]:
                    await self.feed.rest_refresh([s])
            comb = self.combined(doc)
            if comb is not None:
                unreal = (p - comb) * pos
                self.view = {"phase": "OPEN", "date": doc["date"], "strike": doc["strike"], "p": p, "stop_level": level, "combined": comb, "lots": doc["lots"],
                             "distance_pct": (level - comb) / comb * 100 if comb else None, "be_low": doc["strike"] - p, "be_high": doc["strike"] + p,
                             "expiry": iso(expiry), "unrealised": unreal, "margin_used": doc.get("margin_estimate"), "spot": self.feed.spot, "call": doc["call"], "put": doc["put"],
                             "stop_mode": cfg["stop_mode"], "feed_ok": self.feed.connected}
            if self.kill:
                await self.flatten(doc, cfg, "kill switch")
                return "stop"
            for o in await self.broker.check_stops():
                await event(f"Backup stop fired on {o['leg']['symbol']} at {o['avg']}", "warn", "Backup stop fired", doc["mode"], cfg)
            if self.mono() - last_pos > 5:
                last_pos = self.mono()
                try:
                    held = await self.broker.positions(legs)
                    if any(abs(v) != doc["lots"] for v in held.values()):
                        await self.flatten(doc, cfg, "a backup stop closed one leg on the exchange")
                        return "stop"
                except Exception as e:
                    log.warning("straddle position check: %s", e)
            if comb is not None:
                if -unreal > cfg["max_daily_loss_pct"] / 100 * eq:
                    await self.flatten(doc, cfg, f"daily max loss {cfg['max_daily_loss_pct']:g}% of equity")
                    return "stop"
                minute = now.replace(second=0, microsecond=0)
                key = _ist(minute).strftime("%H:%M")
                if minute > _at(day, ENTRY_T) and key not in checked:        # the close of the minute that just ended
                    checked.add(key)
                    doc["parity_checked"] = sorted(checked)
                    if sc.stop_hit(comb, p, doc["stop_multiple"]) and not doc.get("parity_note"):
                        doc["parity_note"] = f"1-minute rule: stop at {key} ({comb:.2f})"
                    if cfg["stop_mode"] == "parity" and sc.stop_hit(comb, p, doc["stop_multiple"]):
                        await self.flatten(doc, cfg, f"combined {comb:.2f} ≥ {level:.2f} at the {key} close")
                        return "stop"
                if cfg["stop_mode"] == "tick" and sc.stop_hit(comb, p, doc["stop_multiple"]):
                    await self.flatten(doc, cfg, f"combined {comb:.2f} ≥ {level:.2f} (tick)")
                    return "stop"
            nxt = (now.replace(second=0, microsecond=0) + timedelta(minutes=1) - now).total_seconds()
            await self.wait_update(max(0.01, min(0.25, nxt, (expiry - now).total_seconds())))

    # ---- 17:30 expiry
    async def settlement(self, env, symbol):
        p = await sx.public_get(env, f"/v2/products/{symbol}")
        v = sx._f((p.get("product_specs") or {}).get("settlement_index_price"))
        return v if p.get("state") == "expired" and v else None

    async def settle(self, doc, cfg, tries=120, wait=5.0):
        await self._save(doc, status="SETTLING")
        self.view = {"phase": "SETTLING", "date": doc["date"], "strike": doc["strike"]}
        env, s = sx.env_of(doc["mode"]), None
        for _ in range(tries):
            self.heartbeat = time.time()
            try:
                s = await self.settlement(env, doc["call"])
            except Exception as e:
                log.warning("straddle settlement read: %s", e)
            if s:
                break
            await self.sleep(wait)
        if not s:
            await event(f"{doc['date']}: settlement price not published yet – will keep trying", "warn", None, doc["mode"], cfg)
            return False
        c, p = sc.settle_values(doc["strike"], s)
        try:
            await self.broker.cancel_all([doc["legs"]["call"], doc["legs"]["put"]])
        except Exception as e:
            log.warning("straddle cancel at expiry: %s", e)
        await self._save(doc, status="CLOSED", exit_reason="Expiry", exit_time="17:30", exit_call=c, exit_put=p, exit_spot=s, exit_at=iso(self.clock()), exit_slip=0.0,
                         parity_note=doc.get("parity_note") or "1-minute rule: no stop")
        await self.after_close(doc, cfg, f"Expiry: settled at {s:,.2f} against strike {doc['strike']:,.0f}")
        return True

    # ---- after every trade: risk halts
    async def after_close(self, doc, cfg, text):
        mode = doc["mode"]
        rows = await live_journal(mode, cfg)
        me = next((r for r in reversed(rows) if r["date"] == doc["date"]), None)
        if me and me.get("lots"):
            text += f". Net {me['net']:+,.2f} $ (gross {me['gross']:+,.2f}, fees {me['fees']:.2f}). Equity {me['equity']:,.2f} $."
        await event(f"{doc['date']} {text}", "info", "Exit – " + (doc.get("exit_reason") or ""), mode, cfg)
        self.view = {"phase": "CLOSED", "date": doc["date"], "net": me["net"] if me and me.get("lots") else None, "exit_reason": doc.get("exit_reason")}
        why = None
        if rows:
            last = rows[-1]
            if -last["dd_pct"] * 100 > cfg["max_dd_pct"]:
                why = f"drawdown {-last['dd_pct'] * 100:.1f}% from the peak is above {cfg['max_dd_pct']:g}%"
            elif last["streak"] > cfg["max_losing_streak"]:
                why = f"{last['streak']} losing trades in a row (limit {cfg['max_losing_streak']:g})"
            else:
                k = int(cfg["slip_halt_days"])
                recent = [r for r in rows if r.get("lots")][-k:]
                if len(recent) == k and all((r.get("entry_slip") or 0) * 100 > cfg["max_entry_slip"] for r in recent):
                    why = f"entry slippage above {cfg['max_entry_slip']:g}% on {k} trading days in a row"
        if why:
            await set_state(halted=True, halt_reason=why)
            await event(f"TRADING HALTED – {why}. Re-enable it on the Straddle Bot page after checking.", "error", "Trading halted", mode, cfg)
        w = sc.recent_win_alert(rows)
        if w and w["alert"]:
            await event(f"Win ratio of the last 30 trades is {w['win_ratio'] * 100:.0f}%, close to the break-even {w['breakeven'] * 100:.0f}%.", "warn", "Win ratio near break-even", mode, cfg)

    # ---- one day, start to end
    async def run_day(self, doc, st, cfg):
        day = datetime.strptime(doc["date"], "%Y-%m-%d").date()
        try:
            legs = await self.preflight(doc, st, cfg)
            wait = (_at(day, ENTRY_T) - self.clock()).total_seconds()
            while wait > 0:
                self.heartbeat = time.time()
                await self.sleep(min(wait, 1.0) if wait > 1.5 else wait)
                wait = (_at(day, ENTRY_T) - self.clock()).total_seconds()
                if self.kill:
                    raise Skip("kill switch")
            await self.enter(doc, legs, cfg)
        except Skip as e:
            await self._skip(doc, str(e), cfg)
            return await self._end_day()
        if await self.monitor(doc, cfg) == "expiry":
            await self.settle(doc, cfg)
        await self._end_day()

    async def _end_day(self):
        if self.feed:
            await self.feed.stop()

    async def resume(self, doc, st, cfg):
        """After a restart with a trade in progress: re-read the exchange before doing anything."""
        env, mode = sx.env_of(doc["mode"]), doc["mode"]
        if doc["status"] == "SETTLING":
            self.broker = self.broker or self.broker_factory(mode, self.feed or self.feed_factory(env), cfg)
            return await self.settle(doc, cfg, tries=3)
        legs = doc.get("legs")
        if not legs:
            return await self._skip(doc, "restarted during entry before any order was sent", cfg)
        self.feed = self.feed_factory(env)
        await self.feed.start([legs["call"]["symbol"], legs["put"]["symbol"]])
        self.broker = self.broker_factory(mode, self.feed, cfg)
        pair = [legs["call"], legs["put"]]
        if mode == "PAPER":                                                # the paper book lives in memory: rebuild it from the journal
            if doc["status"] == "OPEN":
                self.broker.pos = {x["symbol"]: -doc["lots"] for x in pair}
                for x in pair:
                    await self.broker.place_stop(x, doc["lots"], (doc["entry_call"] + doc["entry_put"]) * cfg["disaster_multiple"])
            held = await self.broker.positions(pair)
        else:
            held = await self.broker.positions(pair)
        await event(f"Restart with a trade in progress ({doc['status']}); exchange position {held}", "warn", "Bot restarted mid-trade", mode, cfg)
        if doc["status"] == "OPEN" and all(v == -doc["lots"] for v in held.values()):
            if await self.monitor(doc, cfg) == "expiry":
                await self.settle(doc, cfg)
        else:                                                              # unknown state: be flat
            await self.broker.cancel_all(pair)
            backs = [await self._buy_back(x, abs(held[x["symbol"]]), cfg) for x in pair if held[x["symbol"]] < 0]
            if doc["status"] == "OPEN" or doc.get("entry_call"):
                q = lambda s: (self.feed.quote(doc[s]) or {}).get("mark") or 0.0  # noqa: E731
                now = self.clock()
                await self._save(doc, status="CLOSED", exit_reason="Stop", exit_note="restart: flattened", exit_time=_ist(now).strftime("%H:%M"), exit_call=q("call"), exit_put=q("put"),
                                 exit_spot=self.feed.spot or doc["spot"], exit_at=iso(now))
                await self.after_close(doc, cfg, "Flattened after a restart")
            else:
                await self._skip(doc, f"restarted during entry – flattened ({len(backs)} leg(s) bought back)", cfg)
        await self._end_day()

    async def step(self):
        """One pass of the scheduler. Returns seconds to wait."""
        self.heartbeat = time.time()
        now, st, cfg = self.clock(), await get_state(), await settings()
        mode, day = st["mode"], self.clock().date()
        date = day.strftime("%Y-%m-%d")
        active = await db.straddle_trades.find_one({"status": {"$in": list(ACTIVE)}})
        if active:
            async with self.lock:
                await self.resume(active, st, cfg)
            return 1.0
        doc = await db.straddle_trades.find_one({"_id": f"{date}:{mode}"})
        pre, entry = _at(day, PRE_T), _at(day, ENTRY_T)
        if doc is not None and doc.get("status") in ("NEW", "PREFLIGHT"):     # restarted between the pre-flight and the entry
            if now < entry - timedelta(seconds=20):
                doc = None
            else:
                await self._skip(doc, "the bot restarted just before the entry", cfg)
        if doc is None and pre <= now < entry - timedelta(seconds=20):
            self.kill = False
            doc = {"_id": f"{date}:{mode}", "date": date, "mode": mode, "status": "NEW", "created_at": iso(now)}
            async with self.lock:
                await self.run_day(doc, st, cfg)
            return 1.0
        if doc is None and entry - timedelta(seconds=20) <= now < _at(day, EXPIRY_T):
            await self._skip({"_id": f"{date}:{mode}", "date": date, "mode": mode}, "the bot was not running at 16:55 IST", cfg)
        if now < pre:
            return max(0.2, min(30.0, (pre - now).total_seconds()))
        return 30.0

    # ---- controls
    async def kill_switch(self, by):
        """Cancel this strategy's orders and flatten its position now; the bot stays off until it is switched on again."""
        self.kill = True
        await set_state(enabled=False)
        await event(f"KILL SWITCH pressed by {by}: flattening and switching the bot off", "error", "Kill switch", (await get_state())["mode"], await settings())
        for _ in range(240):                                               # the day's coroutine does the flattening; wait for it (up to 60 s)
            if not await db.straddle_trades.find_one({"status": {"$in": list(ACTIVE)}}):
                break
            await asyncio.sleep(0.25)
        return not await db.straddle_trades.find_one({"status": {"$in": list(ACTIVE)}})

    async def set_mode(self, mode, confirm, by):
        st, cfg = await get_state(), await settings()
        if mode not in MODES:
            raise ValueError("Unknown mode")
        if mode == st["mode"]:
            return
        if await db.straddle_trades.find_one({"status": {"$in": list(ACTIVE) + ["PREFLIGHT"]}}):
            raise ValueError("A trade is in progress. Change the mode after 17:30 IST.")
        if mode != "PAPER":
            import delta_broker
            if not delta_broker.keys(mode):
                raise ValueError(f"The {mode} API key is not saved yet. Paste it on the Auto-Trade page (Delta Exchange connection).")
            if mode == "LIVE":
                if (confirm or "").strip() != "LIVE":
                    raise ValueError("Type LIVE to confirm")
                n = await db.straddle_trades.count_documents({"mode": "PAPER", "status": {"$in": ["CLOSED", "SKIPPED"]}})
                if n < PAPER_DAYS_BEFORE_LIVE:
                    raise ValueError(f"Paper mode must run for {PAPER_DAYS_BEFORE_LIVE} trading days first ({n} so far)")
            await sx.DeltaOptions(mode, cfg["usd_inr"]).balance()          # key, signature and IP whitelist really work
        await set_state(mode=mode, mode_since=iso(now_utc()))
        await event(f"Mode changed {st['mode']} → {mode} by {by}", "warn", f"Mode {mode}", mode, cfg)


ENGINE = Engine()


async def loop():
    await asyncio.sleep(20)
    while True:
        try:
            wait = await ENGINE.step()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("straddle loop error")
            try:
                await event(f"Bot error: {e}", "error", "Bot error", None, await settings())
            except Exception:
                pass
            wait = 5.0
        await asyncio.sleep(wait)


async def watchdog():
    """Separate task: alert when the bot's heartbeat is missing in the minutes before the entry (spec 8)."""
    warned = None
    while True:
        await asyncio.sleep(20)
        try:
            now = now_utc()
            day = now.date()
            if _at(day, (PRE_T[0], PRE_T[1] - 2)) <= now < _at(day, ENTRY_T) and warned != day and time.time() - ENGINE.heartbeat > 90:
                st = await get_state()
                if st.get("enabled", True) and not st.get("halted"):
                    warned = day
                    await event("The straddle bot is NOT running a few minutes before 17:00 IST. Check the server.", "error", "Bot not running", st["mode"], await settings())
        except Exception as e:
            log.warning("straddle watchdog: %s", e)


# ---------------- page data ----------------
async def dashboard():
    st, cfg = await get_state(), await settings()
    mode = st["mode"]
    rows = await live_journal(mode, cfg)
    start = cfg["starting_capital"]
    live = {"metrics": sc.metrics(rows, start, rows[0]["date"], rows[-1]["date"]) if rows else sc.metrics([], start), "monthly": sc.monthly(rows, start), "curve": sc.curve(rows),
            "avg_entry_slip": sc._avg([r["entry_slip"] for r in rows if r.get("entry_slip") is not None]),
            "avg_exit_slip": sc._avg([r["exit_slip"] for r in rows if r.get("exit_slip") is not None and r.get("exit_reason") == "Stop"]),
            "avg_latency_ms": sc._avg([r["latency_ms"] for r in rows if r.get("latency_ms") is not None]), "win_check": sc.recent_win_alert(rows)}
    import delta_broker
    paper_days = await db.straddle_trades.count_documents({"mode": "PAPER", "status": {"$in": ["CLOSED", "SKIPPED"]}})
    today = await db.straddle_trades.find_one({"_id": f"{now_utc().strftime('%Y-%m-%d')}:{mode}"}, {"legs": 0})
    now = now_utc()
    nxt = _at(now.date(), ENTRY_T)
    if now >= nxt:
        nxt += timedelta(days=1)
    return {
        "mode": mode, "enabled": st.get("enabled", True), "halted": bool(st.get("halted")), "halt_reason": st.get("halt_reason"), "settings": cfg,
        "bounds": BOUNDS, "choices": CHOICES, "defaults": DEFAULTS, "server_time": iso(now), "next_entry": iso(nxt),
        "running": time.time() - ENGINE.heartbeat < 90, "drift_ms": ENGINE.drift_ms, "view": ENGINE.view, "today": today,
        "effective_leverage": core_cfg(cfg, mode)["leverage"], "equity": rows[-1]["equity"] if rows else start,
        "ready": {"paper_days": paper_days, "paper_days_needed": PAPER_DAYS_BEFORE_LIVE, "testnet_key": bool(delta_broker.keys("TESTNET")), "live_key": bool(delta_broker.keys("LIVE"))},
        "live": live,
        "events": [e async for e in db.straddle_events.find({}, {"_id": 0}).sort("at", -1).limit(40)],
        "changes": [c async for c in db.straddle_changes.find({}, {"_id": 0}).sort("at", -1).limit(30)],
    }


async def backtest_view():
    """The four workbook variants without their journals (those are paged separately)."""
    bt = await backtest()
    return {"source": bt["source"], "inputs": bt["inputs"], "variants": {k: {x: v[x] for x in ("name", "metrics", "monthly", "curve")} for k, v in bt["variants"].items()}}


async def journal_rows(src):
    if src in sc.VARIANTS:
        return (await backtest())["variants"][src]["rows"], sc.COLUMNS
    if src in MODES:
        return await live_journal(src, await settings()), sc.COLUMNS + sc.LIVE_EXTRA
    raise ValueError("Unknown journal")


async def journal_page(src, offset=0, limit=100, newest_first=True):
    rows, cols = await journal_rows(src)
    rows = list(reversed(rows)) if newest_first else rows
    return {"src": src, "columns": [{"key": k, "label": lab} for k, lab in cols], "total": len(rows), "offset": offset, "rows": rows[offset:offset + limit]}


async def journal_csv(src):
    rows, cols = await journal_rows(src)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([lab for _, lab in cols])
    for r in rows:
        w.writerow(["; ".join(map(str, r[k])) if isinstance(r.get(k), list) else ("" if r.get(k) is None else r[k]) for k, _ in cols])
    return buf.getvalue()
