"""Straddle bot (owner's spec, 26 Nov 2026): SELL the ATM call + ATM put of BTC's same-day option on Delta Exchange India
at 17:00:00 IST, buy both back when call + put >= stop multiple x P, otherwise hold to the 17:30 IST expiry.

  16:55 IST  pre-flight: clock, option list, the 9 strikes around spot, live prices, balance
  16:59:50   arming: order leverage set, connection warmed, balance read, "nothing else on these contracts" check
  17:00:00   entry: both legs sent at the same instant as limit SELLs at mark; re-priced toward the bid every 3 s in 0.5%
             steps, never below mark x (1 - max entry slip); cancelled at the deadline. A single filled leg is bought back
             at once (never a naked leg); uneven fills are evened out.
  after fill exchange-side stop-market BUY on each leg at disaster multiple x P (protection if this server dies)
  17:01-29   stop: "parity" = the mark at each 1-minute close (the backtest's rule), "tick" = every price update.
             Exit: IOC limit BUYs at ask x (1 + max exit slip) every 0.5 s, market orders after 5 s, and it keeps trying
             until the exchange shows no position. A trade is never marked closed while a position is still open.
  17:30      expiry: the settlement price is read from the contract's record; the in-the-money leg pays |settle - strike|.

Hardening (26 Nov 2026, "prepare before war"):
  - the bot runs in its own thread with its own database connection, so a busy website cannot delay an order;
  - a restart at any moment is safe: the exchange is read first, an entry in progress is adopted or flattened, a trade
    whose expiry passed while the bot was down is settled, and nothing can block the next day's trade;
  - decisions use fresh prices only; other positions on the same contracts (Delta's own automation, manual trades) are
    never closed by the bot, and the day is skipped when one is already there before the entry;
  - Live needs a passed connection test and a Testnet round trip of the same code (rehearsals on the page).

Modes: PAPER (default, fills simulated at the real bid/ask and depth) · TESTNET · LIVE (typed confirmation, 30 paper days first).
The maths is in straddle_core.py, market data and orders in straddle_exec.py. Admin only.

Two bots share this engine (owner, 26 Nov 2026), each with its own settings, state, journal, events and rehearsals:
  sb  Straddle Bot       the rules above (owner's spec)
  op  Option Picks bot   Option Picks pick 5 rules, ★ Recommended with compounded capital:
        16:56 pre-flight, 17:01:00 IST entry (the close of the 17:00 minute, as its backtest), strike nearest BTCUSD futures,
        skip the day when the ATM put mark is below $20 or futures are up more than 2% since 17:31 IST yesterday,
        each leg sized from half the equity, each leg stopped on its own when its mark reaches 2x its sell price (the other
        leg stays open to the 17:30 expiry), backup stop on the exchange at disaster multiple x each leg's sell price.
Only one bot may be on Testnet or Live at a time: both sell the same contracts on the same account.
"""
import asyncio
import csv
import io
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import core
import straddle_core as sc
import straddle_exec as sx
from core import iso, now_utc, IST, APP_NAME

log = logging.getLogger("straddle")
MODES = ("PAPER", "TESTNET", "LIVE")
PRE_T, ENTRY_T, EXPIRY_T = (11, 25), (11, 30), (12, 0)          # UTC = 16:55, 17:00, 17:30 IST
ARM_SEC = 10.0                                                  # arming starts this long before the entry
REPRICE_SEC, REPRICE_STEP = 3.0, 0.005
EXIT_RETRY_SEC, EXIT_MARKET_AFTER = 0.5, 5.0
FRESH_SEC = 10.0                                                # a price older than this is never used for a decision
MAX_DRIFT_MS, PAPER_DAYS_BEFORE_LIVE, LIVE_SMALL_LEVERAGE = 100.0, 30, 5.0
ACTIVE = ("ENTERING", "OPEN", "EXITING", "SETTLING")
NOT_DRILL = {"drill": {"$ne": True}}
QUIET_FROM, QUIET_TO = (11, 15), (12, 10)                       # UTC: no rehearsal 16:45-17:40 IST

BOTS = {"sb": {"name": "Straddle Bot", "prefix": "straddle", "coid": "ST", "pre": PRE_T, "entry": ENTRY_T},
        "op": {"name": "Option Picks bot", "prefix": "opbot", "coid": "OP", "pre": (11, 26), "entry": (11, 31)}}   # 16:56 and 17:01:00 IST
OP_PUT_MIN, OP_UP_MAX, FUTURES = 20.0, 0.02, "BTCUSD"           # Option Picks skip rules and the futures that set its strike
LEG_SHARE = {"sb": 1.0, "op": 0.5}                              # share of the equity each leg is sized from
DEFAULTS = {"starting_capital": 1000.0, "leverage": 25.0, "sizing_mode": "fixed", "stop_multiple": 2.0, "stop_mode": "parity",
            "max_entry_slip": 2.0, "max_exit_slip": 5.0, "entry_deadline_sec": 30.0, "disaster_multiple": 3.0, "max_daily_loss_pct": 15.0,
            "max_dd_pct": 25.0, "max_losing_streak": 6.0, "max_lots": 500.0, "gst": 18.0, "max_spread_pct": 10.0, "max_stale_sec": 2.0,
            "slip_halt_days": 3.0, "live_small_size": 1.0, "exchange_leverage": 100.0, "usd_inr": 85.0, "alerts": 1.0}
BOUNDS = {"starting_capital": (10, 10_000_000), "leverage": (0.1, 100), "stop_multiple": (1.05, 10), "max_entry_slip": (0, 50), "max_exit_slip": (0, 100),
          "entry_deadline_sec": (5, 120), "disaster_multiple": (1.5, 20), "max_daily_loss_pct": (1, 100), "max_dd_pct": (1, 100), "max_losing_streak": (1, 100),
          "max_lots": (1, 50000), "gst": (0, 50), "max_spread_pct": (0.5, 100), "max_stale_sec": (0.5, 30), "slip_halt_days": (1, 30), "live_small_size": (0, 1),
          "exchange_leverage": (1, 200), "usd_inr": (50, 200), "alerts": (0, 1)}
CHOICES = {"sizing_mode": ("fixed", "compounded"), "stop_mode": ("parity", "tick")}
# the Option Picks bot starts at the ★ Recommended backtest: $4,000 ($2,000 a leg) × 25x, compounded, each leg checked on every price
OP_DEFAULTS = {"starting_capital": 4000.0, "leverage": 25.0, "sizing_mode": "compounded", "stop_mode": "tick", "max_lots": 1000.0}
OP_VARIANTS = {"op_compound": "compounded", "op_fixed": "fixed"}
OP_VARIANT_NAMES = {"op_compound": "Option Picks rules · Compounded ★ Recommended", "op_fixed": "Option Picks rules · Fixed capital"}


def C(bot, name):
    """A bot's own collection: straddle_trades, opbot_trades, ..."""
    return getattr(db, f"{BOTS[bot]['prefix']}_{name}")


def defaults(bot="sb"):
    return {**DEFAULTS, **(OP_DEFAULTS if bot == "op" else {})}

# ---------------- database: the bot thread has its own connection ----------------
_TLS = threading.local()
MAIN_LOOP, BOT_LOOP = None, None
LAG = {"now_ms": 0.0, "max_ms": 0.0, "day": None}


class _DB:
    """core.db on the website's loop, the bot thread's own Motor client inside the bot thread."""

    def __getattr__(self, name):
        mine = getattr(_TLS, "db", None)                 # compare with None: a Motor database refuses bool()
        return getattr(core.db if mine is None else mine, name)


db = _DB()


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


async def settings(bot="sb"):
    d = defaults(bot)
    doc = await C(bot, "settings").find_one({"_id": "settings"}) or {}
    return {**d, **{k: doc[k] for k in d if k in doc}, "_bot": bot}


async def save_settings(patch, by, bot="sb"):
    cur, new = await settings(bot), clean_settings(patch)
    changed = {k: v for k, v in new.items() if cur[k] != v}
    if changed:
        await C(bot, "settings").update_one({"_id": "settings"}, {"$set": changed}, upsert=True)
        await C(bot, "changes").insert_many([{"at": iso(now_utc()), "by": by, "key": k, "old": cur[k], "new": v} for k, v in changed.items()])
    return await settings(bot)


async def get_state(bot="sb"):
    st = await C(bot, "state").find_one({"_id": "state"}) or {}
    return {"_id": "state", "mode": "PAPER", "enabled": True, "halted": False, "halt_reason": None, **st}


async def set_state(bot="sb", **kw):
    await C(bot, "state").update_one({"_id": "state"}, {"$set": kw}, upsert=True)


def bot_of(cfg):
    return (cfg or {}).get("_bot") or "sb"


def core_cfg(cfg, mode="PAPER"):
    lev = min(cfg["leverage"], LIVE_SMALL_LEVERAGE) if mode == "LIVE" and cfg["live_small_size"] else cfg["leverage"]
    return {"starting_capital": cfg["starting_capital"], "leverage": lev, "gst": cfg["gst"] / 100, "stop_multiple": cfg["stop_multiple"],
            "leg_share": LEG_SHARE[bot_of(cfg)]}


def _ist(t):
    return t.astimezone(IST)


def _at(day, hm, sec=0):
    return datetime(day.year, day.month, day.day, hm[0], hm[1], sec, tzinfo=timezone.utc)


def _day(doc):
    return datetime.strptime(doc["date"], "%Y-%m-%d").date()


async def mode_days(mode, bot="sb"):
    return [d async for d in C(bot, "trades").find({"mode": mode, "status": {"$in": ["CLOSED", "SKIPPED"]}, **NOT_DRILL}).sort("date", 1)]


async def live_journal(mode, cfg):
    return sc.journal(await mode_days(mode, bot_of(cfg)), core_cfg(cfg, mode), cfg["sizing_mode"])


async def equity_now(mode, cfg):
    rows = await live_journal(mode, cfg)
    return rows[-1]["equity"] if rows else cfg["starting_capital"]


# ---------------- backtest (the owner's workbook) ----------------
_BT = {"key": None, "out": None}


async def backtest_data():
    doc = await db.straddle_backtest.find_one({"_id": "data"})
    return (doc["data"], doc.get("at")) if doc else (sc.load_seed(), "bundled")


async def op_backtest():
    """The Option Picks bot's backtest: Option Picks pick 5's own trade facts (history file + the server's daily backtest), at the
    bot's capital, leverage and GST, fixed and compounded. Runs on the website's loop (Option Picks keeps its data there)."""
    import optpicks
    cfg = await settings("op")
    days = sc.op_days((await optpicks.backtest_rows()).get("SSTR", []), optpicks.fee_rates)
    key = ("op", len(days), days[-1]["date"] if days else None, cfg["starting_capital"], cfg["leverage"], cfg["gst"])
    if _BT.get("op_key") != key:
        cc = core_cfg(cfg)
        inputs = {"starting_capital": cfg["starting_capital"], "leverage": cfg["leverage"], "gst": cc["gst"], "stop_multiple": 2.0, "leg_share": cc["leg_share"]}
        out = {"source": "Option Picks pick 5 backtest on Delta's real prices (1 Jan 2024 onward, then the server's own daily backtest)",
               "inputs": inputs, "variants": {}}
        for v, sizing in OP_VARIANTS.items():
            rows = sc.journal(days, inputs, sizing)
            start = inputs["starting_capital"]
            out["variants"][v] = {"name": OP_VARIANT_NAMES[v], "rows": rows, "metrics": sc.metrics(rows, start, rows[0]["date"], rows[-1]["date"]) if rows else sc.metrics([], start),
                                  "monthly": sc.monthly(rows, start), "curve": sc.curve(rows)}
        _BT.update(op_key=key, op_out=out)
    return _BT["op_out"]


