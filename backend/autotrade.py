"""Auto-trade – "Plan A – 4C Reverse 1:2 + 6-Hour Exit (No Swing)" on Delta Exchange India BTCUSD.

Reads this server's own SCALP signals (read-only – the signal logic is not touched) and trades the
OPPOSITE side, one trade at a time, with a daily loss limit and a daily profit target.

Modes
  PAPER    default. Simulated fills from the live Delta price; never calls an order endpoint.
  TESTNET  real orders on Delta's test exchange (delta_broker.DeltaBroker).
  LIVE     real orders. Only an admin can switch to it, by typing LIVE, after a completed TESTNET trade.

For every scalp signal with model entry E and model stop S:  D = |E − S| × stop multiple
  model SHORT → bot LONG : stop E − D, target E + RR·D        model LONG → bot SHORT : stop E + D, target E − RR·D
  enter at market when the last traded price reaches E (LONG: ≥ E, SHORT: ≤ E) inside the entry window;
  skip if price is already more than the slippage guard beyond E; exit on stop, target, 6-hour time exit,
  a daily limit, or "Close everything now".

Everything is stored in MongoDB, so a restart continues where it stopped; real modes first reconcile with
Delta (position + orders) and never place a second entry for a signal (signal ID = client order ID).
"""
import asyncio
import html
import logging
import math
from datetime import timedelta

from pymongo.errors import DuplicateKeyError

from core import db, now_utc, iso, parse_iso, ist_date, age_sec

log = logging.getLogger("autotrade")
CV, TICK = 0.001, 0.5  # BTC per contract, price tick
FEES = {"maker": 0.0002, "taker": 0.0005, "gst": 0.18}
MODES = ("PAPER", "TESTNET", "LIVE")

DEFAULTS = {"margin_inr": 40000.0, "leverage": 10, "daily_loss_usd": 84.0, "daily_target_usd": 225.0, "max_contracts": 100,
            "entry_window_min": 10.0, "slippage_pts": 40.0, "time_exit_min": 360.0, "rr": 2.0, "stop_mult": 1.0,
            "usd_inr": 85.0, "buffer_inr": 5000.0, "paper_balance_inr": 45000.0, "tp_limit": 0, "direction_mode": 2}
RANGES = {"margin_inr": (1000, 5_000_000), "leverage": (1, 100), "daily_loss_usd": (1, 100_000), "daily_target_usd": (1, 1_000_000),
          "max_contracts": (1, 5000), "entry_window_min": (1, 60), "slippage_pts": (0, 2000), "time_exit_min": (5, 2880),
          "rr": (0.25, 10), "stop_mult": (0.25, 5), "usd_inr": (50, 200), "buffer_inr": (0, 1_000_000),
          "paper_balance_inr": (1000, 100_000_000), "tp_limit": (0, 1), "direction_mode": (0, 2)}
_INTS = ("leverage", "max_contracts", "tp_limit", "direction_mode")
PLAN_NAME = {"PLAN_A": "Plan A (reverse)", "FOLLOW": "Follow the signal"}


class NotPlaced(Exception):
    """The entry order is known NOT to exist on the exchange."""


# ---------------- pure maths (unit-tested) ----------------
def tick_round(x):
    return round(x / TICK) * TICK


def levels(model_dir, entry, stop, rr=2.0, stop_mult=1.0):
    """Reverse 1:RR plan for a model signal. Prices rounded to the 0.5 tick."""
    d = abs(entry - stop) * stop_mult
    if model_dir == "SHORT":
        return {"bot_dir": "LONG", "entry": tick_round(entry), "stop": tick_round(entry - d), "target": tick_round(entry + rr * d), "d": d}
    return {"bot_dir": "SHORT", "entry": tick_round(entry), "stop": tick_round(entry + d), "target": tick_round(entry - rr * d), "d": d}


def _usd(v):
    return f"{'+' if v >= 0 else '−'}${abs(v):,.2f}"


def follow_levels(model_dir, entry, stop, target):
    """Follow the signal: its own direction, stop and target (prices rounded to the 0.5 tick)."""
    return {"bot_dir": model_dir, "entry": tick_round(entry), "stop": tick_round(stop), "target": tick_round(target), "d": abs(entry - stop)}


def choose_plan(mode, signal_dir):
    """direction_mode 0 = Plan A always, 1 = follow always, 2 = by day type (range → Plan A, trend with the signal → follow, else none)."""
    if int(mode) == 0:
        return "PLAN_A", None
    if int(mode) == 1:
        return "FOLLOW", None
    import daytype
    day = daytype.public()
    return daytype.playbook(day, signal_dir), day.get("label")


