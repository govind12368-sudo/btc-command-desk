"""Elite 4 Edge: ONE trade at a time for the whole account, four strategies (owner spec 23 Nov 2026).

  Priority on the same candle: P&L #1, Row 9, P&L #5, Row 18 (P&L #5 is kept even though P&L #1 always wins its candle).
  1. P&L #1  4 candles in a row, follow · 4h · 1:3 · no filter                     (runs.py)
  2. Row 9   SMA 9/21 cross · 1h · 1:3 · skipped when the market is Range          (trio.py)
  3. P&L #5  4 candles in a row, follow · 4h · 1:2 · no filter                     (runs.py)
  4. Row 18  CCI 50 back from ±100 · 4h · 1:2 · Reverse · no SELL in Up, no BUY in Down (trio.py)
  Market type = BTC now vs 30 days ago: Up > +5%, Down < -5%, else Range; unknown (first 30 days) = no filter.

Signal at a candle close, entry at the next 5-minute open (backtest) / at the live price (paper and real), stop 2 x ATR(14),
target R:R x stop, time exit 7 days (4h) / 72 h (1h), stop first. $10,000 at 10x ($1,000 margin), size rounded down to
0.001 BTC, Delta taker 0.05% each side + 18% GST. While a trade is open every new signal is skipped.

Journals: A = 1 Jan 2023 -> today (history file made by the strategy lab with this code: Binance BTCUSDT 5m until 31 Jan 2024,
Delta BTCUSD after; then the Delta candles of this server from 1 Oct 2026), B = 1 Oct 2026 -> today, C = live from the first start.
Real money: PAPER by default. TESTNET / LIVE only by an admin, typed confirmation, keys from the server environment only,
Auto-Trade not in TESTNET/LIVE, one completed Elite TESTNET trade before LIVE. Drawdown stop: closed + open P&L from its peak.
"""
import asyncio
import json
import logging
import math
import os
import time
from collections import defaultdict
from datetime import datetime

import runs
import trio
import autotrade
from autotrade import NotPlaced
from core import db, iso, now_utc, parse_iso

log = logging.getLogger("elite")
NAME = "Elite 4 Edge"
ORDER = ["pl1", "sma", "pl5", "cci"]
STRATS = {**{s["key"]: s for s in runs.STRATS if s["key"] in ORDER}, **{s["key"]: s for s in trio.STRATS if s["key"] in ORDER}}
MODULE = {"pl1": runs, "pl5": runs, "sma": trio, "cci": trio}
RULE = {"pl1": "No filter", "pl5": "No filter", "sma": "Skip when the market is Range (BTC within ±5% over 30 days)",
        "cci": "Only with the 30-day trend: no SELL in an Up market, no BUY in a Down market"}
NOTIONAL, LEVERAGE, CV = 10_000.0, 10, 0.001
FEE = {"taker": 0.0005, "maker": 0.0002, "gst": 0.18}
PAPER_CAPITAL = float(os.environ.get("ELITE_PAPER_CAPITAL", 5000))
DD_LIMIT = float(os.environ.get("ELITE_DD_LIMIT", -4000))
REPLAY_IST = "2026-10-01 00:00"                  # journal B start; server candles replace the history file from here
LATE_SEC = 15 * 60                               # a live signal older than this when seen is not traded
MODES = ("PAPER", "TESTNET", "LIVE")
ENV = {"LIVE": ("ELITE_DELTA_API_KEY", "ELITE_DELTA_API_SECRET", "DELTA_TRADE_API_KEY", "DELTA_TRADE_API_SECRET"),
       "TESTNET": ("ELITE_DELTA_TESTNET_API_KEY", "ELITE_DELTA_TESTNET_API_SECRET", "DELTA_TESTNET_API_KEY", "DELTA_TESTNET_API_SECRET")}
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "elite_history.json")
STATE = {"computed_at": None, "error": None, "data": None, "bars5": [], "frames": None, "frames_t": None}
_HIST = {}


# ================================================================ strategy logic (pure, also used by the lab)
def fee(price, q, kind="taker"):
    return price * q * FEE[kind] * (1 + FEE["gst"])


def size_btc(price):
    return math.floor(NOTIONAL / price * 1000) / 1000


def rule_ok(key, sig):
    """(allowed, reason) for a signal (t, side, d, why, chg30)."""
    side, ch = sig[1], sig[4]
    mk = runs.market_type(ch)
    if key == "sma" and mk == "Range":
        return False, f"skipped – Range market (BTC {ch:+.1%} over 30 days)"
    if key == "cci" and side < 0 and mk == "Up":
        return False, f"skipped – SELL against an Up market (BTC {ch:+.1%} over 30 days)"
    if key == "cci" and side > 0 and mk == "Down":
        return False, f"skipped – BUY against a Down market (BTC {ch:+.1%} over 30 days)"
    return True, ""


def frames(b5):
    out = {}
    for tf in runs.TF:
        bs = runs.bars(b5, tf)
        out[tf] = (bs, runs.atr(bs), runs.chg30(bs))
    return out


def candidates(fr, since_t=0, until_t=None):
    """Every signal of the four strategies (allowed or not), oldest first, priority order inside a candle."""
    out = []
    for key in ORDER:
        s = STRATS[key]
        bs, at, ch = fr[s["tf"]]
        for x in MODULE[key].signals(s, bs, at, ch):
            if x[0] < since_t or (until_t is not None and x[0] > until_t):
                continue
            ok, why = rule_ok(key, x)
            out.append({"t": x[0], "key": key, "sig": x, "ok": ok, "skip": why})
    out.sort(key=lambda g: (g["t"], ORDER.index(g["key"])))
    return out