async def backtest(bot="sb"):
    """Journals, metrics, monthly tables and curves of the four workbook variants (cached until a new workbook is imported)."""
    if bot == "op":
        return await op_backtest()
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
_BG, _SENT = set(), {}


async def _deliver(mode, title, text, name="straddle"):
    """Runs on the website's loop (its mail, push and alert-switch code live there)."""
    try:
        if mode == "PAPER":                          # paper alerts follow the site's alert switch, like Auto-Trade; testnet and live always send
            import alert_mode
            if not (await alert_mode.get_mode()).get("effective_armed"):
                return
        import autotrade
        await autotrade._deliver(f"[{mode or 'BOT'}] {APP_NAME} {name}: {title}", text)
    except Exception as e:  # alerts must never break the bot
        log.warning("straddle alert error: %s", e)


def _dispatch(mode, title, text, name="straddle"):
    coro = _deliver(mode, title, text, name)
    try:
        here = asyncio.get_running_loop()
    except RuntimeError:
        here = None
    if MAIN_LOOP is not None and MAIN_LOOP is not here and MAIN_LOOP.is_running():
        asyncio.run_coroutine_threadsafe(coro, MAIN_LOOP)
    elif here is not None:
        t = asyncio.ensure_future(coro)
        _BG.add(t)
        t.add_done_callback(_BG.discard)
    else:
        coro.close()


async def event(text, level="info", alert=None, mode=None, cfg=None):
    """Event log + alert. The same text is not logged twice within a minute, the same alert not sent twice within ten."""
    now, bot = time.time(), bot_of(cfg)
    if now - _SENT.get(("log", bot, text), 0) < 60:
        return
    _SENT[("log", bot, text)] = now
    (log.warning if level != "info" else log.info)("%s: %s", BOTS[bot]["prefix"], text)
    await C(bot, "events").insert_one({"at": iso(now_utc()), "text": text, "level": level, "mode": mode})
    if alert and (cfg or {}).get("alerts", 1) and now - _SENT.get(("alert", bot, alert, text[:80]), 0) >= 600:
        _SENT[("alert", bot, alert, text[:80])] = now
        _dispatch(mode, alert, text, "straddle" if bot == "sb" else BOTS[bot]["name"])
    for k in [k for k, v in _SENT.items() if now - v > 3600]:
        _SENT.pop(k, None)


class Skip(Exception):
    pass