def fee(price, contracts, kind):
    return price * contracts * CV * FEES[kind] * (1 + FEES["gst"])


def size(price, stop, cfg, available_inr, realized_today):
    """Contracts for a new trade: ₹ margin × leverage, capped by free balance, max contracts and the
    room left under today's loss limit (stop distance + entry and exit taker fees)."""
    margin_inr = min(cfg["margin_inr"], max(0.0, available_inr - cfg["buffer_inr"]))
    by_margin = math.floor(margin_inr / cfg["usd_inr"] * cfg["leverage"] / (price * CV) + 1e-9)
    per = abs(price - stop) * CV + fee(price, 1, "taker") + fee(stop, 1, "taker")
    room = cfg["daily_loss_usd"] + realized_today
    by_loss = math.floor(room / per + 1e-9) if room > 0 and per > 0 else 0
    n = max(0, min(by_margin, by_loss, int(cfg["max_contracts"])))
    return n, {"by_margin": by_margin, "by_loss_room": by_loss, "worst_per_contract": per, "loss_room": room,
               "limited_by": "daily loss room" if n == by_loss and by_loss < by_margin else "max contracts" if n == int(cfg["max_contracts"]) and n < by_margin else "margin"}


# ---------------- settings / state ----------------
async def settings():
    doc = await db.settings.find_one({"_id": "autotrade"}) or {}
    return {**DEFAULTS, **{k: v for k, v in (doc.get("values") or {}).items() if k in DEFAULTS}}


async def save_settings(values, by):
    cur = await settings()
    for k, v in (values or {}).items():
        if k not in DEFAULTS:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{k} must be a number")
        lo, hi = RANGES[k]
        if not math.isfinite(v) or not lo <= v <= hi:
            raise ValueError(f"{k} must be between {lo} and {hi}")
        cur[k] = int(v) if k in _INTS else v
    await db.settings.update_one({"_id": "autotrade"}, {"$set": {"values": cur, "updated_at": iso(now_utc()), "updated_by": by}}, upsert=True)
    return cur


async def get_state(now=None):
    st = await db.autotrade.find_one({"_id": "state"})
    if not st:
        st = {"_id": "state", "mode": "PAPER", "paused": False, "halt": None, "since": iso(now or now_utc()), "testnet_ok": False}
        await db.autotrade.insert_one(st)
    return st


async def set_state(**kw):
    await db.autotrade.update_one({"_id": "state"}, {"$set": kw})


async def ensure_indexes():
    await db.autotrade_trades.create_index("status")
    await db.autotrade_signals.create_index("status")
    await db.autotrade_events.create_index("at")


# ---------------- alerts ----------------
_BG = set()


async def _deliver(subject, text):
    import alerts
    import messaging
    from channel_config import CFG
    jobs = []
    if alerts.email_provider() and CFG.get("ALERT_EMAIL"):
        jobs.append(alerts.send_email(to=CFG["ALERT_EMAIL"], subject=subject, html=f"<p>{html.escape(text)}</p>"))
    if messaging.telegram_configured():
        jobs.append(messaging.send_telegram(f"{subject}\n{text}"))
    if CFG.get("PUSHOVER_USER_KEY") and CFG.get("PUSHOVER_APP_TOKEN"):
        import httpx

        async def push():
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post("https://api.pushover.net/1/messages.json", data={"token": CFG["PUSHOVER_APP_TOKEN"], "user": CFG["PUSHOVER_USER_KEY"], "title": subject[:240], "message": text[:1000]})
                if r.status_code >= 400:
                    raise RuntimeError(f"Pushover refused ({r.status_code})")
        jobs.append(push())
    for r in await asyncio.gather(*jobs, return_exceptions=True):
        if isinstance(r, Exception):
            log.warning("auto-trade alert failed: %s", r)


async def send_alert(mode, title, text):
    """Entry / exit / limit / error → every configured channel (email, Telegram, Pushover).
    PAPER follows the site's alert switch; TESTNET and LIVE always send. Never blocks trading."""
    try:
        if mode == "PAPER":
            import alert_mode
            if not (await alert_mode.get_mode()).get("effective_armed"):
                return
        t = asyncio.create_task(_deliver(f"[{mode}] Vantage BTC auto-trade: {title}", text))
        _BG.add(t)
        t.add_done_callback(_BG.discard)
    except Exception as e:  # alerts must never break the engine
        log.warning("auto-trade alert error: %s", e)


