"""Checks the automatic Plan A engine against the hand-built workbook (tests/fixtures_plan_a.json),
using real Delta 1-minute candles. Run where Delta is reachable:  python verify_plan_a.py
Exits non-zero if fewer than 90% of trades match."""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("MONGO_URL", "mongodb://unused")
os.environ.setdefault("DB_NAME", "verify")
import motor.motor_asyncio as mm  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402
mm.AsyncIOMotorClient = lambda *a, **k: AsyncMongoMockClient()

import plan_a  # noqa: E402
from core import db  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))


def ist_to_utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=IST).astimezone(timezone.utc)


async def main():
    fx = json.load(open(os.path.join(os.path.dirname(__file__), "tests", "fixtures_plan_a.json")))
    for s in fx["signals"]:
        if not s["signal_id"].startswith("SCALP"):
            continue
        await db.signals.insert_one({"_id": s["signal_id"], "strategy": "scalp", "direction": s["direction"], "entry": s["entry"],
                                     "stop": s["stop"], "target": s["target"], "qty_btc": s["qty"],
                                     "captured_at": ist_to_utc(s["captured_ist"]).isoformat().replace("+00:00", "Z")})
    st = await plan_a.compute()
    got = {r["signal_id"]: r for r in st["rows"]}
    ok = bad = 0
    for e in fx["plan_a"]:
        g = got.get(e["signal_id"], {})
        exp_fill = e["fill"][:16].replace("T", " ")
        exp_exit = (e["exit"] or "")[:16].replace("T", " ")
        same = g.get("fill_ist") == exp_fill and g.get("plan_result") == e["result"] and (e["result"] == "OPEN" or g.get("exit_ist") == exp_exit)
        if e["result"] == "TIME" and same:
            same = abs((g.get("time_exit_close") or 0) - (e["time_close"] or 0)) < 0.01
        ok += same
        bad += not same
        print(("OK  " if same else "DIFF"), e["signal_id"], "| workbook:", exp_fill, e["result"], exp_exit, e.get("time_close"),
              "| engine:", g.get("fill_ist"), g.get("plan_result"), g.get("exit_ist"), g.get("time_exit_close"), g.get("fill_status"))
    print(f"\n{ok}/{ok + bad} Plan A trades match the workbook; summary: {st['summary']}")
    sys.exit(0 if ok >= 0.9 * (ok + bad) else 1)


asyncio.run(main())