# ---------------- engine ----------------
class Engine:
    def __init__(self, clock=now_utc, sleep=asyncio.sleep, feed_factory=sx.Feed, broker_factory=None, bot="sb"):
        self.bot, self.B, self.op = bot, BOTS[bot], bot == "op"
        self.pre_t, self.entry_t = BOTS[bot]["pre"], BOTS[bot]["entry"]
        self.clock, self.sleep, self.feed_factory = clock, sleep, feed_factory
        self.broker_factory = broker_factory or self._broker
        self.feed, self.broker, self.kill, self.view, self.heartbeat, self.ok_at = None, None, False, {}, 0.0, 0.0
        self.drift_ms, self.drift_src, self.lock, self.mono = None, None, None, time.monotonic
        self.armed, self.strikes = None, []

    @property
    def trades(self):
        return C(self.bot, "trades")

    def _beat(self):
        self.heartbeat = self.ok_at = time.time()

    def _lock(self):
        if self.lock is None:
            self.lock = asyncio.Lock()
        return self.lock

    async def wait_update(self, timeout):
        """Sleep until the next price update or `timeout` seconds, whichever is first."""
        ev = self.feed._event()
        ev.clear()
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    def _broker(self, mode, feed, cfg):
        if mode == "PAPER":
            return sx.PaperBroker(feed)
        return sx.DeltaOptions(mode, cfg["usd_inr"])

    async def _save(self, doc, **kw):
        doc.update(kw)
        await self.trades.update_one({"_id": doc["_id"]}, {"$set": {k: v for k, v in doc.items() if k != "_id"}}, upsert=True)

    async def _skip(self, doc, reason, cfg, alert=True):
        await self._save(doc, status="SKIPPED", skip_reason=reason)
        self.view = {"phase": "SKIPPED", "reason": reason, "date": doc["date"]}
        await event(f"{doc['date']} skipped – {reason}", "warn", "Day skipped" if alert and "switched off" not in reason else None, doc["mode"], cfg)

    # ---- 16:55 pre-flight
    async def clock_drift(self):
        ms, src = await asyncio.get_running_loop().run_in_executor(None, sx.clock_offset_ms)
        self.drift_ms, self.drift_src = ms, src
        return ms

    async def chain(self, day):
        rows = await self.feed.get("/v2/tickers", {"contract_types": "call_options,put_options", "underlying_asset_symbols": "BTC", "expiry_date": day.strftime("%d-%m-%Y")})
        by = {}
        for r in rows:
            k = sx._f(r.get("strike_price"))
            if k is not None:
                by.setdefault(k, {})["call" if r.get("contract_type") == "call_options" else "put"] = r
        return {k: v for k, v in by.items() if "call" in v and "put" in v}

    async def leg_info(self, ticker):
        p = await self.feed.get(f"/v2/products/{ticker['symbol']}")
        specs = p.get("product_specs") or {}
        return {"symbol": p["symbol"], "product_id": p["id"], "tick": float(p.get("tick_size") or 0.1), "maker": float(p.get("maker_commission_rate") or 0),
                "taker": float(p.get("taker_commission_rate") or 0), "cap": float(specs.get("premium_commission_rate") or 0),
                "lot": float(p.get("contract_value") or sc.LOT), "tradable": p.get("state") == "live" and p.get("trading_status") == "operational",
                "lev": sx._f(p.get("default_leverage")), "size_limit": int(p.get("position_size_limit") or 0)}

    async def futures(self):
        """BTCUSD futures, last traded price (Option Picks sets its strike and its +2% rule on it)."""
        t = await self.feed.get(f"/v2/tickers/{FUTURES}")
        return sx._f(t.get("close")) or sx._f(t.get("mark_price"))

    async def fut_1731(self, day):
        """BTCUSD futures at 17:31 IST the day before the expiry: close of the 1-minute candle opening at 17:31 (12:01 UTC)."""
        t = int(_at(day, EXPIRY_T).timestamp()) - 86400 + 60
        rows = await self.feed.get("/v2/history/candles", {"symbol": FUTURES, "resolution": "1m", "start": t - 300, "end": t + 600})
        return next((float(r["close"]) for r in rows or [] if int(r["time"]) == t), None)

    async def ref_price(self, cfg, need=False):
        """The price the strike is chosen from: the BTC index (Straddle Bot) or BTCUSD futures (Option Picks bot)."""
        if not self.op:
            return self.feed.spot
        try:
            return await self.futures()
        except Exception as e:
            if need:
                raise Skip(f"BTC futures price could not be read: {e}")
            log.warning("option picks bot futures read: %s", e)
            return self.feed.spot

    async def preflight(self, doc, st, cfg):
        """Returns {strike: {'call': leg, 'put': leg}} for the 9 strikes around spot, with the feed running. Raises Skip."""
        mode, env, day = doc["mode"], sx.env_of(doc["mode"]), _day(doc)
        if not st.get("enabled", True):
            raise Skip("bot is switched off")
        if st.get("halted"):
            raise Skip(f"trading is halted: {st.get('halt_reason')}")
        ms = await self.clock_drift()
        if ms is not None and abs(ms) > MAX_DRIFT_MS:
            raise Skip(f"server clock is off by {ms:.0f} ms (limit {MAX_DRIFT_MS:.0f} ms)")
        if ms is None and mode != "PAPER":
            raise Skip("server clock could not be checked against a time server")
        self.feed = self.feed_factory(env)
        try:
            chain = await self.chain(day)
        except Exception as e:
            raise Skip(f"option list could not be read: {e}")
        if not chain:
            raise Skip("no BTC option pair expiring today is listed")
        spot = sx._f(next(iter(chain.values()))["call"].get("spot_price"))
        self.strikes = sorted(chain)
        ref = spot
        if self.op:
            ref = await self.ref_price(cfg) or spot
            try:
                doc["fut_1731"] = await self.fut_1731(day)
            except Exception as e:                                         # as the backtest: no reference price, no +2% skip
                log.warning("option picks bot 17:31 futures: %s", e)
                doc["fut_1731"] = None
        near = sorted(chain, key=lambda k: abs(k - ref))[:9]
        legs = {}
        try:
            for k in near:
                c, p = await asyncio.gather(self.leg_info(chain[k]["call"]), self.leg_info(chain[k]["put"]))
                legs[k] = {"call": c, "put": p}
        except Exception as e:
            raise Skip(f"contract details could not be read: {e}")
        await self.feed.start([x["symbol"] for v in legs.values() for x in v.values()])
        self.broker = self.broker_factory(mode, self.feed, cfg)
        if mode == "PAPER":
            eq = await equity_now(mode, cfg)
            self.broker.equity = lambda: eq
        try:
            bal = await self.broker.balance()
        except Exception as e:
            raise Skip(f"account balance could not be read: {e}")
        self.armed = None
        await self._save(doc, status="PREFLIGHT", preflight_at=iso(self.clock()), clock_drift_ms=ms, clock_source=self.drift_src, wallet_available=bal["available"])
        self.view = {"phase": "PREFLIGHT", "date": doc["date"], "spot": spot, "strikes": sorted(near), "drift_ms": ms, "available": bal["available"],
                     **({"futures": ref, "fut_1731": doc.get("fut_1731")} if self.op else {})}
        return legs

    def safe_leverage(self, cfg, spot, mark_call, mark_put):
        """Order leverage for the exchange: the setting, lowered when the margin of a leg would not cover that leg's loss at
        the backup stop (so the backup stop always comes before a liquidation)."""
        loss = cfg["disaster_multiple"] * (mark_call + mark_put) - min(mark_call, mark_put)
        cap = spot / (1.3 * loss) if loss > 0 else cfg["exchange_leverage"]
        return float(max(1, int(min(cfg["exchange_leverage"], cap))))

    async def arm(self, doc, legs, cfg):
        """A few seconds before the entry: leverage, clean-contract check, balance, warm connection. Raises Skip."""
        mode, feed, br = doc["mode"], self.feed, self.broker
        if feed.spot is None or feed.spot_age() > FRESH_SEC:
            await feed.rest_refresh(min_gap=0)
        spot = feed.spot
        if spot is None:
            raise Skip("no BTC index price")
        ref = await self.ref_price(cfg) or spot
        near = sorted(legs, key=lambda k: abs(k - ref))[:3]
        atm = legs[near[0]]
        mc, mp = ((feed.quote(atm[s]["symbol"]) or {}).get("mark") for s in ("call", "put"))
        lev = self.safe_leverage(cfg, spot, mc, mp) if mc and mp else cfg["exchange_leverage"]
        pool = [legs[k][s] for k in near for s in ("call", "put")]
        if mode != "PAPER":
            try:
                held, orders = await br.positions(pool), await br.open_orders(pool)
            except Exception as e:
                raise Skip(f"positions could not be read before the entry: {e}")
            if any(held.values()) or orders:
                raise Skip("there is already a position or an open order on today's contracts (Delta automation or a manual trade). Two managers on one contract are not safe")
            res = await asyncio.gather(*(br.set_leverage(x, lev) for x in pool), return_exceptions=True)
            bad = [r for r in res if isinstance(r, Exception)]
            if bad:
                await event(f"Order leverage could not be set to {lev:g}x ({bad[0]}). The exchange's current leverage will apply.", "warn", None, mode, cfg)
                lev = min((x.get("lev") or lev) for x in pool)
        try:
            ms = await br.warm()
            bal = await br.balance()
        except Exception as e:
            raise Skip(f"margin check failed: {e}")
        self.armed = {"available": bal["available"], "equity": bal["equity"], "lev": lev, "warm_ms": ms}
        doc.update(wallet_before=bal["equity"], exchange_leverage=lev)
        return self.armed

    def plan_size(self, cfg, mode, sizing_equity, spot, pair, available, lev=None):
        """Lots per leg after the caps. Returns (lots, notes)."""
        cc = core_cfg(cfg, mode)
        want = sc.lots_for(sizing_equity * cc["leg_share"], cc["leverage"], spot)
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
        lev = lev or cfg["exchange_leverage"]
        if available is not None:
            per_lot = 2 * spot * sc.LOT / lev                       # both legs; an estimate – the exchange's answer to the order is the real check
            can = int(available * 0.97 / per_lot) if per_lot > 0 else lots      # 3% kept free for fees
            notes["margin_estimate"] = min(lots, can) * per_lot
            if can < lots:
                lots, notes["size_reduced"] = can, True
                notes["why"].append(f"margin: about ${per_lot:.2f} a lot at {lev:g}x against ${available:,.2f} free")
        if lots < want and not notes["liquidity_cap"] and not notes["size_reduced"]:
            notes["size_reduced"] = True
        return max(lots, 0), notes

    # ---- 17:00 entry
    async def enter(self, doc, legs, cfg):
        """Sell both legs. Returns True when a hedged position is open. Raises Skip (flat) otherwise."""
        mode, feed, br = doc["mode"], self.feed, self.broker
        if self.armed is None:
            await self.arm(doc, legs, cfg)
        if feed.spot is None or feed.spot_age() > cfg["max_stale_sec"]:
            await feed.rest_refresh(min_gap=0)
        spot = feed.spot
        if spot is None:
            raise Skip("no BTC index price")
        ref = await self.ref_price(cfg, need=True) if self.op else spot
        if not ref:
            raise Skip("no BTC futures price")
        strike = min(legs, key=lambda k: abs(k - ref))
        if self.strikes and min(self.strikes, key=lambda k: abs(k - ref)) != strike:
            raise Skip(f"BTC moved too far since the pre-flight: the nearest strike is no longer one of the 9 that were prepared ({'futures' if self.op else 'spot'} {ref:,.1f})")
        pair = legs[strike]
        call, put = pair["call"], pair["put"]
        stale = [x["symbol"] for x in (call, put) if feed.age(x["symbol"]) > cfg["max_stale_sec"]]
        if stale:
            await feed.rest_refresh(stale, min_gap=0)
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
        if self.op and not cfg.get("_drill"):                             # Option Picks skip rules, on the same numbers as its backtest
            if mk["put"] < OP_PUT_MIN:
                raise Skip(f"put mark ${mk['put']:.2f} is below ${OP_PUT_MIN:g} (Option Picks rule)")
            fp = doc.get("fut_1731")
            if fp and ref / fp - 1 > OP_UP_MAX:
                raise Skip(f"BTC futures are up {(ref / fp - 1) * 100:+.2f}% since 17:31 IST yesterday ({fp:,.1f} → {ref:,.1f}); Option Picks skips above +{OP_UP_MAX * 100:g}%")
        sizing_eq = cfg["starting_capital"] if cfg["sizing_mode"] == "fixed" else await equity_now(mode, cfg)
        lots, notes = self.plan_size(cfg, mode, sizing_eq, ref, pair, self.armed["available"], self.armed["lev"])
        if lots < 1:
            raise Skip("size is below 1 lot after the caps: " + "; ".join(notes["why"] or ["equity too small"]))
        tag = doc["date"].replace("-", "")[2:] + mode[0] + ("D" if doc.get("drill") else "")
        pre = self.B["coid"]
        if self.op:
            doc["ref_price"] = ref
        await self._save(doc, status="ENTERING", spot=spot, strike=strike, call=call["symbol"], put=put["symbol"], maker=call["maker"], taker=call["taker"], cap=call["cap"],
                         mark_call=mk["call"], mark_put=mk["put"], lots_wanted=notes["wanted"], liquidity_cap=notes["liquidity_cap"], size_reduced=notes["size_reduced"],
                         size_notes=notes["why"], margin_estimate=notes.get("margin_estimate"), legs={"call": call, "put": put}, stop_multiple=cfg["stop_multiple"],
                         sizing_equity_used=sizing_eq, entry_sent_at=iso(self.clock()))
        if notes["size_reduced"] or notes["liquidity_cap"]:
            await event(f"SIZE REDUCED – deviates from backtest: {lots} lots a leg instead of {notes['wanted']} ({'; '.join(notes['why'])})", "warn", "Size reduced", mode, cfg)
        t0, floor = self.mono(), {n: mk[n] * (1 - cfg["max_entry_slip"] / 100) for n in mk}
        orders, fills, lat, n = {}, {"call": [0, 0.0], "put": [0, 0.0]}, [], 0
        uid = f"{int(time.time()) % 100000:05d}"

        async def send(name, leg, size, price, k):
            return await br.place(leg, "sell", size, sx.tick_round(price, leg["tick"]), "gtc", coid=f"{pre}{tag}{name[0].upper()}{uid}{k}")

        for _ in range(4):
            res = await asyncio.gather(send("call", call, lots, mk["call"], n), send("put", put, lots, mk["put"], n), return_exceptions=True)
            if not any(isinstance(r, Exception) for r in res):
                orders = {"call": res[0], "put": res[1]}
                break
            for r, (name, leg) in zip(res, (("call", call), ("put", put))):      # one leg accepted, the other refused: take the accepted one back
                if not isinstance(r, Exception):
                    fin = await br.cancel(r)
                    if fin["filled"]:
                        await self._leg_risk(doc, name, leg, fin["filled"], fin["avg"], spot, cfg)
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
            await self.sleep(0.3)
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
                    orders[name] = await br.place(leg, "sell", left, sx.tick_round(price, leg["tick"]), "gtc", coid=f"{pre}{tag}{name[0].upper()}{uid}{n}")
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
                await self._leg_risk(doc, name, leg, got[name] - keep, fills[name][1] / got[name], spot, cfg)
        if keep < 1:
            why = "kill switch" if self.kill else "only one leg filled before the deadline – bought back" if max(got.values()) else "no fill before the deadline"
            raise Skip(f"entry not completed: {why}")
        ec, ep = fills["call"][1] / got["call"], fills["put"][1] / got["put"]
        await self._opened(doc, cfg, keep, ec, ep, ids, ts, lat, (mk["call"] + mk["put"] - ec - ep) / (mk["call"] + mk["put"]), keep < lots)
        return True

    async def _leg_risk(self, doc, name, leg, extra, sold, spot, cfg):
        """Buy back lots that filled on one leg only, and book what that cost."""
        got, back = await self._buy_back(leg, extra, cfg)
        pos, g = extra * sc.LOT, cfg["gst"] / 100
        cost = ((back or sold) - sold) * pos + sc.leg_fee(leg["maker"], leg["cap"], spot, sold, pos, g) + sc.leg_fee(leg["taker"], leg["cap"], spot, back or sold, pos, g)
        doc.setdefault("leg_risk", []).append({"leg": name, "lots": extra, "sold": sold, "bought": back, "bought_lots": got})
        doc["leg_risk_cost"] = (doc.get("leg_risk_cost") or 0) + cost

    async def place_stops(self, doc, cfg, only=None):
        """Backup stop-market BUY on the exchange for each leg at disaster multiple x P (wide; the bot's own stop comes first).
        Option Picks bot: at disaster multiple x that leg's own sell price, on the legs still open."""
        p, ids, bought = doc["entry_call"] + doc["entry_put"], [], doc.get("bought") or {}
        for name in ("call", "put"):
            leg = doc["legs"][name]
            size = doc["lots"] - (bought.get(name) or {}).get("lots", 0)
            if (only is not None and leg["symbol"] not in only) or size < 1:
                continue
            base = doc[f"entry_{name}"] if self.op else p
            try:
                coid = f"{self.B['coid']}{doc['date'].replace('-', '')[2:]}{doc['mode'][0]}{name[0].upper()}X{int(time.time()) % 100000:05d}"
                ids.append(str(await self.broker.place_stop(leg, size, sx.tick_round(base * cfg["disaster_multiple"], leg["tick"], "up"), coid=coid)))
            except Exception as e:
                await event(f"Backup stop on {leg['symbol']} could not be placed: {e}", "error", "Backup stop missing", doc["mode"], cfg)
        return ids

    async def _opened(self, doc, cfg, lots, ec, ep, ids, ts, lat, slip, cut, note=None):
        p, strike = ec + ep, doc["strike"]
        doc.update(entry_call=ec, entry_put=ep, lots=lots)
        if self.op:
            doc.update(stop_call=ec * doc["stop_multiple"], stop_put=ep * doc["stop_multiple"])
        stops = await self.place_stops(doc, cfg)
        await self._save(doc, status="OPEN", entry_at=iso(self.clock()), entry_slip=slip, order_ids=ids + stops, fill_ts=[t for t in ts if t],
                         latency_ms=round(max(lat), 1) if lat else None, liquidity_cap=bool(doc.get("liquidity_cap") or cut), entry_note=note)
        stop_txt = (f"stops at {ec * doc['stop_multiple']:.2f} (call) and {ep * doc['stop_multiple']:.2f} (put), each leg on its own"
                    if self.op else f"stop at {p * doc['stop_multiple']:.2f}")
        await event(f"Entry{' (' + note + ')' if note else ''}: sold {lots} lots each of {doc['call']} at {ec:.2f} and {doc['put']} at {ep:.2f}. P = {p:.2f}, "
                    f"{stop_txt}, break-evens {strike - p:,.1f} / {strike + p:,.1f}. "
                    f"Entry slippage {slip * 100:+.2f}% vs mark, order leverage {doc.get('exchange_leverage') or 0:g}x.", "info", "Entry", doc["mode"], cfg)

    async def _buy_back(self, leg, size, cfg, until=None):
        """Buy `size` lots of one leg: IOC at ask x (1 + max exit slip) every 0.5 s, market orders after 5 s. Keeps going until
        done, until the exchange shows nothing left to close, or until `until` (the expiry). Returns (lots bought, average price)."""
        br, feed, t0, got, cost, warned, errs = self.broker, self.feed, self.mono(), 0, 0.0, 20.0, 0
        while got < size:
            if until is not None and self.clock() >= until:
                break
            q = feed.quote(leg["symbol"]) or {}
            late = self.mono() - t0 >= EXIT_MARKET_AFTER or not q.get("ask")
            price = None
            if not late:
                price = q["ask"] * (1 + cfg["max_exit_slip"] / 100)
                hi = (q.get("band") or (None, None))[1]
                price = sx.tick_round(min(price, hi) if hi else price, leg["tick"], "up" if not hi or price < hi else "down")
            try:
                o = await br.place(leg, "buy", size - got, price, "ioc", reduce_only=True)
                if o["filled"]:
                    got += o["filled"]
                    cost += o["filled"] * (o["avg"] or 0)
                elif o["size"] == 0:                                     # nothing left to close
                    break
            except Exception as e:
                errs += 1
                log.warning("straddle buy-back %s: %s", leg["symbol"], e)
                try:                                                     # is there still something to close? are resting stops in the way?
                    held = (await br.positions([leg]))[leg["symbol"]]
                    if held >= 0:
                        break
                    if errs == 1:
                        await br.cancel_stops([leg])
                except Exception as e2:
                    log.warning("straddle buy-back position read %s: %s", leg["symbol"], e2)
            if got < size:
                await self.sleep(EXIT_RETRY_SEC)
                if self.mono() - t0 > warned:
                    warned += 30.0
                    await event(f"{leg['symbol']}: NOT FLAT yet, {size - got} lots still to buy back after {self.mono() - t0:.0f} s. Still trying. Check Delta.", "error", "NOT FLAT", None, cfg)
        return got, (cost / got if got else None)

    # ---- stop / exit
    def combined(self, doc, key="mark"):
        """Call + put, only when both prices are fresh."""
        vals = []
        for s in ("call", "put"):
            q = self.feed.quote(doc[s]) or {}
            if q.get(key) is None or self.feed.age(doc[s]) > FRESH_SEC:
                return None
            vals.append(q[key])
        return vals[0] + vals[1]

    def _price_now(self, doc, name):
        q = self.feed.quote(doc[name]) or {}
        return q.get("ask") or q.get("mark") or 0.0

    async def flatten(self, doc, cfg, why):
        """Buy back both legs. Returns 'flat' (trade closed in the journal) or 'expired' (the expiry came first: settle what is left)."""
        legs, expiry, lots = doc["legs"], _at(_day(doc), EXPIRY_T), doc["lots"]
        mark = self.combined(doc)
        if self.op:
            for n in ("call", "put"):
                if self._left(doc, n) > 0 and not (doc.get("leg_exit_mark") or {}).get(n):
                    doc.setdefault("leg_exit_mark", {})[n] = (self.feed.quote(doc[n]) or {}).get("mark")
        if doc.get("status") != "EXITING":
            await self._save(doc, status="EXITING", exit_note=why, exit_started=iso(self.clock()), exit_mark=mark)
        bought = doc.setdefault("bought", {})
        try:
            held = await self.broker.positions([legs["call"], legs["put"]])
        except Exception as e:
            log.warning("straddle position read before exit: %s", e)
            held = {}

        async def back(name):
            leg, done = legs[name], (bought.get(name) or {}).get("lots", 0)
            short = max(0, -held[leg["symbol"]]) if leg["symbol"] in held else lots
            need = min(lots - done, short)
            got, avg = await self._buy_back(leg, need, cfg, until=expiry) if need > 0 else (0, None)
            if got:
                prev = bought.get(name) or {"lots": 0, "avg": 0.0}
                bought[name] = {"lots": prev["lots"] + got, "avg": (prev["lots"] * prev["avg"] + got * avg) / (prev["lots"] + got)}
            return need - got
        left = await asyncio.gather(back("call"), back("put"))
        await self._save(doc)
        if any(left):
            try:                                                           # the last word is the exchange's
                held = await self.broker.positions([legs["call"], legs["put"]])
                left = [min(lots - (bought.get(n) or {}).get("lots", 0), max(0, -held[legs[n]["symbol"]])) for n in ("call", "put")]
            except Exception as e:
                log.warning("straddle position read after exit: %s", e)
        if any(left):
            if self.clock() >= expiry:
                return "expired"
            await event(f"Exit not complete: {left[0]} call and {left[1]} put lots are still open. The backup stops stay on the exchange. Trying again.", "error", "NOT FLAT", doc["mode"], cfg)
            raise RuntimeError("position not flat after the exit attempt")
        try:
            await self.broker.cancel_all([legs["call"], legs["put"]])
        except Exception as e:
            log.warning("straddle cancel after exit: %s", e)
        px, est = {}, False
        for name in ("call", "put"):                                       # lots the bot did not buy itself were closed by the exchange-side stop
            b = bought.get(name) or {"lots": 0, "avg": 0.0}
            rest = lots - b["lots"]
            est = est or rest > 0
            px[name] = (b["lots"] * b["avg"] + rest * self._price_now(doc, name)) / lots
        now = self.clock()
        base = doc.get("exit_mark")
        slip = ((px["call"] + px["put"]) - base) / base if base else None
        if self.op:                                                        # legs not stopped on their own leave now
            for name in ("call", "put"):
                if doc.get(f"{name}_exit") != "Stop":
                    m0 = (doc.get("leg_exit_mark") or {}).get(name)
                    doc.setdefault("leg_slip", {})[name] = (px[name] - m0) / m0 if m0 else None
                    doc.update({f"{name}_exit": "Stop", f"{name}_stop_time": _ist(now).strftime("%H:%M"), f"exit_spot_{name}": self.feed.spot or doc["spot"]})
            doc["stopped_legs"] = 2
            slip = sc._avg([v for v in (doc.get("leg_slip") or {}).values() if v is not None])     # each leg against its own mark
        await self._save(doc, status="CLOSED", exit_reason="Stop", exit_time=_ist(now).strftime("%H:%M"), exit_call=px["call"], exit_put=px["put"],
                         exit_spot=self.feed.spot or doc["spot"], exit_at=iso(now), exit_slip=slip,
                         exit_note=doc.get("exit_note") + ("; price of the part closed by the backup stop is estimated" if est else ""))
        await self.after_close(doc, cfg, f"Stop exit ({doc['exit_note']}): bought back at {px['call'] + px['put']:.2f} = {(px['call'] + px['put']) / (doc['entry_call'] + doc['entry_put']):.2f} × P")
        return "flat"

    async def monitor(self, doc, cfg):
        """17:00 -> 17:30: stop rule, daily loss guard, kill switch, backup-stop watch. Returns 'stop' when flat, 'expiry' at 17:30."""
        if self.op:
            return await self.monitor_legs(doc, cfg)
        day = _day(doc)
        expiry, p = _at(day, EXPIRY_T), doc["entry_call"] + doc["entry_put"]
        level, pos = sc.stop_level(p, doc["stop_multiple"]), doc["lots"] * sc.LOT
        eq = doc.get("sizing_equity_used") or cfg["starting_capital"]
        checked, last_pos, legs, blind = set(doc.get("parity_checked") or []), -1e9, [doc["legs"]["call"], doc["legs"]["put"]], None
        refresh_after = max(cfg["max_stale_sec"], 3.0)

        async def out(why):
            return "stop" if await self.flatten(doc, cfg, why) == "flat" else "expiry"

        while True:
            now = self.clock()
            self._beat()
            if now >= expiry:
                return "expiry"
            stale = [s for s in (doc["call"], doc["put"]) if self.feed.age(s) > refresh_after]
            if stale:
                await self.feed.rest_refresh(stale)
            comb = self.combined(doc)
            if comb is None:
                blind = blind if blind is not None else self.mono()
                if self.mono() - blind > 15:
                    await event("No fresh option prices for more than 15 s. The stop cannot be checked; the backup stops on the exchange still protect the trade.",
                                "error", "No prices", doc["mode"], cfg)
            else:
                blind, unreal = None, (p - comb) * pos
                self.view = {"phase": "OPEN", "date": doc["date"], "strike": doc["strike"], "p": p, "stop_level": level, "combined": comb, "lots": doc["lots"],
                             "distance_pct": (level - comb) / comb * 100 if comb else None, "be_low": doc["strike"] - p, "be_high": doc["strike"] + p,
                             "expiry": iso(expiry), "unrealised": unreal, "margin_used": doc.get("margin_used") or doc.get("margin_estimate"), "spot": self.feed.spot,
                             "call": doc["call"], "put": doc["put"], "stop_mode": cfg["stop_mode"], "feed_ok": self.feed.connected, "liquidation": doc.get("liquidation")}
            if self.kill:
                return await out("kill switch")
            for o in await self.broker.check_stops():
                await event(f"Backup stop fired on {o['leg']['symbol']} at {o['avg']}", "warn", "Backup stop fired", doc["mode"], cfg)
            if self.mono() - last_pos > 5:
                last_pos = self.mono()
                try:
                    held = await self.broker.positions(legs)
                    short = [max(0, -v) for v in held.values()]
                    if any(s < doc["lots"] for s in short):
                        return await out("a backup stop or the exchange closed part of the position")
                    if any(s > doc["lots"] for s in short) and not doc.get("foreign"):
                        await self._save(doc, foreign=True)
                        await event(f"Another short position is on the same contracts ({held}); the bot manages only its own {doc['lots']} lots. "
                                    "Stop Delta's own automation or manual trades on these contracts.", "error", "Other position on the contract", doc["mode"], cfg)
                except Exception as e:
                    log.warning("straddle position check: %s", e)
            if comb is not None:
                if -unreal > cfg["max_daily_loss_pct"] / 100 * eq:
                    return await out(f"daily max loss {cfg['max_daily_loss_pct']:g}% of equity")
                minute = now.replace(second=0, microsecond=0)
                key = _ist(minute).strftime("%H:%M")
                if minute > _at(day, ENTRY_T) and key not in checked:        # the close of the minute that just ended
                    checked.add(key)
                    doc["parity_checked"] = sorted(checked)
                    if sc.stop_hit(comb, p, doc["stop_multiple"]) and not doc.get("parity_note"):
                        doc["parity_note"] = f"1-minute rule: stop at {key} ({comb:.2f})"
                    if cfg["stop_mode"] == "parity" and sc.stop_hit(comb, p, doc["stop_multiple"]):
                        return await out(f"combined {comb:.2f} ≥ {level:.2f} at the {key} close")
                if cfg["stop_mode"] == "tick" and sc.stop_hit(comb, p, doc["stop_multiple"]):
                    return await out(f"combined {comb:.2f} ≥ {level:.2f} (tick)")
            nxt = (now.replace(second=0, microsecond=0) + timedelta(minutes=1) - now).total_seconds()
            await self.wait_update(max(0.01, min(0.25, nxt, (expiry - now).total_seconds())))

    # ---- Option Picks bot: each leg on its own
    def _left(self, doc, name):
        return doc["lots"] - ((doc.get("bought") or {}).get(name) or {}).get("lots", 0)

    async def close_leg(self, doc, cfg, name, why):
        """Buy back one leg (its own stop); the other leg stays open. Lots the exchange already closed (backup stop) are booked
        at the current price and marked as estimated."""
        leg, expiry, lots = doc["legs"][name], _at(_day(doc), EXPIRY_T), doc["lots"]
        bought = doc.setdefault("bought", {})
        need = self._left(doc, name)
        if need <= 0:
            return
        if not doc.get("leg_exit_started", {}).get(name):
            doc.setdefault("leg_exit_started", {})[name] = iso(self.clock())
            doc.setdefault("leg_exit_mark", {})[name] = (self.feed.quote(leg["symbol"]) or {}).get("mark")
            await self._save(doc)
        try:
            held = (await self.broker.positions([leg]))[leg["symbol"]]
            short = max(0, -held)
        except Exception as e:
            log.warning("option picks bot position read before a leg exit: %s", e)
            short = need
        take = min(need, short)
        got, avg = await self._buy_back(leg, take, cfg, until=expiry) if take > 0 else (0, None)
        if got:
            prev = bought.get(name) or {"lots": 0, "avg": 0.0}
            bought[name] = {"lots": prev["lots"] + got, "avg": (prev["lots"] * prev["avg"] + got * avg) / (prev["lots"] + got)}
        est = False
        if self._left(doc, name) > 0:                                      # what is still missing: closed by the exchange, or still open?
            try:
                short = max(0, -(await self.broker.positions([leg]))[leg["symbol"]])
            except Exception as e:
                log.warning("option picks bot position read after a leg exit: %s", e)
                short = self._left(doc, name)
            gone = self._left(doc, name) - min(short, self._left(doc, name))
            if gone > 0:
                prev, px_ = bought.get(name) or {"lots": 0, "avg": 0.0}, self._price_now(doc, name)
                bought[name] = {"lots": prev["lots"] + gone, "avg": (prev["lots"] * prev["avg"] + gone * px_) / (prev["lots"] + gone)}
                est = True
        if self._left(doc, name) > 0:
            await self._save(doc)
            if self.clock() >= expiry:
                return
            await event(f"{leg['symbol']}: leg exit not complete, {self._left(doc, name)} lots still open. The backup stop stays on the exchange. Trying again.",
                        "error", "NOT FLAT", doc["mode"], cfg)
            raise RuntimeError("leg not flat after the exit attempt")
        try:
            await self.broker.cancel_stops([leg])
        except Exception as e:
            log.warning("option picks bot cancel backup stop: %s", e)
        now, b = self.clock(), bought[name]
        doc.update({f"{name}_exit": "Stop", f"{name}_stop_time": _ist(now).strftime("%H:%M"), f"exit_spot_{name}": self.feed.spot or doc["spot"],
                    f"exit_{name}": b["avg"], "stopped_legs": sum(1 for n in ("call", "put") if doc.get(f"{n}_exit") == "Stop" or n == name)})
        base = (doc.get("leg_exit_mark") or {}).get(name)
        doc.setdefault("leg_slip", {})[name] = (b["avg"] - base) / base if base else None
        if est:
            doc["exit_note"] = ((doc.get("exit_note") or "") + f"; {name}: price of the part closed by the backup stop is estimated").lstrip("; ")
        await self._save(doc)
        other = "put" if name == "call" else "call"
        tail = f"The {other} stays open to 17:30." if self._left(doc, other) > 0 else "Both legs are now closed."
        await event(f"{name.title()} stop ({why}): bought back {b['lots']} lots at {b['avg']:.2f} = {b['avg'] / doc[f'entry_{name}']:.2f} × its sell price "
                    f"{doc[f'entry_{name}']:.2f}. {tail}", "warn", f"{name.title()} stop", doc["mode"], cfg)

    async def monitor_legs(self, doc, cfg):
        """Option Picks bot, 17:01 -> 17:30: each leg is bought back when its own mark reaches stop multiple x its sell price; the
        other leg stays open. Daily loss guard, kill switch and backup-stop watch as the Straddle Bot. 'stop' when both legs are
        closed, 'expiry' at 17:30 (open legs settle)."""
        day = _day(doc)
        expiry, lots, pos = _at(day, EXPIRY_T), doc["lots"], doc["lots"] * sc.LOT
        entry = {n: doc[f"entry_{n}"] for n in ("call", "put")}
        level = {n: entry[n] * doc["stop_multiple"] for n in entry}
        eq = doc.get("sizing_equity_used") or cfg["starting_capital"]
        checked, last_pos, blind = set(doc.get("parity_checked") or []), -1e9, None
        refresh_after = max(cfg["max_stale_sec"], 3.0)

        async def out(why):
            return "stop" if await self.flatten(doc, cfg, why) == "flat" else "expiry"

        while True:
            now = self.clock()
            self._beat()
            if now >= expiry:
                return "expiry"
            live = [n for n in ("call", "put") if self._left(doc, n) > 0]
            if not live:
                return await out("both legs stopped")
            stale = [doc[n] for n in live if self.feed.age(doc[n]) > refresh_after]
            if stale:
                await self.feed.rest_refresh(stale)
            marks = {}
            for n in live:
                q = self.feed.quote(doc[n]) or {}
                if q.get("mark") is not None and self.feed.age(doc[n]) <= FRESH_SEC:
                    marks[n] = q["mark"]
            bought = doc.get("bought") or {}
            realised = sum((entry[n] - b["avg"]) * b["lots"] * sc.LOT for n, b in bought.items() if b and b.get("lots"))
            if len(marks) < len(live):
                blind = blind if blind is not None else self.mono()
                if self.mono() - blind > 15:
                    await event("No fresh option prices for more than 15 s. The leg stops cannot be checked; the backup stops on the exchange still protect the trade.",
                                "error", "No prices", doc["mode"], cfg)
            else:
                blind = None
            unreal = realised + sum((entry[n] - marks[n]) * self._left(doc, n) * sc.LOT for n in marks)
            self.view = {"phase": "OPEN", "date": doc["date"], "strike": doc["strike"], "p": entry["call"] + entry["put"], "lots": lots, "expiry": iso(expiry),
                         "unrealised": unreal, "margin_used": doc.get("margin_used") or doc.get("margin_estimate"), "spot": self.feed.spot, "call": doc["call"],
                         "put": doc["put"], "stop_mode": cfg["stop_mode"], "feed_ok": self.feed.connected, "liquidation": doc.get("liquidation"),
                         "be_low": doc["strike"] - entry["call"] - entry["put"], "be_high": doc["strike"] + entry["call"] + entry["put"],
                         "legs": {n: {"entry": entry[n], "stop": level[n], "mark": marks.get(n), "open_lots": self._left(doc, n),
                                      "distance_pct": (level[n] - marks[n]) / marks[n] * 100 if marks.get(n) else None,
                                      "exit": doc.get(f"{n}_exit"), "exit_price": doc.get(f"exit_{n}") if doc.get(f"{n}_exit") else None} for n in ("call", "put")}}
            if self.kill:
                return await out("kill switch")
            for o in await self.broker.check_stops():
                await event(f"Backup stop fired on {o['leg']['symbol']} at {o['avg']}", "warn", "Backup stop fired", doc["mode"], cfg)
            if self.mono() - last_pos > 5:
                last_pos = self.mono()
                try:
                    held = await self.broker.positions([doc["legs"][n] for n in ("call", "put")])
                    for n in live:
                        short = max(0, -held[doc[n]])
                        if short < self._left(doc, n):
                            await self.close_leg(doc, cfg, n, "a backup stop or the exchange closed part of this leg")
                        elif short > self._left(doc, n) and not doc.get("foreign"):
                            await self._save(doc, foreign=True)
                            await event(f"Another short position is on the same contracts ({held}); the bot manages only its own lots. "
                                        "Stop Delta's own automation or manual trades on these contracts.", "error", "Other position on the contract", doc["mode"], cfg)
                except RuntimeError:
                    raise
                except Exception as e:
                    log.warning("option picks bot position check: %s", e)
            if marks and len(marks) == len(live) and -unreal > cfg["max_daily_loss_pct"] / 100 * eq:
                return await out(f"daily max loss {cfg['max_daily_loss_pct']:g}% of equity")
            minute = now.replace(second=0, microsecond=0)
            key = _ist(minute).strftime("%H:%M")
            close_check = minute > _at(day, self.entry_t) and key not in checked
            if close_check:
                checked.add(key)
                doc["parity_checked"] = sorted(checked)
            hit = [n for n in marks if self._left(doc, n) > 0 and (cfg["stop_mode"] == "tick" or close_check) and marks[n] >= level[n] - 1e-12]
            if hit:
                await asyncio.gather(*(self.close_leg(doc, cfg, n, f"mark {marks[n]:.2f} ≥ {level[n]:.2f}, {doc['stop_multiple']:g}× its sell price"
                                                      + ("" if cfg["stop_mode"] == "tick" else f" at the {key} close")) for n in hit))
                continue
            nxt = (now.replace(second=0, microsecond=0) + timedelta(minutes=1) - now).total_seconds()
            await self.wait_update(max(0.01, min(0.25, nxt, (expiry - now).total_seconds())))

    # ---- 17:30 expiry
    async def settlement(self, symbol):
        p = await self.feed.get(f"/v2/products/{symbol}")
        v = sx._f((p.get("product_specs") or {}).get("settlement_index_price"))
        return v if p.get("state") == "expired" and v else None

    async def twap(self, day):
        """Fallback when Delta has not published the settlement price hours after expiry: the index's own 17:00-17:30 IST average."""
        a, b = int(_at(day, ENTRY_T).timestamp()), int(_at(day, EXPIRY_T).timestamp())
        rows = await self.feed.get("/v2/history/candles", {"symbol": sx.INDEX, "resolution": "1m", "start": a, "end": b})
        closes = [float(r["close"]) for r in rows or [] if a <= int(r["time"]) < b]
        return sum(closes) / len(closes) if len(closes) >= 20 else None

    async def settle(self, doc, cfg, tries=120, wait=5.0):
        """Close the trade at the official settlement price. Returns False while the price is not known yet (it is tried again; never blocks the next day)."""
        if doc.get("status") != "SETTLING":
            await self._save(doc, status="SETTLING")
        self.view = {"phase": "SETTLING", "date": doc["date"], "strike": doc.get("strike")}
        if self.feed is None:
            self.feed = self.feed_factory(sx.env_of(doc["mode"]))
        s, src, late = None, "official settlement_index_price", self.clock() - _at(_day(doc), EXPIRY_T)
        for i in range(tries):
            self._beat()
            try:
                s = await self.settlement(doc["call"])
            except Exception as e:
                log.warning("straddle settlement read: %s", e)
            if s or i == tries - 1:
                break
            await self.sleep(wait)
        if not s and late > timedelta(hours=2):
            try:
                s, src = await self.twap(_day(doc)), "index 30-minute average (official price not published)"
            except Exception as e:
                log.warning("straddle settlement fallback: %s", e)
            if not s and late > timedelta(hours=8) and doc.get("spot"):
                s, src = doc.get("last_spot") or doc["spot"], "UNVERIFIED: last known index price (no settlement data)"
        if not s:
            await event(f"{doc['date']}: settlement price not published yet – will keep trying", "warn", None, doc["mode"], cfg)
            return False
        lots, bought = doc["lots"], doc.get("bought") or {}
        val = dict(zip(("call", "put"), sc.settle_values(doc["strike"], s)))
        px = {n: ((bought.get(n) or {}).get("lots", 0) * (bought.get(n) or {}).get("avg", 0.0) + (lots - (bought.get(n) or {}).get("lots", 0)) * val[n]) / lots for n in val}
        part = any((bought.get(n) or {}).get("lots", 0) for n in val)
        try:
            if self.broker is not None and doc.get("legs"):
                await self.broker.cancel_all([doc["legs"]["call"], doc["legs"]["put"]])
        except Exception as e:
            log.warning("straddle cancel at expiry: %s", e)
        reason, slip = "Stop" if part else "Expiry", None if part else 0.0
        if self.op:
            for n in ("call", "put"):
                if doc.get(f"{n}_exit") != "Stop":
                    doc[f"{n}_exit"] = "Settle"
            k = sum(1 for n in ("call", "put") if doc.get(f"{n}_exit") == "Stop")
            doc["stopped_legs"] = k
            reason = "Stop" if k == 2 else "Stop+Expiry" if k else "Expiry"
            if k:
                slip = sc._avg([v for v in (doc.get("leg_slip") or {}).values() if v is not None])     # the stopped leg against its own mark
        await self._save(doc, status="CLOSED", exit_reason=reason, exit_time="17:30", exit_call=px["call"], exit_put=px["put"], exit_spot=s,
                         exit_at=iso(self.clock()), exit_slip=slip, settle_source=src,
                         parity_note=doc.get("parity_note") or ("live check = the backtest's 1-minute mark high" if self.op else "1-minute rule: no stop"))
        text = f"Expiry: settled at {s:,.2f} against strike {doc['strike']:,.0f}" + ((" (the stopped leg was bought back before expiry)" if self.op else
                                                                                   " (part of the position was bought back before expiry)") if part else "")
        if not src.startswith("official"):
            await event(f"{doc['date']}: settlement taken from {src}. Compare with Delta's statement.", "error", "Settlement not official", doc["mode"], cfg)
        await self.after_close(doc, cfg, text)
        return True

    # ---- after every trade: wallet check and risk halts
    async def after_close(self, doc, cfg, text):
        mode = doc["mode"]
        if doc.get("drill"):
            return await event(f"Rehearsal {doc['date']} {text}", "info", None, mode, cfg)
        rows = await live_journal(mode, cfg)
        me = next((r for r in reversed(rows) if r["date"] == doc["date"]), None)
        if me and me.get("lots"):
            text += f". Net {me['net']:+,.2f} $ (gross {me['gross']:+,.2f}, fees {me['fees']:.2f}). Equity {me['equity']:,.2f} $."
        if mode != "PAPER" and self.broker is not None and doc.get("wallet_before") is not None:
            try:
                await self.sleep(3.0)
                after = (await self.broker.balance())["equity"]
                change = after - doc["wallet_before"]
                await self._save(doc, wallet_after=after, wallet_change=change)
                text += f" Delta wallet moved {change:+,.2f} $."
                if me and me.get("lots") and abs(change - me["net"]) > max(2.0, 0.25 * abs(me["premium"])):
                    await event(f"{doc['date']}: the Delta wallet moved {change:+,.2f} $ but the journal computes {me['net']:+,.2f} $. Other strategies on the same account "
                                "also move the wallet; if none traded in this half hour, compare with Delta's statement.", "warn", "Check wallet", mode, cfg)
            except Exception as e:
                log.warning("straddle wallet check: %s", e)
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
            await set_state(self.bot, halted=True, halt_reason=why)
            await event(f"TRADING HALTED – {why}. Re-enable the {self.B['name']} on the Straddle Bot page after checking.", "error", "Trading halted", mode, cfg)
        w = sc.recent_win_alert(rows)
        if w and w["alert"]:
            await event(f"Win ratio of the last 30 trades is {w['win_ratio'] * 100:.0f}%, close to the break-even {w['breakeven'] * 100:.0f}%.", "warn", "Win ratio near break-even", mode, cfg)

    # ---- one day, start to end
    async def _sleep_until(self, t):
        while True:
            wait = (t - self.clock()).total_seconds()
            if wait <= 0:
                return
            self._beat()
            await self.sleep(min(wait, 1.0) if wait > 1.5 else wait)
            if self.kill:
                raise Skip("kill switch")

    async def run_day(self, doc, st, cfg):
        entry = _at(_day(doc), self.entry_t)
        try:
            legs = await self.preflight(doc, st, cfg)
            await self._sleep_until(entry - timedelta(seconds=ARM_SEC))
            await self.arm(doc, legs, cfg)
            await self._sleep_until(entry)
            await self.enter(doc, legs, cfg)
        except Skip as e:
            await self._skip(doc, str(e), cfg)
            return await self._end_day()
        await self._after_entry(doc, cfg)
        await self._end_day()

    async def _after_entry(self, doc, cfg):
        if doc["mode"] != "PAPER":
            try:                                                           # what the exchange really blocked, and where it would liquidate
                det = await self.broker.position_details([doc["legs"]["call"], doc["legs"]["put"]])
                await self._save(doc, margin_used=sum(v.get("margin") or 0 for v in det.values()) or None, liquidation={k: v.get("liquidation") for k, v in det.items()})
            except Exception as e:
                log.warning("straddle margin read: %s", e)
        if await self.monitor(doc, cfg) == "expiry":
            await self.settle(doc, cfg)

    async def _end_day(self):
        self.armed = None
        if self.feed:
            await self.feed.stop()
        if self.broker:
            try:
                await self.broker.close()
            except Exception:
                pass

    async def resume(self, doc, st, cfg):
        """After a restart (or an error) with a trade in progress: read the exchange first, then act."""
        env, mode, status = sx.env_of(doc["mode"]), doc["mode"], doc["status"]
        await self._end_day()
        self.feed = self.feed_factory(env)
        self.broker = self.broker_factory(mode, self.feed, cfg)
        legs = doc.get("legs")
        try:
            if self.clock() >= _at(_day(doc), EXPIRY_T):                    # the expiry passed while the bot was away: the exchange has settled what was open
                if doc.get("entry_call") is None or not legs:
                    return await self._skip(doc, "the bot was down from the entry until after the expiry. If Delta shows a fill for today, it settled at expiry: check the statement", cfg)
                return await self.settle(doc, cfg, tries=1, wait=0)
            if not legs:
                return await self._skip(doc, "restarted during the entry before any order was sent", cfg)
            pair = [legs["call"], legs["put"]]
            await self.feed.start([x["symbol"] for x in pair])
            if mode == "PAPER":                                            # the paper book lives in memory: rebuild it from the journal
                if status == "ENTERING":
                    return await self._skip(doc, "restarted during the entry (paper)", cfg)
                done = doc.get("bought") or {}
                self.broker.pos = {legs[n]["symbol"]: -(doc["lots"] - (done.get(n) or {}).get("lots", 0)) for n in ("call", "put")}
            det = await self.broker.position_details(pair)
            short = {n: max(0, -det[legs[n]["symbol"]]["size"]) for n in ("call", "put")}
            await event(f"Restart with a trade in progress ({status}); the exchange shows {short['call']} call and {short['put']} put lots short", "warn", "Bot restarted mid-trade", mode, cfg)
            if status == "ENTERING":
                await self.broker.cancel_all(pair)
                keep = min(short.values())
                ec, ep = det[legs["call"]["symbol"]].get("entry"), det[legs["put"]["symbol"]].get("entry")
                for n in ("call", "put"):
                    if short[n] > keep:
                        await self._leg_risk(doc, n, legs[n], short[n] - keep, det[legs[n]["symbol"]].get("entry") or 0.0, doc["spot"], cfg)
                if keep < 1 or not ec or not ep:
                    return await self._skip(doc, "restarted during the entry – nothing hedged was open, flattened", cfg)
                mk = doc["mark_call"] + doc["mark_put"]
                await self._opened(doc, cfg, keep, ec, ep, [], [], [], (mk - ec - ep) / mk, keep < doc.get("lots_wanted", keep), note="adopted after a restart")
                return await self._after_entry(doc, cfg)
            if status == "EXITING":
                if await self.flatten(doc, cfg, doc.get("exit_note") or "restart during the exit") == "expired":
                    await self.settle(doc, cfg)
                return
            want = {n: self._left(doc, n) for n in ("call", "put")}          # Option Picks bot: a stopped leg is expected to be flat
            if self.op:
                for n in ("call", "put"):
                    if short[n] < want[n]:                                 # this leg changed while the bot was down: book it, keep the other
                        await self.close_leg(doc, cfg, n, "the leg changed while the bot was down")
                want = {n: self._left(doc, n) for n in ("call", "put")}
                short = {n: min(short[n], want[n]) for n in short}
            if all(short[n] >= want[n] for n in short):                     # OPEN and intact: make sure the backup stops are there, then carry on
                have = {o["symbol"] for o in await self.broker.open_orders(pair) if o["stop"]}
                missing = {legs[n]["symbol"] for n in ("call", "put") if want[n] > 0} - have
                if missing:
                    await self.place_stops(doc, cfg, only=missing)
                return await self._after_entry(doc, cfg)
            if await self.flatten(doc, cfg, "the position changed while the bot was down") == "expired":
                await self.settle(doc, cfg)
        finally:
            await self._end_day()

    async def step(self):
        """One pass of the scheduler. Returns seconds to wait."""
        self.heartbeat = time.time()
        now, st, cfg = self.clock(), await get_state(self.bot), await settings(self.bot)
        mode, day = st["mode"], self.clock().date()
        date = day.strftime("%Y-%m-%d")
        async for active in self.trades.find({"status": {"$in": list(ACTIVE)}, **NOT_DRILL}):
            async with self._lock():
                if active["status"] == "SETTLING":                         # one quick try; an unpublished price never blocks today's trade
                    try:
                        await self.settle(active, cfg, tries=1, wait=0)
                    finally:
                        await self._end_day()
                    continue
                await self.resume(active, st, cfg)
            self.ok_at = time.time()
            return 1.0
        doc = await self.trades.find_one({"_id": f"{date}:{mode}"})
        pre, entry = _at(day, self.pre_t), _at(day, self.entry_t)
        late = entry - timedelta(seconds=ARM_SEC + 10)
        if doc is not None and doc.get("status") in ("NEW", "PREFLIGHT"):     # restarted between the pre-flight and the entry
            if now < late:
                doc = None
            else:
                await self._skip(doc, "the bot restarted just before the entry", cfg)
        self.ok_at = time.time()
        if doc is None and pre <= now < late:
            self.kill = False
            doc = {"_id": f"{date}:{mode}", "date": date, "mode": mode, "status": "NEW", "created_at": iso(now)}
            async with self._lock():
                await self.run_day(doc, st, cfg)
            self.ok_at = time.time()
            return 1.0
        if doc is None and late <= now < _at(day, EXPIRY_T):
            await self._skip({"_id": f"{date}:{mode}", "date": date, "mode": mode}, f"the bot was not running at {_ist(pre).strftime('%H:%M')} IST", cfg)
        if now < pre:
            return max(0.2, min(30.0, (pre - now).total_seconds()))
        return 30.0

    # ---- controls
    async def kill_switch(self, by):
        """Cancel this strategy's orders and flatten its position now; the bot stays off until it is switched on again."""
        self.kill = True
        await set_state(self.bot, enabled=False)
        await event(f"KILL SWITCH pressed by {by}: flattening and switching the {self.B['name']} off", "error", "Kill switch", (await get_state(self.bot))["mode"], await settings(self.bot))
        q = {"status": {"$in": ["ENTERING", "OPEN", "EXITING"]}, **NOT_DRILL}
        for _ in range(240):                                               # the bot's own thread does the flattening; wait for it (up to 60 s)
            if not await self.trades.find_one(q):
                break
            await asyncio.sleep(0.25)
        return not await self.trades.find_one(q)

    async def set_mode(self, mode, confirm, by):
        st, cfg = await get_state(self.bot), await settings(self.bot)
        if mode not in MODES:
            raise ValueError("Unknown mode")
        if mode == st["mode"]:
            return
        if await self.trades.find_one({"status": {"$in": list(ACTIVE) + ["PREFLIGHT"]}, **NOT_DRILL}):
            raise ValueError("A trade is in progress. Change the mode after 17:30 IST.")
        if mode != "PAPER":
            for other in BOTS:
                if other != self.bot and (await get_state(other))["mode"] != "PAPER":
                    raise ValueError(f"The {BOTS[other]['name']} is on {(await get_state(other))['mode']}. Only one bot may trade on the exchange at a time "
                                     "(both sell the same contracts on the same account): put it back on Paper first")
            import delta_broker
            if not delta_broker.keys(mode):
                raise ValueError(f"The {mode} API key is not saved yet. Paste it on the Auto-Trade page (Delta Exchange connection).")
            if mode == "LIVE":
                if (confirm or "").strip() != "LIVE":
                    raise ValueError("Type LIVE to confirm")
                n = await self.trades.count_documents({"mode": "PAPER", "status": {"$in": ["CLOSED", "SKIPPED"]}, **NOT_DRILL})
                if n < PAPER_DAYS_BEFORE_LIVE:
                    raise ValueError(f"The {self.B['name']} must run on Paper for {PAPER_DAYS_BEFORE_LIVE} trading days first ({n} so far)")
                if not st.get("conn_ok_LIVE"):
                    raise ValueError("Run the Live connection test first (Rehearsal, on this page). It must pass")
                if not (st.get("drill_ok_TESTNET") or st.get("drill_waived")):
                    raise ValueError("Run the Testnet round-trip rehearsal first (Rehearsal, on this page). It must pass")
            br = sx.DeltaOptions(mode, cfg["usd_inr"])
            try:
                await br.balance()                                         # key, signature and IP whitelist really work
            finally:
                await br.close()
        await set_state(self.bot, mode=mode, mode_since=iso(now_utc()))
        await event(f"Mode changed {st['mode']} → {mode} by {by}", "warn", f"Mode {mode}", mode, cfg)