# ---------------- paper broker ----------------
class PaperBroker:
    """Simulated exchange: fills at the live price, bracket watched by the engine. No network calls."""
    name = "PAPER"

    async def available_inr(self, cfg):
        net = 0.0
        async for t in db.autotrade_trades.find({"mode": "PAPER", "status": "CLOSED"}, {"net_inr": 1}):
            net += t.get("net_inr") or 0
        return cfg["paper_balance_inr"] + net

    async def prepare(self, cfg):
        return None

    async def enter(self, side, contracts, stop, target, coid, price, cfg):
        return {"price": price, "contracts": contracts, "role": "taker"}

    async def find_entry(self, coid):
        raise NotPlaced("paper order was never placed")

    async def confirm_bracket(self, trade, timeout=5):
        return True

    async def position(self):
        t = await db.autotrade_trades.find_one({"mode": "PAPER", "status": "OPEN"})
        return {"size": t["contracts"] * (1 if t["bot_dir"] == "LONG" else -1), "entry": t["entry"]} if t else None

    async def poll_exit(self, t, price, hi, lo, cfg):
        long = t["bot_dir"] == "LONG"
        if (lo <= t["stop"]) if long else (hi >= t["stop"]):  # stop first when both are touched (same as the workbook)
            return {"reason": "STOP", "price": min(t["stop"], price) if long else max(t["stop"], price), "role": "taker"}
        if (hi >= t["target"]) if long else (lo <= t["target"]):
            return {"reason": "TARGET", "price": t["target"], "role": "maker" if cfg.get("tp_limit") else "taker"}
        return None

    async def resolve_exit(self, t, price):
        return {"reason": "MANUAL", "price": price, "role": "taker"}

    async def close(self, t, coid, price):
        return {"price": price, "role": "taker"}

    async def cancel_all(self):
        return None


def default_broker(mode):
    if mode == "PAPER":
        return PaperBroker()
    import delta_broker
    return delta_broker.get(mode)