def book(cands, b5, start_t, live):
    """One trade at a time for the account. Returns (trades, skipped signals)."""
    rows, skips, busy, by = [], [], -1, None
    for g in cands:
        if g["t"] < start_t:
            continue
        if not g["ok"]:
            skips.append({**_sigrow(g), "reason": g["skip"]})
            continue
        if g["t"] < busy:
            skips.append({**_sigrow(g), "reason": f"skipped – trade already open ({by})"})
            continue
        res = runs.simulate(STRATS[g["key"]], [g["sig"]], b5, 0, live)
        if not res:
            continue                               # data gap at the entry, or the entry candle is not in the data yet
        r = res[0]
        rows.append(r)
        by = f"{r['wb']} {r['side']} from {r['entry_ist']}"
        busy = r["exit_t"] if r["status"] == "CLOSED" else 10 ** 13
    return rows, skips


def _sigrow(g):
    s = STRATS[g["key"]]
    return {"t": g["t"], "ist": runs._ist(g["t"]), "key": g["key"], "wb": s["wb"], "side": "LONG" if g["sig"][1] > 0 else "SHORT", "why": g["sig"][3]}


def _ym(t):
    d = datetime.fromtimestamp(t, runs.IST)
    return d.year, f"{d.year}-{d.month:02d}"


def _dd(rows):
    eq = pk = dd = 0.0
    for r in sorted(rows, key=lambda r: r["exit_t"]):
        eq += r["net"]; pk = max(pk, eq); dd = min(dd, eq - pk)
    return round(dd, 2)


def _stats(rows):
    c = [r for r in rows if r["status"] == "CLOSED"]
    w = sum(1 for r in c if r["net"] > 0)
    g, f, n = sum(r["gross"] for r in c), sum(r["fees"] for r in c), sum(r["net"] for r in c)
    return {"trades": len(c), "wins": w, "losses": len(c) - w, "win_rate": w / len(c) if c else None, "gross": round(g, 2), "fees": round(f, 2),
            "net": round(n, 2), "per_trade": round(n / len(c), 2) if c else None, "max_dd": _dd(c)}


def tables(rows):
    """Combined + per-strategy stats, monthly and yearly tables (entry month, IST)."""
    c = sorted([r for r in rows if r["status"] == "CLOSED"], key=lambda r: r["entry_t"])
    per = [{"key": k, "wb": STRATS[k]["wb"], "name": STRATS[k]["name"], **_stats([r for r in c if r["strategy"] == k])} for k in ORDER]
    months, years = defaultdict(list), defaultdict(list)
    for r in c:
        y, m = _ym(r["entry_t"])
        months[m].append(r); years[y].append(r)
    mlist, cum = [], 0.0
    if c:
        y0, m0 = map(int, _ym(c[0]["entry_t"])[1].split("-"))
        y1, m1 = map(int, _ym(int(time.time()))[1].split("-"))
        y, m = y0, m0
        while (y, m) <= (y1, m1):
            key = f"{y}-{m:02d}"
            x = months.get(key, [])
            s = _stats(x); cum += s["net"]
            mlist.append({"month": key, **{k: s[k] for k in ("trades", "wins", "win_rate", "gross", "fees", "net")}, "cumulative": round(cum, 2),
                          "by_strategy": {STRATS[k]["wb"]: round(sum(r["net"] for r in x if r["strategy"] == k), 2) for k in ORDER}})
            m += 1
            if m == 13:
                y, m = y + 1, 1
    ylist, cum = [], 0.0
    for y in sorted(years):
        s = _stats(years[y]); cum += s["net"]
        ylist.append({"year": str(y), **{k: s[k] for k in ("trades", "wins", "win_rate", "gross", "fees", "net", "max_dd")}, "cumulative": round(cum, 2),
                      "by_strategy": {STRATS[k]["wb"]: round(sum(r["net"] for r in years[y] if r["strategy"] == k), 2) for k in ORDER}})
    tot = _stats(c)
    ylist.append({"year": "Total till date", **{k: tot[k] for k in ("trades", "wins", "win_rate", "gross", "fees", "net", "max_dd")}, "cumulative": tot["net"],
                  "by_strategy": {p["wb"]: p["net"] for p in per}})
    return {"combined": tot, "per_strategy": per, "monthly": mlist, "yearly": ylist}


def history():
    if "doc" not in _HIST:
        try:
            with open(HISTORY_FILE) as f:
                _HIST["doc"] = json.load(f)
        except Exception as e:
            log.warning(f"elite history not loaded: {e}")
            _HIST["doc"] = {"rows": []}
    return _HIST["doc"]


KEEP = ("strategy", "wb", "signal_t", "entry_t", "side", "entry", "sl", "tp", "d", "size", "rr", "chg30", "market", "reason", "exit_t",
        "exit_price", "result", "gross", "fees", "net", "status", "hold_h", "mark")


def _slim(r, period):
    x = {k: r.get(k) for k in KEEP}
    x.update(period=period, entry_ist=runs._ist(r["entry_t"]), signal_ist=runs._ist(r["signal_t"]),
             exit_ist=runs._ist(r["exit_t"]) if r.get("exit_t") else None, wb=STRATS[r["strategy"]]["wb"])
    return x