ENGINES = {b: Engine(bot=b) for b in BOTS}
ENGINE = ENGINES["sb"]


# ---------------- rehearsals (run on the bot's thread, never in the trade window) ----------------
def _quiet_window(now):
    return _at(now.date(), QUIET_FROM) <= now < _at(now.date(), QUIET_TO)


def expiry_day(now):
    """The next daily expiry: today before 17:30 IST, tomorrow after."""
    return now.date() if now < _at(now.date(), EXPIRY_T) else now.date() + timedelta(days=1)


async def _check(steps, name, fn):
    t0 = time.perf_counter()
    try:
        detail = await fn()
        steps.append({"name": name, "ok": True, "detail": str(detail or "ok"), "ms": round((time.perf_counter() - t0) * 1000)})
        return True
    except Exception as e:
        steps.append({"name": name, "ok": False, "detail": str(e) or type(e).__name__, "ms": round((time.perf_counter() - t0) * 1000)})
        return False


async def _rehearsal_guard():
    if _quiet_window(now_utc()):
        raise ValueError("Not between 16:45 and 17:40 IST: the bots are trading then")
    for b in BOTS:                                                         # either bot's trade blocks a rehearsal: same account, same contracts
        if await C(b, "trades").find_one({"status": {"$in": ["PREFLIGHT"] + list(ACTIVE)}, **NOT_DRILL}):
            raise ValueError(f"A {BOTS[b]['name']} trade is in progress. Try again after it is closed.")