# ---------------- engine ----------------
class Engine:
    def __init__(self, broker_factory=None, clock=now_utc, alert=None):
        self.broker_factory = broker_factory or default_broker
        self.clock = clock
        self.alert = alert or send_alert
        self.reconciled = False
        self.lock = asyncio.Lock()
        self.last_price = None
        self._close_err = None

    # ---- helpers
    async def event(self, kind, text, sid=None, level="info", alert=None, mode=None):
        mode = mode or (await get_state(self.clock()))["mode"]
        await db.autotrade_events.insert_one({"at": iso(self.clock()), "kind": kind, "text": text, "signal_id": sid, "level": level, "mode": mode})
        if alert:
            await self.alert(mode, alert, text)

    async def _mark(self, sid, status, reason, kind=None, level="info"):
        await db.autotrade_signals.update_one({"_id": sid}, {"$set": {"status": status, "reason": reason, "done_at": iso(self.clock())}})
        await self.event(kind or status.lower().replace(" ", "_"), f"{sid}: {status} – {reason}", sid, level)

    async def realized(self, mode, day):
        tot = 0.0
        async for t in db.autotrade_trades.find({"mode": mode, "status": "CLOSED", "day": day}, {"net_usd": 1}):
            tot += t.get("net_usd") or 0
        return tot

    @staticmethod
    def unrealized(t, price):
        """Net of the entry fee already paid and the taker fee a market exit would cost."""
        sign = 1 if t["bot_dir"] == "LONG" else -1
        return (price - t["entry"]) * t["contracts"] * CV * sign - t["entry_fee"] - fee(price, t["contracts"], "taker")

    # ---- one engine step
    async def tick(self, price, hi=None, lo=None):
        async with self.lock:
            hi, lo = max(hi or price, price), min(lo or price, price)
            self.last_price = price
            now = self.clock()
            st, cfg = await get_state(now), await settings()
            broker = self.broker_factory(st["mode"])
            if not self.reconciled:
                await self._reconcile(st, cfg, broker, price, now)
                self.reconciled = True
                st = await get_state(now)
            if st.get("halt") and st["halt"]["day"] != ist_date(now):
                await set_state(halt=None)
                st["halt"] = None
                await self.event("day", "New trading day (00:00 IST): daily limits reset")
                await db.autotrade_events.delete_many({"at": {"$lt": iso(now - timedelta(days=90))}})
            await self._ingest(st, cfg, now)
            pend = await db.autotrade_trades.find_one({"status": "PENDING"})
            if pend:
                await self._resolve_pending(pend, st, cfg, broker, now)
                return
            t = await db.autotrade_trades.find_one({"status": "OPEN"})
            if t:
                await self._manage(t, st, cfg, broker, price, hi, lo, now)
                return
            await self._limit_check(st, cfg, broker, now)
            st = await get_state(now)
            if not st.get("halt") and not st.get("paused"):
                await self._try_enter(st, cfg, broker, price, hi, lo, now)

    # ---- signals
    async def _ingest(self, st, cfg, now):
        since = parse_iso(st["since"])
        floor = iso(max(since, now - timedelta(hours=24)) - timedelta(seconds=2))
        seen = {x["_id"] async for x in db.autotrade_signals.find({"captured_at": {"$gte": floor}}, {"_id": 1})}
        async for s in db.signals.find({"captured_at": {"$gte": floor}}):
            sid = s["_id"]
            if sid in seen or parse_iso(s["captured_at"]) < since:
                continue
            doc = {"_id": sid, "strategy": s.get("strategy"), "model_dir": s.get("direction"), "captured_at": s["captured_at"], "seen_at": iso(now)}
            open_t = await db.autotrade_trades.find_one({"status": {"$in": ["OPEN", "PENDING"]}}, {"_id": 1})
            if s.get("strategy") != "scalp":
                doc.update(status="IGNORED", reason="ignored – swing")
            elif open_t:
                doc.update(status="SKIPPED", reason="skipped – trade open")
            elif st.get("halt"):
                doc.update(status="SKIPPED", reason=f"skipped – {st['halt']['reason']}")
            elif st.get("paused"):
                doc.update(status="SKIPPED", reason="skipped – paused")
            else:
                plan, day_label = choose_plan(cfg.get("direction_mode", 0), s["direction"])
                doc.update(plan=plan, day_type=day_label)
            if doc.get("status"):
                pass
            elif plan == "STAND_ASIDE":
                doc.update(status="SKIPPED", reason=f"skipped – day type is {day_label}: stand aside")
            else:
                lv = follow_levels(s["direction"], float(s["entry"]), float(s["stop"]), float(s["target"])) if plan == "FOLLOW" else levels(s["direction"], float(s["entry"]), float(s["stop"]), cfg["rr"], cfg["stop_mult"])
                cap = parse_iso(s["captured_at"])
                end = cap + timedelta(minutes=cfg["entry_window_min"])
                if s.get("valid_until"):
                    end = min(end, parse_iso(s["valid_until"]))
                doc.update(bot_dir=lv["bot_dir"], entry=lv["entry"], stop=lv["stop"], target=lv["target"], d=lv["d"], window_end=iso(end))
                if now >= end:
                    doc.update(status="NOT FILLED", reason="entry window was already over when the signal was read")
                else:
                    doc.update(status="ARMED", reason=f"waiting for price to reach {lv['entry']:.1f}")
            try:
                await db.autotrade_signals.insert_one(doc)
            except DuplicateKeyError:
                continue  # each signal ID is handled once
            extra = f" – {PLAN_NAME.get(doc.get('plan'), '')}{' on a ' + doc['day_type'] + ' day' if doc.get('day_type') else ''}: bot {doc['bot_dir']} at {doc['entry']:.1f}, stop {doc['stop']:.1f}, target {doc['target']:.1f}" if doc["status"] == "ARMED" else ""
            await self.event(doc["status"].lower().replace(" ", "_"), f"{sid}: {doc['status']} ({doc['reason']}){extra}", sid)

    async def _try_enter(self, st, cfg, broker, price, hi, lo, now):
        armed = [a async for a in db.autotrade_signals.find({"status": "ARMED"}).sort("captured_at", 1)]
        for a in armed:
            sid, up = a["_id"], a["model_dir"] == "SHORT"  # the signal's own limit fills when price comes UP to a SHORT entry / DOWN to a LONG entry
            if now >= parse_iso(a["window_end"]):
                await self._mark(sid, "NOT FILLED", f"price did not reach {a['entry']:.1f} inside the entry window – nothing traded")
                continue
            if not ((hi >= a["entry"]) if up else (lo <= a["entry"])):
                continue
            beyond = (price - a["entry"]) if up else (a["entry"] - price)
            if beyond > cfg["slippage_pts"]:
                await self._mark(sid, "SKIPPED", f"SLIPPAGE – price {price:.1f} is {beyond:.1f} points beyond entry {a['entry']:.1f} (guard {cfg['slippage_pts']:.0f})")
                continue
            try:
                avail = await broker.available_inr(cfg)
            except Exception as e:
                await self._mark(sid, "SKIPPED", f"could not read the account balance: {e}", "error", "error")
                await self.alert(st["mode"], "Error", f"{sid}: balance could not be read, signal skipped ({e})")
                continue
            today = ist_date(now)
            n, info = size(price, a["stop"], cfg, avail, await self.realized(st["mode"], today))
            if n < 1:
                await self._mark(sid, "SKIPPED", f"size below 1 contract (limited by {info['limited_by']}; free balance ₹{avail:,.0f}, loss room ${info['loss_room']:.2f})")
                continue
            if await self._enter(a, n, info, st, cfg, broker, price, now):
                return

    async def _enter(self, a, n, info, st, cfg, broker, price, now):
        sid = a["_id"]
        if not await db.autotrade_signals.find_one_and_update({"_id": sid, "status": "ARMED"}, {"$set": {"status": "ENTERING"}}):
            return False
        coid = ("E-" + sid)[:32]
        trade = {"_id": sid, "signal_id": sid, "mode": st["mode"], "status": "PENDING", "model_dir": a["model_dir"], "bot_dir": a["bot_dir"],
                 "captured_at": a["captured_at"], "trigger_entry": a["entry"], "stop": a["stop"], "target": a["target"], "contracts": n,
                 "coid": coid, "created_at": iso(now), "size_note": info["limited_by"], "plan": a.get("plan") or "PLAN_A", "day_type": a.get("day_type")}
        try:
            await db.autotrade_trades.insert_one(trade)  # written BEFORE the order: a crash can never cause a second entry
        except DuplicateKeyError:
            return False
        try:
            try:
                await broker.prepare(cfg)
            except Exception as e:  # nothing was sent yet
                raise NotPlaced(f"account could not be prepared (leverage/margin mode): {e}")
            r = await broker.enter("buy" if a["bot_dir"] == "LONG" else "sell", n, a["stop"], a["target"], coid, price, cfg)
        except NotPlaced as e:
            await db.autotrade_trades.delete_one({"_id": sid, "status": "PENDING"})
            await self._mark(sid, "SKIPPED", f"order was not placed: {e}", "error", "error")
            await self.alert(st["mode"], "Error", f"{sid}: entry order was not placed ({e})")
            return False
        except Exception as e:  # unknown outcome: keep PENDING, check the exchange on the next tick, never re-send
            await self.event("error", f"{sid}: entry order outcome unknown ({e}); checking the exchange before anything else", sid, "error", alert="Error")
            return True
        return await self._opened(trade, r, st, cfg, broker, price, now)

    async def _opened(self, trade, r, st, cfg, broker, price, now):
        sid, filled = trade["_id"], int(r.get("contracts") or 0)
        if filled < 1:
            await db.autotrade_trades.delete_one({"_id": sid, "status": "PENDING"})
            await self._mark(sid, "SKIPPED", "entry order did not fill", "error", "error")
            return False
        upd = {"status": "OPEN", "entry": r["price"], "contracts": filled, "fill_time": iso(now), "entry_fee": fee(r["price"], filled, "taker"),
               "margin_usd": r["price"] * filled * CV / cfg["leverage"], "bracket_confirmed": False}
        await db.autotrade_trades.update_one({"_id": sid}, {"$set": upd})
        trade = {**trade, **upd}
        await db.autotrade_signals.update_one({"_id": sid}, {"$set": {"status": "ENTERED", "reason": "trade opened", "done_at": iso(now)}})
        others = [x["_id"] async for x in db.autotrade_signals.find({"status": "ARMED"}, {"_id": 1})]
        for o in others:
            await self._mark(o, "CANCELLED", "another armed signal was traded first", "cancelled")
        await self.event("entered", f"{sid}: entered {trade['bot_dir']} {filled} contracts ({filled * CV:.3f} BTC) at {r['price']:.1f}; stop {trade['stop']:.1f}, "
                         f"target {trade['target']:.1f}; margin ${upd['margin_usd']:.2f} (₹{upd['margin_usd'] * cfg['usd_inr']:,.0f})", sid, alert="Entry")
        if await broker.confirm_bracket(trade, 5):
            await db.autotrade_trades.update_one({"_id": sid}, {"$set": {"bracket_confirmed": True}})
            await self.event("bracket_confirmed", f"{sid}: stop and target orders confirmed", sid)
        else:
            await self.event("error", f"{sid}: stop/target orders could NOT be confirmed within 5 seconds – closing the position at market", sid, "error", alert="Error – bracket missing")
            await self._close(trade, "SAFETY", st, cfg, broker, price, now)
        return True

    async def _resolve_pending(self, t, st, cfg, broker, now):
        try:
            r = await broker.find_entry(t["coid"])
        except NotPlaced:
            await db.autotrade_trades.delete_one({"_id": t["_id"], "status": "PENDING"})
            await self._mark(t["_id"], "SKIPPED", "entry order was never placed (checked with the exchange)", "error", "error")
            return
        except Exception as e:
            log.warning("pending entry still unknown: %s", e)
            return
        await self._opened(t, r, st, cfg, broker, self.last_price or r["price"], now)

    # ---- open trade
    async def _manage(self, t, st, cfg, broker, price, hi, lo, now):
        ex = await broker.poll_exit(t, price, hi, lo, cfg)
        if ex:
            await self._finish(t, ex["reason"], ex["price"], ex["role"], st, cfg, broker, now)
            return
        if now >= parse_iso(t["fill_time"]) + timedelta(minutes=cfg["time_exit_min"]):
            await self._close(t, "TIME", st, cfg, broker, price, now)
            return
        daily = await self.realized(st["mode"], ist_date(now)) + self.unrealized(t, price)
        why = "Daily loss limit hit" if daily <= -cfg["daily_loss_usd"] else "Daily target reached" if daily >= cfg["daily_target_usd"] else None
        if why:
            await self._close(t, "DAILY LIMIT", st, cfg, broker, price, now, halt=why)

    async def _close(self, t, reason, st, cfg, broker, price, now, halt=None):
        try:
            r = await broker.close(t, ("X-" + t["_id"])[:26] + f"-{now.strftime('%M%S')}", price)
        except Exception as e:  # retried on the next tick; logged and alerted at most once a minute
            if not self._close_err or (now - self._close_err).total_seconds() > 60:
                self._close_err = now
                await self.event("error", f"{t['_id']}: market close ({reason}) failed: {e} – retrying", t["_id"], "error", alert="Error – close failed")
            return
        try:
            await broker.cancel_all()
        except Exception as e:
            await self.event("error", f"cancel-all after close failed: {e}", t["_id"], "error")
        await self._finish(t, r.get("reason") or reason, r["price"], r["role"], st, cfg, broker, now)
        if halt:
            await self._halt(halt, st, broker, now)

    async def _finish(self, t, reason, exit_price, role, st, cfg, broker, now):
        n, sign = t["contracts"], 1 if t["bot_dir"] == "LONG" else -1
        gross = (exit_price - t["entry"]) * n * CV * sign
        fees = t["entry_fee"] + fee(exit_price, n, role)
        net = gross - fees
        await db.autotrade_trades.update_one({"_id": t["_id"]}, {"$set": {"status": "CLOSED", "exit_time": iso(now), "exit_price": exit_price, "exit_reason": reason,
                                                                     "exit_role": role, "gross": gross, "fees": fees, "net_usd": net, "net_inr": net * cfg["usd_inr"], "day": ist_date(now)}})
        if t["mode"] == "TESTNET" and t.get("bracket_confirmed"):
            await set_state(testnet_ok=True)
        await self.event("exited", f"{t['_id']}: exit {reason} at {exit_price:.1f} – net {_usd(net)} ({'+' if net >= 0 else '−'}₹{abs(net) * cfg['usd_inr']:,.0f}), gross {_usd(gross)}, fees ${fees:.2f}", t["_id"], alert=f"Exit – {reason}")
        await self._limit_check(st, cfg, broker, now)

    async def _limit_check(self, st, cfg, broker, now):
        """Stop for the day once realized P&L is at or beyond a limit."""
        real = await self.realized(st["mode"], ist_date(now))
        reason = "Daily loss limit hit" if real <= -cfg["daily_loss_usd"] else "Daily target reached" if real >= cfg["daily_target_usd"] else None
        if reason:
            await self._halt(reason, st, broker, now)

    async def _halt(self, reason, st, broker, now):
        if (await get_state(now)).get("halt"):
            return
        real = await self.realized(st["mode"], ist_date(now))
        await set_state(halt={"day": ist_date(now), "reason": reason, "at": iso(now), "pnl": real})
        for a in [x["_id"] async for x in db.autotrade_signals.find({"status": "ARMED"}, {"_id": 1})]:
            await self._mark(a, "SKIPPED", f"skipped – {reason}", "skipped")
        try:
            await broker.cancel_all()
        except Exception as e:
            await self.event("error", f"cancel-all at daily limit failed: {e}", level="error")
        await self.event("limit", f"{reason}: today's P&L {_usd(real)}. No new trades until 00:00 IST.", level="warn", alert=reason)

    # ---- restart safety
    async def _reconcile(self, st, cfg, broker, price, now):
        t = await db.autotrade_trades.find_one({"status": "OPEN"})
        pos = await broker.position()  # raises → tick fails → retried; nothing is traded before this succeeds
        flat = not pos or not pos.get("size")
        if t and flat:
            ex = await broker.resolve_exit(t, price)
            await self.event("restart", f"{t['_id']}: restart – the position was already closed on the exchange; recording the exit", t["_id"], "warn")
            await self._finish(t, ex["reason"], ex["price"], ex["role"], st, cfg, broker, now)
        elif t:
            await self.event("restart", f"{t['_id']}: restart – open trade recovered ({pos['size']} contracts on the exchange); no new order placed", t["_id"])
            if not await broker.confirm_bracket(t, 5):
                await self.event("error", f"{t['_id']}: after restart the stop/target orders are missing – closing at market", t["_id"], "error", alert="Error – bracket missing")
                await self._close(t, "SAFETY", st, cfg, broker, price, now)
        elif not flat:
            await set_state(paused=True)
            await self.event("error", f"The exchange shows a BTCUSD position of {pos['size']} contracts that this bot did not open. Auto-trade is paused and will not touch it.", level="error", alert="Paused – unknown position")

    # ---- controls (admin)
    async def pause(self, by):
        async with self.lock:
            await set_state(paused=True)
            for a in [x["_id"] async for x in db.autotrade_signals.find({"status": "ARMED"}, {"_id": 1})]:
                await self._mark(a, "SKIPPED", "skipped – paused", "skipped")
            await self.event("control", f"New trades paused by {by}. An open trade stays under its stop and target.")

    async def resume(self, by):
        async with self.lock:
            await set_state(paused=False)
            await self.event("control", f"Resumed by {by}")

    async def close_everything(self, by):
        async with self.lock:
            now = self.clock()
            st, cfg = await get_state(now), await settings()
            broker = self.broker_factory(st["mode"])
            await set_state(paused=True)
            for a in [x["_id"] async for x in db.autotrade_signals.find({"status": "ARMED"}, {"_id": 1})]:
                await self._mark(a, "CANCELLED", "closed manually", "cancelled")
            t = await db.autotrade_trades.find_one({"status": "OPEN"})
            if t:
                if self.last_price is None:
                    raise ValueError("No live price yet – try again in a few seconds")
                await self._close(t, "MANUAL", st, cfg, broker, self.last_price, now)
            else:
                try:
                    await broker.cancel_all()
                except Exception as e:
                    await self.event("error", f"cancel-all failed: {e}", level="error")
            await self.event("control", f"Close everything now – by {by}. Auto-trade is paused; press Resume to continue.", level="warn", alert="Closed manually")

    async def set_mode(self, mode, confirm, by):
        import delta_broker
        async with self.lock:
            now = self.clock()
            st, cfg = await get_state(now), await settings()
            if mode not in MODES:
                raise ValueError("Unknown mode")
            if mode == st["mode"]:
                return
            if await db.autotrade_trades.find_one({"status": {"$in": ["OPEN", "PENDING"]}}) or await db.autotrade_signals.find_one({"status": "ARMED"}):
                raise ValueError("Close the open trade (or wait for armed signals to finish) before changing mode")
            if mode != "PAPER":
                if not delta_broker.keys(mode):
                    k = delta_broker.ENV[mode]
                    raise ValueError(f"The {mode} API key is not on the server yet (add {k[0]} and {k[1]} to the server's .env file)")
                if mode == "LIVE":
                    if (confirm or "").strip() != "LIVE":
                        raise ValueError("Type LIVE to confirm")
                    if not st.get("testnet_ok"):
                        raise ValueError("Complete at least one TESTNET trade (entry, bracket confirmed, exit) before LIVE")
                await delta_broker.get(mode).preflight(cfg)  # auth, IP whitelist, product, flat account, leverage
            await set_state(mode=mode, since=iso(now), halt=None)
            self.reconciled = False
            await self.event("mode", f"Mode changed {st['mode']} → {mode} by {by}", level="warn", alert=f"Mode {mode}", mode=mode)