def build(b5, live, fr=None, hist=None):
    """Journals A and B, and the backtest view used for the paper-vs-backtest comparison."""
    replay_t = runs._ts(REPLAY_IST)
    hdoc = history() if hist is None else hist
    old = sorted([r for r in hdoc.get("rows", []) if r["signal_t"] < replay_t], key=lambda r: r["entry_t"])
    fr = fr or frames(b5)
    start = max([replay_t] + [r["exit_t"] for r in old])
    new, skips = book(candidates(fr, replay_t - 86400), b5, start, live)
    rows = [_slim(r, "Backtest history") for r in old] + [_slim(r, "Backtest (server candles)") for r in new]
    A = {**tables(rows), "trades": rows[::-1], "from_ist": "2023-01-01", "source": hdoc.get("source")}
    B_rows = [r for r in rows if r["entry_t"] >= replay_t]
    B = {**tables(B_rows), "trades": B_rows[::-1], "from_ist": REPLAY_IST[:10]}
    return {"A": A, "B": B, "backtest_skips": skips[-400:], "last_candle_ist": runs._ist(b5[-1]["t"] + 300) if b5 else None}


# ================================================================ live engine (paper, testnet, live)
_seen = {"ts": 0.0}


def ws_price():
    """(last, high, low) since this engine's previous call, from the site's Delta websocket (own cursor)."""
    from core import age_sec
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


def env_keys(mode):
    """Elite reads keys ONLY from the server environment (never from keys pasted in the site)."""
    a, b, c, d = ENV[mode]
    k, s = os.environ.get(a, "").strip(), os.environ.get(b, "").strip()
    if not (k and s):
        k, s = os.environ.get(c, "").strip(), os.environ.get(d, "").strip()
    return (k, s) if k and s else None


async def ensure_indexes():
    await db.elite_trades.create_index("status")
    await db.elite_signals.create_index("t")
    await db.elite_events.create_index("at")


async def get_state(now=None):
    st = await db.elite_state.find_one({"_id": "state"})
    if not st:
        now = now or now_utc()
        st = {"_id": "state", "mode": "PAPER", "mode_since": iso(now), "live_start": iso(now), "last_tc": None, "peak": 0.0, "halt": None, "testnet_ok": False}
        await db.elite_state.insert_one(st)
    return st


async def set_state(**kw):
    await db.elite_state.update_one({"_id": "state"}, {"$set": kw}, upsert=True)


class PaperBroker:
    name = "PAPER"

    async def prepare(self, cfg):
        return None

    async def enter(self, side, contracts, stop, target, coid, price, cfg):
        return {"price": price, "contracts": contracts, "role": "taker"}

    async def confirm_bracket(self, trade, timeout=5):
        return True

    async def poll_exit(self, t, price, hi, lo, cfg):
        long = t["side"] == "LONG"
        if (lo <= t["sl"]) if long else (hi >= t["sl"]):          # stop first when both are touched
            return {"reason": "Stop", "price": min(t["sl"], price) if long else max(t["sl"], price), "role": "taker"}
        if (hi >= t["tp"]) if long else (lo <= t["tp"]):
            return {"reason": "Target", "price": t["tp"], "role": "taker"}
        return None

    async def close(self, t, coid, price):
        return {"price": price, "role": "taker"}

    async def cancel_all(self):
        return None

    async def position(self):
        return None