async def _finish_check(kind, mode, steps, by, extra=None, bot="sb"):
    ok = bool(steps) and all(s["ok"] for s in steps)
    rec = {"at": iso(now_utc()), "kind": kind, "mode": mode, "ok": ok, "steps": steps, "by": by, "bot": bot, **(extra or {})}
    await C(bot, "checks").insert_one(dict(rec))
    return rec


async def connection_test(mode, by, bot="sb"):
    """Everything short of a trade, on the real API: key, clock, wallet, contracts, prices, positions, leverage,
    and one resting order far above the market that is cancelled at once (it cannot fill)."""
    import delta_broker
    if mode not in ("TESTNET", "LIVE"):
        raise ValueError("Choose Testnet or Live")
    await _rehearsal_guard()
    cfg, steps, eng, ctx = await settings(bot), [], Engine(bot=bot), {}
    eng.feed = sx.Feed(sx.env_of(mode))
    br = sx.DeltaOptions(mode, cfg["usd_inr"])

    async def key():
        if not delta_broker.keys(mode):
            raise RuntimeError(f"no {mode} API key is saved (Auto-Trade page, Delta Exchange connection)")
        return "key found"

    async def clock():
        ms = await eng.clock_drift()
        if ms is None:
            raise RuntimeError("no time server could be reached. Install chrony on the server and allow outgoing UDP port 123")
        if abs(ms) > MAX_DRIFT_MS:
            raise RuntimeError(f"clock is {ms:+.0f} ms off (limit {MAX_DRIFT_MS:.0f}). Install chrony on the server")
        return f"{ms:+.1f} ms ({eng.drift_src})"

    async def wallet():
        ctx["bal"] = await br.balance()
        return f"${ctx['bal']['equity']:,.2f} balance, ${ctx['bal']['available']:,.2f} free"

    async def futures():
        f = await eng.futures()
        if not f:
            raise RuntimeError("no BTCUSD futures price")
        return f"BTCUSD futures {f:,.1f} (sets the strike)"

    async def contracts():
        chain = await eng.chain(expiry_day(now_utc()))
        if not chain:
            raise RuntimeError("no BTC daily option pair is listed for the next expiry")
        spot = sx._f(next(iter(chain.values()))["call"].get("spot_price"))
        k = min(chain, key=lambda x: abs(x - spot))
        ctx["call"], ctx["put"] = await asyncio.gather(eng.leg_info(chain[k]["call"]), eng.leg_info(chain[k]["put"]))
        if not (ctx["call"]["tradable"] and ctx["put"]["tradable"]):
            raise RuntimeError("the ATM pair is listed but not tradable now")
        return f"{len(chain)} strikes; ATM {k:,.0f}: {ctx['call']['symbol']} + {ctx['put']['symbol']}; fee {ctx['call']['taker'] * 100:g}%, cap {ctx['call']['cap'] * 100:g}% of premium"

    async def prices():
        await eng.feed.start([ctx["call"]["symbol"], ctx["put"]["symbol"]])
        for _ in range(40):
            if eng.feed.connected:
                break
            await asyncio.sleep(0.25)
        q = eng.feed.quote(ctx["call"]["symbol"]) or {}
        if not q.get("mark"):
            raise RuntimeError("no mark price for the ATM call")
        return f"websocket {'connected' if eng.feed.connected else 'NOT connected (REST fallback worked)'}; call mark {q['mark']:.2f}, bid {q.get('bid')}, ask {q.get('ask')}"

    async def account():
        pair = [ctx["call"], ctx["put"]]
        det, orders = await br.position_details(pair), await br.open_orders(pair)
        return f"positions {[v['size'] for v in det.values()]}, {len(orders)} open orders on the ATM pair"

    async def leverage():
        got = await asyncio.gather(br.set_leverage(ctx["call"], cfg["exchange_leverage"]), br.set_leverage(ctx["put"], cfg["exchange_leverage"]))
        return f"order leverage now {got[0]:g}x / {got[1]:g}x"

    async def resting():
        leg = ctx["call"]
        q = eng.feed.quote(leg["symbol"]) or {}
        hi = (q.get("band") or (None, None))[1]
        price = sx.tick_round(hi * 0.98 if hi else q["mark"] * 3, leg["tick"], "down")
        o = await br.place(leg, "sell", 1, price, "gtc", coid=f"{BOTS[bot]['coid']}CT{int(time.time()) % 10 ** 8}")
        fin = await br.cancel(o)
        if fin["filled"]:                                                  # cannot happen at a price near the upper band; if it does, undo it
            await br.place(leg, "buy", fin["filled"], None, "ioc", reduce_only=True)
            raise RuntimeError(f"the far order filled at {fin['avg']} and was bought back – tell the developer")
        if fin["open"]:
            raise RuntimeError("the order could not be cancelled – cancel it by hand on Delta now")
        return f"placed 1 lot SELL at {price:g} (mark {q['mark']:.2f}) in {o['latency_ms']:.0f} ms, cancelled in {fin['latency_ms']:.0f} ms, nothing filled"

    try:
        if await _check(steps, "API key", key) and await _check(steps, "Sign in and read the wallet", wallet):
            await _check(steps, "Server clock", clock)
            if bot == "op":
                await _check(steps, "BTC futures price (Option Picks strike)", futures)
            if await _check(steps, "Option list and ATM contracts", contracts):
                await _check(steps, "Live prices", prices)
                await _check(steps, "Read positions and open orders", account)
                await _check(steps, "Set order leverage", leverage)
                await _check(steps, "Place and cancel a resting order (cannot fill)", resting)
    finally:
        await eng.feed.stop()
        await br.close()
    rec = await _finish_check("connection", mode, steps, by, bot=bot)
    if rec["ok"]:
        await set_state(bot, **{f"conn_ok_{mode}": rec["at"]})
    return rec