ENGINE = Engine()


# ---------------- status for the dashboard ----------------
async def status():
    import delta_broker
    now = now_utc()
    st, cfg = await get_state(now), await settings()
    price = ENGINE.last_price
    t = await db.autotrade_trades.find_one({"status": "OPEN"})
    armed = [a async for a in db.autotrade_signals.find({"status": "ARMED"}).sort("captured_at", 1)]
    real = await ENGINE.realized(st["mode"], ist_date(now))
    unreal = Engine.unrealized(t, price) if t and price else 0.0
    label = "Limit hit" if st.get("halt") else "Paused" if st.get("paused") else "In trade" if t else "Armed" if armed else "Idle"
    open_trade = None
    if t:
        left = (parse_iso(t["fill_time"]) + timedelta(minutes=cfg["time_exit_min"]) - now).total_seconds()
        open_trade = {**{k: t.get(k) for k in ("signal_id", "model_dir", "bot_dir", "entry", "trigger_entry", "stop", "target", "contracts", "margin_usd", "fill_time", "bracket_confirmed", "plan", "day_type")},
                      "unrealized": unreal, "time_left_sec": max(0, left)}
    try:
        balance_inr = await PaperBroker().available_inr(cfg) if st["mode"] == "PAPER" else None
    except Exception:
        balance_inr = None
    return {
        "mode": st["mode"], "status": label, "paused": bool(st.get("paused")), "halt": st.get("halt"), "price": price, "server_time": iso(now),
        "settings": cfg, "ranges": RANGES,
        "today": {"day": ist_date(now), "realized": real, "unrealized": unreal, "total": real + unreal,
                  "loss_limit": cfg["daily_loss_usd"], "target": cfg["daily_target_usd"], "usd_inr": cfg["usd_inr"]},
        "paper_balance_inr": balance_inr, "open_trade": open_trade,
        "armed": [{k: a.get(k) for k in ("_id", "model_dir", "bot_dir", "entry", "stop", "target", "captured_at", "window_end", "plan", "day_type")} for a in armed],
        "journal": [{k: v for k, v in x.items() if k != "_id"} async for x in db.autotrade_trades.find({"status": "CLOSED"}).sort("exit_time", -1).limit(200)],
        "events": [{k: v for k, v in x.items() if k != "_id"} async for x in db.autotrade_events.find().sort("at", -1).limit(150)],
        "signals": [x async for x in db.autotrade_signals.find({"status": {"$nin": ["ARMED"]}}).sort("captured_at", -1).limit(30)],
        "live_ready": {"testnet_key": bool(delta_broker.keys("TESTNET")), "live_key": bool(delta_broker.keys("LIVE")), "testnet_trade_done": bool(st.get("testnet_ok"))},
    }