class Engine:
    def __init__(self, clock=now_utc, broker_factory=None, notify=None):
        self.clock, self.lock = clock, asyncio.Lock()
        self.last_price, self.reconciled = None, False
        self.broker_factory = broker_factory or self._broker
        self.notify = notify
        self._brokers = {}

    def _broker(self, mode):
        if mode == "PAPER":
            return PaperBroker()
        if mode not in self._brokers:
            import delta_broker
            k = env_keys(mode)
            if not k:
                raise RuntimeError(f"{mode} keys are not in the server environment")
            self._brokers[mode] = delta_broker.DeltaBroker(mode, keys_override=k)
        return self._brokers[mode]

    @staticmethod
    async def cfg():
        usd_inr = 85.0
        try:
            usd_inr = float((await autotrade.settings()).get("usd_inr", 85.0))
        except Exception:
            pass
        return {"leverage": LEVERAGE, "usd_inr": usd_inr, "tp_limit": False}

    async def _notify(self, kind, t):
        if not self.notify:
            return
        try:
            await self.notify(kind, t)
        except Exception as e:
            log.warning("elite email failed: %s", e)

    async def event(self, kind, text, level="info", **extra):
        await db.elite_events.insert_one({"at": iso(self.clock()), "kind": kind, "level": level, "text": text, **extra})

    # ---- money
    @staticmethod
    def unrealized(t, price):
        if not t or price is None or t.get("status") != "OPEN":
            return 0.0
        sd = 1 if t["side"] == "LONG" else -1
        return sd * (price - t["entry"]) * t["size"] - t["entry_fee"] - fee(price, t["size"])

    async def book_pnl(self, st):
        real = 0.0
        async for t in db.elite_trades.find({"mode": st["mode"], "status": "CLOSED", "fill_time": {"$gte": st["mode_since"]}}, {"net": 1}):
            real += t.get("net") or 0.0
        return real

    # ---- signals from candles
    async def on_candles(self, cands, last_tc):
        """New candle closes: record every signal (taken or skipped with the reason) and take the first allowed one."""
        async with self.lock:
            st = await get_state(self.clock())
            now = self.clock()
            if st.get("last_tc") is None:
                await set_state(last_tc=last_tc)      # first start: never trade signals from before the engine ran
                await self.event("start", f"Elite 4 Edge live engine started in {st['mode']}; signals from candles closing after {runs._ist(last_tc)} IST are traded.")
                return
            new = [g for g in cands if st["last_tc"] < g["t"] <= last_tc]
            for t in sorted({g["t"] for g in new}):
                for g in sorted([x for x in new if x["t"] == t], key=lambda x: ORDER.index(x["key"])):   # priority inside one candle
                    await self._signal(g, st, now)
            await set_state(last_tc=last_tc)

    async def _signal(self, g, st, now):
        sid = f"{g['key']}-{g['t']}"
        if await db.elite_signals.find_one({"_id": sid}, {"_id": 1}):
            return
        doc = {**_sigrow(g), "_id": sid, "seen_at": iso(now), "mode": st["mode"], "d": g["sig"][2], "chg30": g["sig"][4], "market": runs.market_type(g["sig"][4])}
        open_t = await db.elite_trades.find_one({"status": {"$in": ["OPEN", "PENDING"]}})
        st = await get_state(self.clock())
        if not g["ok"]:
            doc.update(status="SKIPPED", reason=g["skip"])
        elif open_t:
            doc.update(status="SKIPPED", reason=f"skipped – trade already open ({open_t['wb']} {open_t['side']} from {open_t['entry_ist']})")
        elif st.get("halt"):
            doc.update(status="SKIPPED", reason=f"skipped – trading stopped ({st['halt']['reason']})")
        elif now.timestamp() - g["t"] > LATE_SEC:
            doc.update(status="SKIPPED", reason="skipped – seen more than 15 minutes after the candle closed (server or data delay)")
        elif self.last_price is None:
            doc.update(status="SKIPPED", reason="skipped – no live price")
        else:
            doc.update(status="TAKING", reason="")
        await db.elite_signals.insert_one(doc)
        if doc["status"] == "TAKING":
            await self._enter(g, sid, st, now)
        else:
            await self.event("skipped", f"{doc['wb']} {doc['side']} at {doc['ist']}: {doc['reason']}")

    async def _enter(self, g, sid, st, now):
        s, price = STRATS[g["key"]], self.last_price
        side, d = (1 if g["sig"][1] > 0 else -1), g["sig"][2]
        n = int(round(size_btc(price) / CV))
        if n < 1:
            await db.elite_signals.update_one({"_id": sid}, {"$set": {"status": "SKIPPED", "reason": "skipped – size below 0.001 BTC"}})
            return False
        sl = round((price - side * d) * 2) / 2                      # Delta tick 0.5
        tp = round((price + side * s["rr"] * d) * 2) / 2
        tr = {"_id": sid, "mode": st["mode"], "status": "PENDING", "strategy": g["key"], "wb": s["wb"], "name": s["name"], "side": "LONG" if side > 0 else "SHORT",
              "signal_t": g["t"], "signal_ist": runs._ist(g["t"]), "d": d, "rr": s["rr"], "sl": sl, "tp": tp, "size": n * CV, "contracts": n,
              "hold_h": s["hold_h"], "reason": g["sig"][3], "market": runs.market_type(g["sig"][4]), "chg30": g["sig"][4], "created_at": iso(now),
              "price_at_signal": price, "coid": ("EL-" + sid)[:32]}
        await db.elite_trades.insert_one(tr)              # written before any order: a crash can never cause a second entry
        broker, cfg = self.broker_factory(st["mode"]), await self.cfg()
        try:
            await broker.prepare(cfg)
            r = await broker.enter("buy" if side > 0 else "sell", n, sl, tp, tr["coid"], price, cfg)
        except Exception as e:
            if isinstance(e, NotPlaced) or st["mode"] == "PAPER":
                await db.elite_trades.delete_one({"_id": sid, "status": "PENDING"})
                await db.elite_signals.update_one({"_id": sid}, {"$set": {"status": "SKIPPED", "reason": f"order not placed: {str(e)[:120]}"}})
                await self.event("error", f"{s['wb']}: entry order was not placed ({e})", "error")
                return False
            await self.event("error", f"{s['wb']}: entry order outcome unknown ({e}); the exchange is checked every second", "error")
            return True
        return await self._opened(tr, r, st, now, broker)

    async def _opened(self, tr, r, st, now, broker):
        n = int(r.get("contracts") or 0)
        if n < 1:
            await db.elite_trades.delete_one({"_id": tr["_id"], "status": "PENDING"})
            await db.elite_signals.update_one({"_id": tr["_id"]}, {"$set": {"status": "SKIPPED", "reason": "entry order did not fill"}})
            return False
        px = float(r["price"])
        upd = {"status": "OPEN", "entry": px, "contracts": n, "size": n * CV, "fill_time": iso(now), "entry_t": int(now.timestamp()),
               "entry_ist": runs._ist(int(now.timestamp())), "entry_fee": fee(px, n * CV), "time_exit_t": int(now.timestamp()) + tr["hold_h"] * 3600,
               "margin": px * n * CV / LEVERAGE, "bracket_confirmed": st["mode"] == "PAPER"}
        await db.elite_trades.update_one({"_id": tr["_id"]}, {"$set": upd})
        await db.elite_signals.update_one({"_id": tr["_id"]}, {"$set": {"status": "TAKEN", "reason": "trade opened"}})
        tr = {**tr, **upd}
        await self.event("entered", f"{tr['wb']}: {tr['side']} {tr['size']:.3f} BTC at {px:,.1f}, stop {tr['sl']:,.1f}, target {tr['tp']:,.1f} ({st['mode']})")
        await self._notify("entry", tr)
        if st["mode"] != "PAPER":
            if await broker.confirm_bracket({**tr, "bot_dir": tr["side"]}, 5):
                await db.elite_trades.update_one({"_id": tr["_id"]}, {"$set": {"bracket_confirmed": True}})
            else:
                await self.event("error", f"{tr['wb']}: stop/target orders could NOT be confirmed – closing at market", "error")
                await self._close(tr, "Safety close", st, broker, self.last_price or px, now)
        return True

    # ---- ticks
    async def tick(self, price, hi=None, lo=None):
        hi = price if hi is None else hi
        lo = price if lo is None else lo
        self.last_price = price
        async with self.lock:
            st = await get_state(self.clock())
            now = self.clock()
            broker = self.broker_factory(st["mode"])
            if not self.reconciled:
                await self._reconcile(st, broker, now)
                self.reconciled = True
            t = await db.elite_trades.find_one({"status": "OPEN"})
            if t:
                ex = None
                try:
                    ex = await broker.poll_exit({**t, "bot_dir": t["side"], "stop": t["sl"], "target": t["tp"]}, price, hi, lo, await self.cfg())
                except Exception as e:
                    log.warning("elite poll_exit: %s", e)
                if ex:
                    reason = ex["reason"].title() if ex["reason"].upper() in ("STOP", "TARGET") else ex["reason"]
                    await self._closed(t, reason, float(ex["price"]), ex.get("role", "taker"), st, now)
                elif now.timestamp() >= t["time_exit_t"]:
                    await self._close(t, "Time exit", st, broker, price, now)
            pend = await db.elite_trades.find_one({"status": "PENDING"})
            if pend and st["mode"] != "PAPER":
                try:
                    r = await broker.find_entry(pend["coid"])
                    await self._opened(pend, r, st, now, broker)
                except Exception as e:
                    if isinstance(e, NotPlaced):
                        await db.elite_trades.delete_one({"_id": pend["_id"], "status": "PENDING"})
                        await db.elite_signals.update_one({"_id": pend["_id"]}, {"$set": {"status": "SKIPPED", "reason": "entry order was never placed"}})
            await self._drawdown(st, broker, price, now)

    async def _drawdown(self, st, broker, price, now):
        if st.get("halt"):
            return
        t = await db.elite_trades.find_one({"status": "OPEN"})
        pnl = await self.book_pnl(st) + self.unrealized(t, price)
        peak = max(float(st.get("peak") or 0.0), pnl)
        if peak > float(st.get("peak") or 0.0) + 0.5:
            await set_state(peak=round(peak, 2))
        if pnl - peak <= DD_LIMIT:
            await set_state(halt={"reason": f"drawdown limit {DD_LIMIT:,.0f} reached (P&L {pnl:,.2f} from a peak of {peak:,.2f})", "at": iso(now), "kind": "drawdown"})
            if t:
                await self._close(t, "Drawdown stop", st, broker, price, now)
            await self.event("halt", f"Trading stopped: drawdown {pnl - peak:,.2f} reached the limit {DD_LIMIT:,.0f}", "error")

    async def _close(self, t, reason, st, broker, price, now):
        try:
            r = await broker.close({**t, "bot_dir": t["side"], "stop": t["sl"], "target": t["tp"]}, ("EX-" + t["_id"])[:26] + now.strftime("%M%S"), price)
        except Exception as e:
            await self.event("error", f"{t['wb']}: close order failed ({e}); will retry", "error")
            return
        try:
            await broker.cancel_all()
        except Exception as e:
            await self.event("error", f"cancel-all after close failed: {e}", "error")
        if r.get("reason") in ("STOP", "TARGET"):
            reason = r["reason"].title()
        await self._closed(t, reason, float(r.get("price") or price), r.get("role", "taker"), st, now)

    async def _closed(self, t, reason, px, role, st, now):
        sd = 1 if t["side"] == "LONG" else -1
        gross = sd * (px - t["entry"]) * t["size"]
        fees = t["entry_fee"] + fee(px, t["size"], "maker" if role == "maker" else "taker")
        upd = {"status": "CLOSED", "result": reason, "exit_price": px, "exit_time": iso(now), "exit_t": int(now.timestamp()), "exit_ist": runs._ist(int(now.timestamp())),
               "gross": gross, "fees": fees, "net": gross - fees, "hold_h": round((now.timestamp() - t["entry_t"]) / 3600, 1)}
        res = await db.elite_trades.update_one({"_id": t["_id"], "status": "OPEN"}, {"$set": upd})
        if not res.modified_count:
            return
        if st["mode"] == "TESTNET" and t.get("bracket_confirmed"):
            await set_state(testnet_ok=True)
        t = {**t, **upd}
        await self.event("closed", f"{t['wb']}: {reason} at {px:,.1f}, net {t['net']:+,.2f} ({st['mode']})")
        await self._notify("exit", t)

    async def _reconcile(self, st, broker, now):
        if st["mode"] == "PAPER":
            return
        try:
            pos = await broker.position()
        except Exception as e:
            await self.event("error", f"could not read the {st['mode']} position: {e}", "error")
            return
        t = await db.elite_trades.find_one({"status": "OPEN"})
        if t and not pos:
            ex = await broker.resolve_exit({**t, "bot_dir": t["side"], "stop": t["sl"], "target": t["tp"]}, self.last_price or t["entry"])
            await self._closed(t, ex["reason"].title(), float(ex["price"]), ex.get("role", "taker"), st, now)
        elif pos and not t and not await db.elite_trades.find_one({"status": "PENDING"}):
            await set_state(halt={"reason": f"the exchange shows a BTCUSD position of {pos['size']} contracts that Elite did not open", "at": iso(now), "kind": "unknown position"})
            await self.event("halt", "Unknown position on the exchange: Elite stopped and will not touch it", "error")

    # ---- controls (admin)
    async def stop_all(self, by):
        async with self.lock:
            st, now = await get_state(self.clock()), self.clock()
            broker = self.broker_factory(st["mode"])
            await set_state(halt={"reason": f"Stop all pressed by {by}", "at": iso(now), "kind": "manual"})
            t = await db.elite_trades.find_one({"status": "OPEN"})
            if t:
                if self.last_price is None:
                    raise ValueError("No live price yet – try again in a few seconds")
                await self._close(t, "Stop all", st, broker, self.last_price, now)
            else:
                try:
                    await broker.cancel_all()
                except Exception as e:
                    await self.event("error", f"cancel-all failed: {e}", "error")
            await self.event("control", f"STOP ALL by {by}: open trade closed, orders cancelled, new trades stopped. Press Resume to continue.", "warn")

    async def resume(self, by):
        async with self.lock:
            st = await get_state(self.clock())
            t = await db.elite_trades.find_one({"status": "OPEN"})
            pnl = await self.book_pnl(st) + self.unrealized(t, self.last_price)
            await set_state(halt=None, peak=round(pnl, 2))     # the drawdown is measured again from here
            await self.event("control", f"Resumed by {by}; drawdown is measured from P&L {pnl:,.2f}", "warn")

    async def set_mode(self, mode, confirm, by):
        async with self.lock:
            st, now = await get_state(self.clock()), self.clock()
            if mode not in MODES:
                raise ValueError("Unknown mode")
            if mode == st["mode"]:
                return
            if await db.elite_trades.find_one({"status": {"$in": ["OPEN", "PENDING"]}}):
                raise ValueError("Close the open Elite trade first (Stop all), then change mode")
            if mode != "PAPER":
                if (confirm or "").strip() != mode:
                    raise ValueError(f"Type {mode} to confirm")
                if not env_keys(mode):
                    raise ValueError(f"The {mode} API key is not in the server environment (ELITE_DELTA{'_TESTNET' if mode == 'TESTNET' else ''}_API_KEY / _SECRET)")
                at = await autotrade.get_state()
                if at.get("mode") == mode:                # one bot per Delta account: never both on the same account
                    raise ValueError(f"Auto-Trade is in {mode} on the same Delta account. Put Auto-Trade in Paper first.")
                if mode == "LIVE" and not st.get("testnet_ok"):
                    raise ValueError("Complete one Elite TESTNET trade (entry, stop/target confirmed, exit) before LIVE")
                cfg = await self.cfg()
                pf = await self.broker_factory(mode).preflight(cfg)   # key works from this server, account flat, leverage set
                need = NOTIONAL / LEVERAGE * cfg["usd_inr"] * 1.05
                if pf and pf.get("available_inr") is not None and pf["available_inr"] < need:
                    raise ValueError(f"The {mode} account has ₹{pf['available_inr']:,.0f} available; one Elite trade needs about ₹{need:,.0f} margin ($1,000 at 10x)")
            await set_state(mode=mode, mode_since=iso(now), peak=0.0, halt=None)
            self.reconciled = False
            await self.event("mode", f"Mode changed {st['mode']} → {mode} by {by}", "warn")