async def round_trip(by, bot="sb"):
    """Testnet only: the bot's own entry, backup stops and exit run once with 1 lot, outside the 17:00 schedule.
    Option Picks bot: its skip rules are not applied, and the exit buys the call back on its own first (the per-leg stop path)."""
    import delta_broker
    mode = "TESTNET"
    await _rehearsal_guard()
    if not delta_broker.keys(mode):
        raise ValueError("The Testnet API key is not saved yet. Paste it on the Auto-Trade page (Delta Exchange connection).")
    cfg = {**await settings(bot), "max_lots": 1.0, "max_spread_pct": 100.0, "max_entry_slip": 50.0, "entry_deadline_sec": 20.0, "alerts": 0.0, "sizing_mode": "fixed",
           "_drill": True}
    now, steps, eng, ctx = now_utc(), [], Engine(clock=lambda: now_utc(), bot=bot), {}       # the same clock that dates the rehearsal
    doc = {"_id": f"drill-{int(now.timestamp())}:{mode}", "date": expiry_day(now).strftime("%Y-%m-%d"), "mode": mode, "status": "NEW", "drill": True, "created_at": iso(now)}

    async def pre():
        ctx["legs"] = await eng.preflight(doc, {"enabled": True}, cfg)
        return f"clock {eng.drift_ms:+.1f} ms; {len(ctx['legs'])} strikes ready; websocket {'connected' if eng.feed.connected else 'connecting'}"

    async def arm():
        a = await eng.arm(doc, ctx["legs"], cfg)
        return f"order leverage {a['lev']:g}x, ${a['available']:,.2f} free, connection {a['warm_ms']:.0f} ms"

    async def entry():
        await eng.enter(doc, ctx["legs"], cfg)
        return (f"sold {doc['lots']} lot each: {doc['call']} at {doc['entry_call']:.2f} (mark {doc['mark_call']:.2f}), {doc['put']} at {doc['entry_put']:.2f} "
                f"(mark {doc['mark_put']:.2f}); slowest order {doc.get('latency_ms')} ms")

    async def stops():
        pair = [doc["legs"]["call"], doc["legs"]["put"]]
        have = [o for o in await eng.broker.open_orders(pair) if o["stop"]]
        det = await eng.broker.position_details(pair)
        if len(have) < 2:
            raise RuntimeError(f"only {len(have)} of 2 backup stops are on the exchange")
        if any(v["size"] != -doc["lots"] for v in det.values()):
            raise RuntimeError(f"position is {[v['size'] for v in det.values()]}, expected {-doc['lots']} on each leg")
        ctx["margin"] = sum(v.get("margin") or 0 for v in det.values())
        return f"2 backup stops resting; position {-doc['lots']}/{-doc['lots']}; margin blocked ${ctx['margin']:,.2f}; liquidation at {[v.get('liquidation') for v in det.values()]}"

    async def leg_exit():
        await eng.close_leg(doc, cfg, "call", "rehearsal: per-leg stop path")
        pair = [doc["legs"]["call"], doc["legs"]["put"]]
        held = await eng.broker.positions(pair)
        if held[doc["call"]] != 0 or held[doc["put"]] != -doc["lots"]:
            raise RuntimeError(f"after the call exit the position is {held}; expected call 0 and put {-doc['lots']}")
        stops_left = [o for o in await eng.broker.open_orders(pair) if o["stop"]]
        if len(stops_left) != 1:
            raise RuntimeError(f"{len(stops_left)} backup stops left; expected only the put's")
        return f"call bought back on its own at {doc['exit_call']:.2f}; the put is still open with its backup stop"

    async def exit_():
        r = await eng.flatten(doc, cfg, "rehearsal")
        if r != "flat":
            raise RuntimeError("not flat – close the two option positions by hand on Delta Testnet")
        return f"bought back at {doc['exit_call']:.2f} + {doc['exit_put']:.2f}"

    async def clean():
        pair = [doc["legs"]["call"], doc["legs"]["put"]]
        held, orders = await eng.broker.positions(pair), await eng.broker.open_orders(pair)
        if any(held.values()) or orders:
            raise RuntimeError(f"left over: positions {held}, {len(orders)} open orders")
        return "no position and no open order left"

    waived = False
    async with ENGINES[bot]._lock():
        try:
            if await _check(steps, "Pre-flight", pre) and await _check(steps, "Arming", arm):
                if await _check(steps, "Entry: sell 1 lot of each leg", entry):
                    await _check(steps, "Backup stops and position on the exchange", stops)
                    if bot == "op":
                        await _check(steps, "Leg exit: buy the call back on its own", leg_exit)
                    await _check(steps, "Exit: buy both legs back" if bot == "sb" else "Exit: buy the put back", exit_)
                    await _check(steps, "Nothing left behind", clean)
                elif any(w in steps[-1]["detail"] for w in ("no two-sided market", "below 1 lot", "no fill before", "bid-ask spread", "not tradable")):
                    waived = True                                           # the practice exchange has no usable market today: not the bot's fault
            elif "no BTC option pair" in steps[-1]["detail"]:
                waived = True
        finally:
            try:
                if doc.get("legs") and eng.broker is not None and doc.get("status") in ("ENTERING", "OPEN", "EXITING"):
                    await eng.broker.cancel_all([doc["legs"]["call"], doc["legs"]["put"]])
                    for x in (doc["legs"]["call"], doc["legs"]["put"]):
                        await eng._buy_back(x, doc.get("lots") or 1, cfg, until=now_utc() + timedelta(seconds=20))
            except Exception as e:
                steps.append({"name": "Clean-up", "ok": False, "detail": str(e), "ms": 0})
            await C(bot, "trades").update_one({"_id": doc["_id"]}, {"$set": {"status": "DRILL", "drill": True}}, upsert=True)
            await eng._end_day()
    rec = await _finish_check("round trip", mode, steps, by, {"waived": waived}, bot=bot)
    if rec["ok"]:
        await set_state(bot, drill_ok_TESTNET=rec["at"], drill_waived=False)
    elif waived:
        await set_state(bot, drill_waived=True)
    return rec


