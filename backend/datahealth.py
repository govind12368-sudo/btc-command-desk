"""Data Health: every feed and every calculation loop in one place (admin page).

Feeds: the source adapters (Delta REST, Binance, OKX, news, calendar, macro …) and the Delta websocket — status, age of the last good
update against the expected interval, failures, last error. Engines: each background loop's last run against its cycle.
Plus the database (ping), the live push stream and a quick server load reading.
"""
import asyncio
import time

from core import db, now_utc, parse_iso

ENGINES = [  # module, label, cycle in seconds, key holding the last run
    ("engine", "Signal engine (scalp / swing / pillars)", 2, "computed_at"),
    ("plan_a", "Plan A – Live", 120, "computed_at"),
    ("regime", "Regime journal", 120, "computed_at"),
    ("flush", "Flush Short", 120, "computed_at"),
    ("runs", "Candle Runs", 120, "computed_at"),
    ("trio", "Delta Top 3", 120, "computed_at"),
    ("elite", "Elite 4 Edge", 120, "computed_at"),
    ("optpicks", "Option Picks", 900, "computed_at"),
    ("observer", "Strategy Observer", 600, "computed_at"),   # recomputes every 10th minute
    ("daytype", "Day-type switch", 60, "updated"),
]


def _age(ts):
    try:
        return max(0.0, (now_utc() - parse_iso(ts)).total_seconds()) if ts else None
    except Exception:
        return None


def _grade(age, every, status_ok=True, err=None):
    if not status_ok or (age is None):
        return "ERROR" if err else "WAITING"
    if age <= max(every * 3, 30):
        return "OK"
    if age <= max(every * 10, 300):
        return "LATE"
    return "STALE"


def feeds(is_admin):
    from sources import DELTA_WS, REGISTRY, WS
    out = []
    for s in REGISTRY.values():
        p = s.public()
        age = _age(p.get("last_success"))
        st = p["status"]
        grade = {"ok": "OK", "partial": "PARTIAL", "checking": "WAITING", "rate_limited": "LATE", "access_needed": "ACCESS NEEDED"}.get(st, "ERROR")
        if st == "ok" and age is not None and p.get("freshness_sec") and age > p["freshness_sec"]:
            grade = "STALE"
        if getattr(s, "page_only", False) and st == "checking":
            grade = "ON DEMAND"
        out.append({"key": p["key"], "family": p["family"], "name": p["name"], "status": grade, "age_sec": age, "every_sec": p.get("interval_sec"),
                    "fresh_sec": p.get("freshness_sec"), "fails": p.get("fail_count"), "error": p.get("error") if is_admin else (p.get("error") and "error"),
                    "next_retry": p.get("next_retry")})
    age = _age(WS.get("last_msg"))
    out.insert(0, {"key": "delta_ws", "family": "delta", "name": "Delta websocket (ticker, order book, trades)", "status": "OK" if WS.get("status") == "connected" and (age or 0) < 30 else "ERROR",
                   "age_sec": age, "every_sec": 0, "fresh_sec": 30, "fails": WS.get("reconnects", 0), "error": WS.get("error") if is_admin else None,
                   "next_retry": None, "url": DELTA_WS if is_admin else None})
    return out


def engines():
    out = []
    for mod, label, every, key in ENGINES:
        try:
            st = __import__(mod).STATE
        except Exception:
            continue
        ts = st.get(key)
        err = st.get("error") or st.get("compute_error")
        age = _age(ts)
        out.append({"key": mod, "name": label, "every_sec": every, "last_run": ts, "age_sec": age, "error": err, "status": _grade(age, every, err is None, err)})
    return out


async def database():
    t0 = time.perf_counter()
    try:
        await asyncio.wait_for(db.command("ping"), 5)
    except Exception as e:
        return {"status": "ERROR", "error": str(e)[:160]}
    ms = round((time.perf_counter() - t0) * 1000, 1)
    out = {"status": "OK" if ms < 200 else "LATE", "ping_ms": ms}
    try:
        ds = await asyncio.wait_for(db.command("dbStats"), 5)
        out.update(collections=ds.get("collections"), objects=ds.get("objects"), size_mb=round(((ds.get("storageSize") or 0) + (ds.get("indexSize") or 0)) / 1048576, 1))
    except Exception:
        pass
    return out


def server():
    import os
    import shutil

    import sysmon
    mi = sysmon.meminfo()
    du = shutil.disk_usage("/")
    total, avail = mi.get("MemTotal"), mi.get("MemAvailable")
    return {"cpus": os.cpu_count(), "load": [round(x, 2) for x in os.getloadavg()], "mem_pct": round((total - avail) / total * 100, 1) if total and avail else None,
            "swap_mb": round((mi.get("SwapTotal", 0) - mi.get("SwapFree", 0)) / 1048576) if "SwapTotal" in mi else None,
            "disk_pct": round(du.used / (du.used + du.free) * 100, 1)}


async def report(is_admin=True):
    import stream
    f, e = feeds(is_admin), engines()
    bad = [x["name"] for x in f + e if x["status"] in ("ERROR", "STALE")]
    late = [x["name"] for x in f + e if x["status"] in ("LATE", "ACCESS NEEDED")]
    return {"at": now_utc().isoformat(), "feeds": f, "engines": e, "database": await database(), "stream": stream.public_stats(), "server": server(),
            "summary": {"feeds": len(f), "engines": len(e), "ok": sum(1 for x in f + e if x["status"] in ("OK", "PARTIAL", "ON DEMAND")), "problems": bad, "late": late}}