ENGINE = Engine()


async def notify(kind, t):
    """Email for a new Elite position and for its close. Follows the site's alert switches (alert mode, kill switch, email channel)."""
    import alert_mode
    import alerts
    from channel_config import CFG
    from core import APP_NAME
    from html import escape
    if not alert_mode.source_on("elite"):
        return
    key = f"elite-{kind}-{t['_id']}-{t['mode']}"
    if await db.alerts_log.find_one({"_id": key}, {"_id": 1}):
        return
    entry = {"_id": key, "at": iso(now_utc()), "signal_id": t["_id"], "channel": "email", "kind": f"elite-{kind}", "test": False}
    mode = await alert_mode.get_mode()
    to = CFG.get("ALERT_EMAIL")
    if not mode["effective_armed"]:
        entry["result"] = "not sent (" + ("kill switch on" if mode["kill_switch"] else "alert mode is OFF") + ")"
    elif not mode["channels"].get("email"):
        entry["result"] = "not sent (email channel switched off)"
    elif not to:
        entry["result"] = "not sent (ALERT_EMAIL not configured)"
    else:
        name = CFG.get("EMAIL_FROM_NAME") or APP_NAME
        app = CFG["FRONTEND_URL"].split(",")[0].rstrip("/")
        f1 = lambda v: f"{v:,.1f}"
        if kind == "entry":
            subject = f"{name}: Elite 4 Edge {t['mode']} — {t['side']} BTCUSD at {f1(t['entry'])} ({t['wb']})"
            rows = [("Strategy", f"{t['wb']} · {t['name']}"), ("Trade", f"{t['side']} {t['size']:.3f} BTC (~$10,000, 10x)"), ("Entry", f1(t["entry"])),
                    ("Stop loss", f1(t["sl"])), ("Take profit", f"{f1(t['tp'])} (1:{t['rr']:g})"), ("Time exit", runs._ist(t["time_exit_t"]) + " IST"),
                    ("Why", t["reason"]), ("Mode", t["mode"])]
        else:
            subject = f"{name}: Elite 4 Edge {t['mode']} closed — {t['wb']} {t['result']} {('+' if t['net'] >= 0 else '−')}${abs(t['net']):,.2f}"
            rows = [("Strategy", f"{t['wb']} · {t['name']}"), ("Result", t["result"]), ("Entry", f"{f1(t['entry'])} at {t['entry_ist']} IST"),
                    ("Exit", f"{f1(t['exit_price'])} at {t['exit_ist']} IST"), ("Net after fees", f"{('+' if t['net'] >= 0 else '−')}${abs(t['net']):,.2f}"), ("Mode", t["mode"])]
        tr = "".join(f"<tr><td style='padding:4px 12px 4px 0;color:#555'>{escape(k)}</td><td><b>{escape(str(v))}</b></td></tr>" for k, v in rows)
        html = (f"<div style='font-family:Arial,sans-serif'><h2>{escape(name)} · Elite 4 Edge</h2><table>{tr}</table>"
                f"<p>Open the page: <a href='{escape(app)}/elite-4-edge'>{escape(app)}/elite-4-edge</a></p>"
                f"<p style='font-size:12px;color:#888'>{'PAPER trade: no real order was placed.' if t['mode'] == 'PAPER' else t['mode'] + ' order on Delta Exchange.'}</p></div>")
        try:
            entry["provider_id"] = await alerts.send_email(to=to, subject=subject, html=html)
            entry["result"] = "sent"; entry["to"] = to
        except Exception as e:
            entry["result"] = f"failed: {str(e)[:160]}"
    try:
        await db.alerts_log.insert_one(entry)
    except Exception:
        pass