async def in_bot(coro):
    """Run a coroutine on the bot's thread (its sockets and database connection live there) and wait for it here."""
    if BOT_LOOP is None or not BOT_LOOP.is_running():
        return await coro
    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, BOT_LOOP))


# ---------------- the bot's own thread ----------------
async def loop(engine=None):
    engine = engine or ENGINE
    await asyncio.sleep(20)
    while True:
        try:
            wait = await engine.step()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("%s loop error", engine.B["prefix"])
            try:
                await event(f"Bot error: {e}", "error", "Bot error", None, await settings(engine.bot))
            except Exception:
                pass
            wait = 5.0
        await asyncio.sleep(wait)


async def _lag_probe():
    """How late the bot's own loop wakes up. A healthy loop is within a few ms."""
    while True:
        t = time.perf_counter()
        await asyncio.sleep(0.05)
        ms = max(0.0, (time.perf_counter() - t - 0.05) * 1000)
        day = now_utc().date()
        if LAG["day"] != day:
            LAG.update(day=day, max_ms=0.0)
        LAG["now_ms"] = ms
        LAG["max_ms"] = max(LAG["max_ms"], ms)


async def _bot_main():
    asyncio.ensure_future(_lag_probe())
    await asyncio.gather(*(loop(e) for e in ENGINES.values()))      # each bot has its own scheduler; one failing never stops the other


