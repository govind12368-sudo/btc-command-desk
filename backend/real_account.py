"""Real Account Tested (owner request, 25 Nov 2026): trades from the owner's real Delta Exchange account, entered by an admin.

Admin only. "Rev" = reversed signal: side, stop and target swapped against the original signal, so Reward:Risk is below 1.
Only the facts of each trade are stored (entry time, side, entry, SL, TP, result, exit price). Everything else is worked out:
  start balance  $100 for the first trade, then the end balance of the trade before
  position       10 x the start balance (10x leverage on the whole balance)
  qty            position / entry, in BTC
  gross          qty x (exit - entry), sign by side
  fees           Delta taker 0.05% on the entry and the exit notional, + 18% GST
So a corrected price re-computes every balance after it.
Row 1 (18 Nov 10:30 SHORT) exits at 63,249.7 (owner, 25 Nov 2026): the first table's +$26.00 gross did not match that exit price.
"""
import re
from datetime import datetime

from core import db, iso, now_utc

START_BAL, LEVERAGE, TAKER, GST = 100.0, 10.0, 0.0005, 0.18
RESULTS = ("Target", "Stop", "Time", "Manual")
SEED = [
    {"entry_ist": "2026-11-18 10:30", "side": "SHORT", "entry": 64390.0, "sl": 67511.0, "tp": 63349.7, "result": "Target", "exit": 63249.7},
    {"entry_ist": "2026-11-18 20:30", "side": "LONG", "entry": 63644.0, "sl": 60747.7, "tp": 64609.4, "result": "Stop", "exit": 60747.7},
    {"entry_ist": "2026-11-21 01:30", "side": "LONG", "entry": 58520.5, "sl": 50916.2, "tp": 61055.3, "result": "Target", "exit": 61055.3},
    {"entry_ist": "2026-11-22 17:30", "side": "LONG", "entry": 61036.0, "sl": 55135.1, "tp": 63986.5, "result": "Target", "exit": 63986.5},
]
FIELDS = ("entry_ist", "side", "entry", "sl", "tp", "result", "exit")


def tid(t):
    return f"{t['entry_ist']} {t['side']}"


def clean(t):
    """Validated trade facts, or ValueError with a plain reason."""
    try:
        out = {"entry_ist": str(t["entry_ist"]).strip(), "side": str(t["side"]).strip().upper(), "result": str(t["result"]).strip().title()}
        for k in ("entry", "sl", "tp", "exit"):
            out[k] = round(float(t[k]), 1)
    except (KeyError, TypeError, ValueError):
        raise ValueError("Fill in entry time, side, entry, SL, TP, result and exit price")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", out["entry_ist"]):
        raise ValueError("Entry time must look like 2026-11-18 10:30 (IST)")
    try:
        datetime.strptime(out["entry_ist"], "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError("Entry time is not a real date and time")
    if out["side"] not in ("LONG", "SHORT"):
        raise ValueError("Side must be LONG or SHORT")
    if out["result"] not in RESULTS:
        raise ValueError(f"Result must be one of: {', '.join(RESULTS)}")
    if min(out["entry"], out["sl"], out["tp"], out["exit"]) <= 0:
        raise ValueError("Prices must be above 0")
    if out["side"] == "LONG" and not out["sl"] < out["entry"] < out["tp"]:
        raise ValueError("For a LONG, SL must be below the entry and TP above it")
    if out["side"] == "SHORT" and not out["tp"] < out["entry"] < out["sl"]:
        raise ValueError("For a SHORT, TP must be below the entry and SL above it")
    return out


def compute(trades):
    """Rows with balances, size, P&L and fees, oldest first, plus the totals."""
    rows, bal = [], START_BAL
    for t in sorted(trades, key=lambda x: x["entry_ist"]):
        sign = 1 if t["side"] == "LONG" else -1
        pos = bal * LEVERAGE
        qty = pos / t["entry"]
        gross = qty * (t["exit"] - t["entry"]) * sign
        fees = (pos + qty * t["exit"]) * TAKER * (1 + GST)
        net = gross - fees
        risk, reward = abs(t["entry"] - t["sl"]), abs(t["tp"] - t["entry"])
        rows.append({"id": tid(t), **{k: t[k] for k in FIELDS}, "rr": reward / risk if risk else None, "start_bal": bal, "position": pos,
                     "qty": qty, "gross": gross, "fees": fees, "net": net, "end_bal": bal + net})
        bal += net
    wins = sum(1 for r in rows if r["net"] > 0)
    tot = {"trades": len(rows), "wins": wins, "losses": len(rows) - wins, "win_pct": 100 * wins / len(rows) if rows else None,
           "gross": sum(r["gross"] for r in rows), "fees": sum(r["fees"] for r in rows), "net": sum(r["net"] for r in rows),
           "start_bal": START_BAL, "end_bal": bal, "return_pct": 100 * (bal - START_BAL) / START_BAL,
           "first": rows[0]["entry_ist"] if rows else None, "last": rows[-1]["entry_ist"] if rows else None}
    return rows, tot


async def _seed():
    """The owner's first four trades, once. Deleting them later does not bring them back."""
    if await db.site_ui.find_one({"_id": "real_account_seed"}):
        return
    for t in SEED:
        c = clean(t)
        await db.real_trades.update_one({"_id": tid(c)}, {"$setOnInsert": {**c, "added_by": "owner (chat, 25 Nov 2026)", "added_at": iso(now_utc())}}, upsert=True)
    await db.site_ui.update_one({"_id": "real_account_seed"}, {"$set": {"at": iso(now_utc())}}, upsert=True)


async def dashboard():
    await _seed()
    trades = [t async for t in db.real_trades.find({})]
    rows, tot = compute(trades)
    return {"rows": rows, "totals": tot, "start_bal": START_BAL, "leverage": LEVERAGE, "fee_pct": TAKER * 100, "gst_pct": GST * 100,
            "results": list(RESULTS), "computed_at": iso(now_utc())}


async def add(t, by):
    c = clean(t)
    if await db.real_trades.find_one({"_id": tid(c)}):
        raise ValueError(f"A {c['side']} trade at {c['entry_ist']} is already in the list")
    await db.real_trades.insert_one({"_id": tid(c), **c, "added_by": by, "added_at": iso(now_utc())})


async def remove(trade_id):
    r = await db.real_trades.delete_one({"_id": trade_id})
    if not r.deleted_count:
        raise ValueError("Trade not found")