ENGINE.notify = notify


# ================================================================ data loops and dashboard
async def refresh():
    import daytype
    now = int(time.time()); now -= now % 300
    have = STATE["bars5"] or list(runs.STATE.get("bars5") or [])   # start from the Candle Runs store when it is already loaded
    floor = runs._ts(REPLAY_IST) - 32 * 86400
    start = have[-1]["t"] - 900 if have and have[0]["t"] <= floor + 86400 else floor
    if start == floor:
        have = []
    new = await daytype._fetch(start, now)
    m = {c["t"]: c for c in have}
    added, real_now = 0, time.time()
    for c in new:
        if c["t"] + 300 + 8 <= real_now:               # a 5-minute candle counts 8 s after it closed (its last trades are in)
            if c["t"] not in m:
                added += 1
            m[c["t"]] = {k: c[k] for k in ("t", "o", "h", "l", "c")}
    STATE["bars5"] = [m[t] for t in sorted(m) if t >= floor]
    return added


async def compute():
    import plan_a
    b5 = STATE["bars5"]
    if not b5:
        return STATE
    live = ENGINE.last_price or plan_a.STATE.get("live_price") or b5[-1]["c"]
    fr = STATE["frames"] if STATE.get("frames_t") == b5[-1]["t"] else frames(b5)
    data = build(b5, live, fr)
    data["computed_at"] = iso(now_utc())
    STATE.update(computed_at=data["computed_at"], error=None, data=data)
    return STATE