_STOP = threading.Event()


def _thread_main():
    global BOT_LOOP
    while not _STOP.is_set():
        lp = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(lp)
            import motor.motor_asyncio as mm
            _TLS.db = mm.AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
            BOT_LOOP = lp
            lp.run_until_complete(_bot_main())
        except Exception:
            if not _STOP.is_set():
                log.exception("straddle bot thread stopped; restarting in 5 s")
        finally:
            BOT_LOOP = None
            try:
                lp.close()
            except Exception:
                pass
        _STOP.wait(5)


def start(main_loop):
    """Called once at server start: the bot gets its own thread, event loop and database connection."""
    global MAIN_LOOP
    MAIN_LOOP = main_loop
    _STOP.clear()
    t = threading.Thread(target=_thread_main, name="straddle-bot", daemon=True)
    t.start()
    return t


def stop():
    """Stop the bot thread (tests, shutdown)."""
    _STOP.set()
    lp = BOT_LOOP
    if lp is not None:
        lp.call_soon_threadsafe(lp.stop)


def alive(bot=None):
    """True when the bot (both bots when none is named) finished a scheduler pass in the last two minutes (/api/health, watchdog)."""
    return all(time.time() - e.ok_at < 120 for b, e in ENGINES.items() if bot in (None, b))


def hold_restart():
    """True from a minute before the entry until a few minutes after the expiry while a bot is alive and has a trade in hand."""
    now = now_utc()
    day = now.date()
    return any(_at(day, e.entry_t) - timedelta(seconds=90) <= now < _at(day, EXPIRY_T) + timedelta(minutes=3)
               and alive(b) and e.view.get("phase") in ("PREFLIGHT", "OPEN") for b, e in ENGINES.items())


async def watchdog():
    """Runs on the website's loop: alert when the bot's heartbeat is missing in the minutes before the entry (spec 8)."""
    warned = {}
    while True:
        await asyncio.sleep(20)
        for b, e in ENGINES.items():
            try:
                now = now_utc()
                day = now.date()
                if _at(day, (e.pre_t[0], e.pre_t[1] - 2)) <= now < _at(day, e.entry_t) and warned.get(b) != day and time.time() - e.heartbeat > 90:
                    st = await get_state(b)
                    if st.get("enabled", True) and not st.get("halted"):
                        warned[b] = day
                        await event(f"The {e.B['name']} is NOT running a few minutes before its {_ist(_at(day, e.entry_t)).strftime('%H:%M')} IST entry. Check the server.",
                                    "error", "Bot not running", st["mode"], await settings(b))
            except Exception as ex:
                log.warning("straddle watchdog %s: %s", b, ex)


# ---------------- page data ----------------
RULES = {
    "sb": ["16:55 IST pre-flight: clock, option list, the 9 strikes around the BTC index, live prices, balance.",
           "17:00:00 IST: sell the ATM call and ATM put (strike nearest the BTC index) as limit orders at mark, re-priced toward the bid.",
           "Size: equity × leverage ÷ BTC price per leg (lot 0.001 BTC).",
           "Stop: buy both legs back when call + put ≥ stop multiple × P (P = both sell prices).",
           "Otherwise hold to the 17:30 IST expiry; settled at Delta's official settlement price."],
    "op": ["16:56 IST pre-flight: clock, option list, the 9 strikes around BTCUSD futures, live prices, balance, BTC futures at 17:31 IST yesterday.",
           "17:01:00 IST (close of the 17:00 minute, as the backtest): sell the ATM call and ATM put, strike nearest BTCUSD futures.",
           "Skip the day when the ATM put mark is below $20, or BTC futures are up more than 2% since 17:31 IST yesterday.",
           "Size: each leg gets half the equity × leverage ÷ BTC futures (compounded: the equity after the last closed trade; lot 0.001 BTC).",
           "Stop: each leg on its own, bought back when its mark reaches stop multiple × its sell price; the other leg stays open.",
           "Open legs settle at the 17:30 IST expiry at Delta's official settlement price."],
}


async def dashboard(bot="sb"):
    st, cfg = await get_state(bot), await settings(bot)
    eng = ENGINES[bot]
    mode = st["mode"]
    rows = await live_journal(mode, cfg)
    start_cap = cfg["starting_capital"]
    live = {"metrics": sc.metrics(rows, start_cap, rows[0]["date"], rows[-1]["date"]) if rows else sc.metrics([], start_cap), "monthly": sc.monthly(rows, start_cap), "curve": sc.curve(rows),
            "avg_entry_slip": sc._avg([r["entry_slip"] for r in rows if r.get("entry_slip") is not None]),
            "avg_exit_slip": sc._avg([r["exit_slip"] for r in rows if r.get("exit_slip") is not None and r.get("exit_reason") in ("Stop", "Stop+Expiry")]),
            "avg_latency_ms": sc._avg([r["latency_ms"] for r in rows if r.get("latency_ms") is not None]), "win_check": sc.recent_win_alert(rows)}
    import delta_broker
    paper_days = await C(bot, "trades").count_documents({"mode": "PAPER", "status": {"$in": ["CLOSED", "SKIPPED"]}, **NOT_DRILL})
    today = await C(bot, "trades").find_one({"_id": f"{now_utc().strftime('%Y-%m-%d')}:{mode}"}, {"legs": 0})
    now = now_utc()
    nxt = _at(now.date(), eng.entry_t)
    if now >= nxt:
        nxt += timedelta(days=1)
    bots = []
    for b in BOTS:
        s2 = st if b == bot else await get_state(b)
        bots.append({"key": b, "name": BOTS[b]["name"], "mode": s2["mode"], "enabled": s2.get("enabled", True), "halted": bool(s2.get("halted")),
                     "recommended": b == "op", "entry_ist": _ist(_at(now.date(), BOTS[b]["entry"])).strftime("%H:%M:%S")})
    return {
        "bot": bot, "bot_name": BOTS[bot]["name"], "bots": bots, "rules": RULES[bot], "leg_share": LEG_SHARE[bot],
        "variants": list(OP_VARIANTS if bot == "op" else sc.VARIANTS), "entry_ist": _ist(_at(now.date(), eng.entry_t)).strftime("%H:%M:%S"),
        "mode": mode, "enabled": st.get("enabled", True), "halted": bool(st.get("halted")), "halt_reason": st.get("halt_reason"), "settings": cfg,
        "bounds": BOUNDS, "choices": CHOICES, "defaults": defaults(bot), "server_time": iso(now), "next_entry": iso(nxt),
        "running": alive(bot), "own_thread": BOT_LOOP is not None, "lag_ms": round(LAG["max_ms"], 1), "drift_ms": eng.drift_ms, "drift_source": eng.drift_src,
        "view": eng.view, "today": today, "effective_leverage": core_cfg(cfg, mode)["leverage"], "equity": rows[-1]["equity"] if rows else start_cap,
        "ready": {"paper_days": paper_days, "paper_days_needed": PAPER_DAYS_BEFORE_LIVE, "testnet_key": bool(delta_broker.keys("TESTNET")), "live_key": bool(delta_broker.keys("LIVE")),
                  "conn_ok_TESTNET": st.get("conn_ok_TESTNET"), "conn_ok_LIVE": st.get("conn_ok_LIVE"), "drill_ok_TESTNET": st.get("drill_ok_TESTNET"),
                  "drill_waived": bool(st.get("drill_waived")), "quiet": _quiet_window(now),
                  "other_on_exchange": next((f"{x['name']} is on {x['mode']}" for x in bots if x["key"] != bot and x["mode"] != "PAPER"), None)},
        "live": live,
        "checks": [c async for c in C(bot, "checks").find({}, {"_id": 0}).sort("at", -1).limit(6)],
        "events": [e async for e in C(bot, "events").find({}, {"_id": 0}).sort("at", -1).limit(40)],
        "changes": [c async for c in C(bot, "changes").find({}, {"_id": 0}).sort("at", -1).limit(30)],
    }


async def backtest_view(bot="sb"):
    """The backtest variants without their journals (those are paged separately)."""
    bt = await backtest(bot)
    return {"bot": bot, "source": bt["source"], "inputs": bt["inputs"], "order": list(bt["variants"]),
            "variants": {k: {x: v[x] for x in ("name", "metrics", "monthly", "curve")} for k, v in bt["variants"].items()}}


def columns(bot, live=False):
    if bot == "op":                                                        # its backtest records BTC futures there; the bot records the index
        base = [(k, "BTC price at entry (fee notional)" if k == "spot" else lab) for k, lab in sc.COLUMNS]
        return base + sc.OP_EXTRA + (sc.LIVE_EXTRA if live else [])
    return sc.COLUMNS + (sc.LIVE_EXTRA if live else [])


async def journal_rows(src, bot="sb"):
    if src in (OP_VARIANTS if bot == "op" else sc.VARIANTS):
        return (await backtest(bot))["variants"][src]["rows"], columns(bot)
    if src in MODES:
        return await live_journal(src, await settings(bot)), columns(bot, True)
    raise ValueError("Unknown journal")


async def journal_page(src, offset=0, limit=100, newest_first=True, bot="sb"):
    rows, cols = await journal_rows(src, bot)
    rows = list(reversed(rows)) if newest_first else rows
    return {"src": src, "bot": bot, "columns": [{"key": k, "label": lab} for k, lab in cols], "total": len(rows), "offset": offset, "rows": rows[offset:offset + limit]}


async def journal_csv(src, bot="sb"):
    rows, cols = await journal_rows(src, bot)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([lab for _, lab in cols])
    for r in rows:
        w.writerow(["; ".join(map(str, r[k])) if isinstance(r.get(k), list) else ("" if r.get(k) is None else r[k]) for k, _ in cols])
    return buf.getvalue()