JOURNAL_COLS = ["signal_id", "mode", "plan", "day_type", "model_dir", "bot_dir", "captured_at", "fill_time", "entry", "stop", "target", "contracts",
                "exit_time", "exit_reason", "exit_price", "gross", "fees", "net_usd", "net_inr"]


async def journal_csv():
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(JOURNAL_COLS)
    async for t in db.autotrade_trades.find({"status": "CLOSED"}).sort("exit_time", 1):
        w.writerow([round(t[c], 2) if isinstance(t.get(c), float) else t.get(c, "") for c in JOURNAL_COLS])
    return buf.getvalue()


# ---------------- price feed + loop ----------------
_seen = {"ts": 0.0}


def ws_price():
    """(last, high, low) of Delta trades since the previous call, from the live websocket; None when stale."""
    from sources import WS
    if WS.get("status") != "connected" or not WS.get("last_msg") or age_sec(parse_iso(WS["last_msg"])) > 5:
        return None
    tr = WS.get("trades") or []
    if not tr:
        last = (WS.get("quote") or {}).get("last")
        return (last, last, last) if last else None
    new = [x["price"] for x in tr if x["ts"] > _seen["ts"]] if _seen["ts"] else []
    _seen["ts"] = tr[-1]["ts"]
    last = tr[-1]["price"]
    return last, max(new + [last]), min(new + [last])


async def rest_price():
    from sources import http_get, DELTA
    p = float((await http_get(f"{DELTA}/v2/tickers/BTCUSD")).json()["result"]["close"])
    return p, p, p


async def loop():
    await ensure_indexes()
    fails = 0
    while True:
        try:
            p = ws_price() or await rest_price()  # websocket last traded price; REST ticker every second as fallback
            await ENGINE.tick(*p)
            fails = 0
        except Exception as e:
            fails += 1
            log.warning("auto-trade tick failed (%s): %s", fails, e)
            if fails in (10, 120):
                try:
                    await ENGINE.event("error", f"Auto-trade engine has failed {fails} times in a row: {e}", level="error", alert="Error")
                except Exception:
                    pass
        await asyncio.sleep(1 if fails < 5 else min(30, fails))