async def candle_loop():
    """Delta 5-minute candles: refreshed every 20 s; each new candle close is checked for signals at once."""
    await asyncio.sleep(20)
    last_compute = 0.0
    while True:
        try:
            added = await refresh()
            b5 = STATE["bars5"]
            if b5 and (added or STATE.get("frames_t") != b5[-1]["t"]):
                fr = frames(b5)
                STATE.update(frames=fr, frames_t=b5[-1]["t"])
                last_tc = max(fr[tf][0][-1]["tc"] for tf in fr if fr[tf][0])
                await ENGINE.on_candles(candidates(fr, last_tc - 6 * 3600, last_tc), last_tc)
            if time.monotonic() - last_compute > 120:
                await compute()
                last_compute = time.monotonic()
        except Exception as e:
            STATE["error"] = str(e)[:200]
            log.exception("elite candle loop")
        await asyncio.sleep(20)


async def tick_loop():
    await asyncio.sleep(25)
    fails = 0
    while True:
        try:
            p = ws_price() or await autotrade.rest_price()
            await ENGINE.tick(*p)
            fails = 0
        except Exception as e:
            fails += 1
            log.warning("elite tick failed (%s): %s", fails, e)
        await asyncio.sleep(1 if fails < 5 else min(30, fails))


async def loop():
    await ensure_indexes()
    await asyncio.gather(candle_loop(), tick_loop())


async def live_view():
    st = await get_state()
    price = ENGINE.last_price
    trades = [{k: v for k, v in x.items() if k != "_id"} | {"id": x["_id"]} async for x in db.elite_trades.find().sort("created_at", -1).limit(500)]
    sigs = [{k: v for k, v in x.items() if k != "_id"} | {"id": x["_id"]} async for x in db.elite_signals.find().sort("t", -1).limit(300)]
    events = [{k: v for k, v in x.items() if k != "_id"} async for x in db.elite_events.find().sort("at", -1).limit(100)]
    open_t = next((t for t in trades if t["status"] == "OPEN"), None)
    if open_t:
        open_t["unrealized"] = Engine.unrealized(open_t, price)
        open_t["time_left_h"] = round(max(0, open_t["time_exit_t"] - now_utc().timestamp()) / 3600, 1)
    book_trades = [t for t in trades if t["mode"] == st["mode"] and t.get("fill_time", "") >= st["mode_since"]]
    closed = [t for t in book_trades if t["status"] == "CLOSED"]
    realized = sum(t["net"] for t in closed)
    unreal = Engine.unrealized(open_t, price) if open_t and open_t["mode"] == st["mode"] else 0.0
    pnl = realized + unreal
    paper_closed = sorted([t for t in trades if t["mode"] == "PAPER" and t["status"] == "CLOSED"], key=lambda t: t["exit_t"])
    # paper vs backtest: each live signal against what the backtest did with the same candle
    data = STATE.get("data") or {}
    bt = {(r["strategy"], r["signal_t"]): r for r in (data.get("A") or {}).get("trades", [])}
    bskip = {(s["key"], s["t"]): s["reason"] for s in data.get("backtest_skips", [])}
    live_t0 = parse_iso(st["live_start"]).timestamp()
    cmp_rows = []
    for s_ in sorted(sigs, key=lambda x: x["t"]):
        if s_["t"] < live_t0:
            continue
        lt = next((t for t in trades if t["id"] == s_["id"]), None)
        b = bt.get((s_["key"], s_["t"]))
        cmp_rows.append({"ist": s_["ist"], "wb": s_["wb"], "side": s_["side"], "live_status": s_["status"], "live_reason": s_.get("reason"),
                         "live_entry": lt.get("entry") if lt else None, "live_result": lt.get("result") if lt else None, "live_net": lt.get("net") if lt else None,
                         "bt_status": "TAKEN" if b else "SKIPPED", "bt_reason": None if b else bskip.get((s_["key"], s_["t"]), "not a signal in the backtest"),
                         "bt_entry": b["entry"] if b else None, "bt_result": (b.get("result") or "Open") if b else None, "bt_net": b.get("net") if b and b["status"] == "CLOSED" else None})
    both = [c for c in cmp_rows if c["live_net"] is not None and c["bt_net"] is not None]
    bt_since = [r for r in (data.get("A") or {}).get("trades", []) if r["entry_t"] >= live_t0 and r["status"] == "CLOSED"]
    return {
        "mode": st["mode"], "mode_since": st["mode_since"], "live_start": st["live_start"], "halt": st.get("halt"), "price": price,
        "paper_capital": PAPER_CAPITAL, "paper_balance": PAPER_CAPITAL + sum(t["net"] for t in paper_closed),
        "book": {"realized": round(realized, 2), "unrealized": round(unreal, 2), "pnl": round(pnl, 2), "peak": float(st.get("peak") or 0.0),
                 "drawdown": round(pnl - max(float(st.get("peak") or 0.0), pnl), 2), "limit": DD_LIMIT, "trades": len(closed)},
        "open_trade": open_t, "trades": [t for t in trades if t["status"] != "PENDING"], "signals": sigs, "events": events,
        "paper_stats": _stats([{**t, "status": "CLOSED"} for t in paper_closed]),
        "compare": {"rows": cmp_rows[::-1], "live_net_same_signals": round(sum(c["live_net"] for c in both), 2), "bt_net_same_signals": round(sum(c["bt_net"] for c in both), 2),
                    "matched": len(both), "live_net_all": round(sum(t["net"] for t in paper_closed if t["entry_t"] >= live_t0), 2),
                    "bt_net_all": round(sum(r["net"] for r in bt_since), 2), "bt_trades_all": len(bt_since)},
        "real_money": {"testnet_key": bool(env_keys("TESTNET")), "live_key": bool(env_keys("LIVE")), "testnet_trade_done": bool(st.get("testnet_ok")),
                       "autotrade_mode": (await autotrade.get_state()).get("mode")},
    }


async def dashboard(journals=True):
    d = STATE.get("data") or {}
    if not journals:
        return {"name": NAME, "computed_at": STATE.get("computed_at"), "error": STATE.get("error"), "live": await live_view()}
    return {"name": NAME, "computed_at": STATE.get("computed_at"), "error": STATE.get("error"), "A": d.get("A"), "B": d.get("B"),
            "last_candle_ist": d.get("last_candle_ist"), "live": await live_view(),
            "strategies": [{"key": k, "wb": STRATS[k]["wb"], "name": STRATS[k]["name"], "tf": STRATS[k]["tf"], "rr": STRATS[k]["rr"], "hold_h": STRATS[k]["hold_h"],
                            "priority": i + 1, "rule": RULE[k], "signal_rule": STRATS[k]["rule"]} for i, k in enumerate(ORDER)],
            "settings": {"notional": NOTIONAL, "leverage": LEVERAGE, "margin": NOTIONAL / LEVERAGE, "paper_capital": PAPER_CAPITAL, "dd_limit": DD_LIMIT,
                         "fees": "0.05% taker each side + 18% GST", "one_trade_at_a_time": True, "priority": [STRATS[k]["wb"] for k in ORDER]}}
