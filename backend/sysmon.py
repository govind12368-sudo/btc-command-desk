"""Server capacity and how the site uses it (admin-only Server page).

Read from inside the backend container: Linux /proc shows the whole server (CPU, memory, swap, load, uptime) and the root
disk; the container's cgroup files show the backend's own CPU and memory; MongoDB reports its own memory, CPU time and
storage. One sample every 5 minutes is kept for 8 days (db.server_stats, TTL index)."""
import asyncio
import logging
import os
import platform
import shutil
import time
from datetime import timedelta

from core import db, iso, now_utc

log = logging.getLogger("sysmon")
EVERY_SEC = 300
KEEP_DAYS = 8
MB = 1024 * 1024


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return None


def meminfo():
    out = {}
    for line in (_read("/proc/meminfo") or "").splitlines():
        k, _, v = line.partition(":")
        try:
            out[k.strip()] = int(v.split()[0]) * 1024
        except Exception:
            pass
    return out


def cpu_times():
    """(idle incl. iowait, steal, total) jiffies for the whole server."""
    line = (_read("/proc/stat") or "").splitlines()[:1]
    if not line:
        return None
    v = [int(x) for x in line[0].split()[1:]]
    v += [0] * (8 - len(v))
    return v[3] + v[4], v[7], sum(v[:8])


def cg_cpu_usec():
    st = _read("/sys/fs/cgroup/cpu.stat")                       # cgroup v2
    if st:
        for line in st.splitlines():
            if line.startswith("usage_usec"):
                return int(line.split()[1])
    v1 = _read("/sys/fs/cgroup/cpuacct/cpuacct.usage")          # cgroup v1 (nanoseconds)
    return int(v1) // 1000 if v1 else None


def cg_memory():
    cur = _read("/sys/fs/cgroup/memory.current") or _read("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    mx = _read("/sys/fs/cgroup/memory.max") or _read("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    stat = {}
    for line in (_read("/sys/fs/cgroup/memory.stat") or _read("/sys/fs/cgroup/memory/memory.stat") or "").splitlines():
        k, _, v = line.partition(" ")
        if v.strip().isdigit():
            stat[k] = int(v)
    limit = int(mx) if mx and mx.strip().isdigit() and int(mx) < 1 << 60 else None
    return {"current": int(cur) if cur and cur.strip().isdigit() else None, "limit": limit,
            "anon": stat.get("anon", stat.get("rss")), "file": stat.get("file", stat.get("cache"))}


def proc_self():
    out = {}
    for line in (_read("/proc/self/status") or "").splitlines():
        if line.startswith("VmRSS:"):
            out["rss"] = int(line.split()[1]) * 1024
        elif line.startswith("Threads:"):
            out["threads"] = int(line.split()[1])
    try:
        out["open_files"] = len(os.listdir("/proc/self/fd"))
    except Exception:
        pass
    return out


def os_name():
    txt = _read("/etc/os-release") or ""
    host_kernel = platform.release()
    pretty = next((l.split("=", 1)[1].strip('"') for l in txt.splitlines() if l.startswith("PRETTY_NAME=")), "")
    return {"kernel": host_kernel, "container_os": pretty, "arch": platform.machine()}


async def mongo_stats():
    out = {}
    try:
        ss = await asyncio.wait_for(db.client.admin.command("serverStatus"), 5)
        ex = ss.get("extra_info", {}) or {}
        wt = (ss.get("wiredTiger") or {}).get("cache", {}) or {}
        out.update(resident=(ss.get("mem") or {}).get("resident", 0) * MB, connections=(ss.get("connections") or {}).get("current"),
                   cpu_usec=(ex.get("user_time_us") or 0) + (ex.get("system_time_us") or 0) or None, version=ss.get("version"),
                   cache_used=wt.get("bytes currently in the cache"), cache_max=wt.get("maximum bytes configured"),
                   uptime_sec=ss.get("uptime"), ops=dict(ss.get("opcounters") or {}))
    except Exception as e:
        out["error"] = str(e)[:160]
    try:
        ds = await asyncio.wait_for(db.command("dbStats"), 5)
        out.update(data_size=ds.get("dataSize"), storage_size=ds.get("storageSize"), index_size=ds.get("indexSize"),
                   objects=ds.get("objects"), collections=ds.get("collections"))
    except Exception as e:
        out.setdefault("error", str(e)[:160])
    return out


async def collections(top=15):
    rows = []
    try:
        names = await db.list_collection_names()
    except Exception:
        return rows
    for name in names:
        try:
            cs = await db[name].aggregate([{"$collStats": {"storageStats": {}}}]).to_list(1)
            s = cs[0]["storageStats"] if cs else {}
            rows.append({"name": name, "count": s.get("count"), "data": s.get("size"), "disk": (s.get("storageSize") or 0) + (s.get("totalIndexSize") or 0)})
        except Exception:
            rows.append({"name": name, "count": None, "data": None, "disk": None})
    rows.sort(key=lambda r: r["disk"] or 0, reverse=True)
    return rows[:top]


async def sample(window=1.0):
    """One measurement: CPU over `window` seconds (server, backend, MongoDB), memory, disk, load, event-loop delay."""
    c1, g1, m1 = cpu_times(), cg_cpu_usec(), await mongo_stats()
    t1 = time.perf_counter()
    await asyncio.sleep(window)
    t2 = time.perf_counter()
    c2, g2, m2 = cpu_times(), cg_cpu_usec(), await mongo_stats()
    dt, ncpu = t2 - t1, os.cpu_count() or 1
    host_cpu = steal = None
    if c1 and c2 and c2[2] > c1[2]:
        tot = c2[2] - c1[2]
        host_cpu = round(100 * (1 - (c2[0] - c1[0]) / tot), 1)
        steal = round(100 * (c2[1] - c1[1]) / tot, 1)
    be_cpu = round((g2 - g1) / 1e6 / dt * 100 / ncpu, 2) if g1 is not None and g2 is not None else None
    mg_cpu = round((m2["cpu_usec"] - m1["cpu_usec"]) / 1e6 / dt * 100 / ncpu, 2) if m1.get("cpu_usec") and m2.get("cpu_usec") else None
    mi = meminfo()
    du = shutil.disk_usage("/")
    cg = cg_memory()
    me = proc_self()
    total, avail = mi.get("MemTotal"), mi.get("MemAvailable")
    return {
        "at": iso(now_utc()), "cpus": ncpu, "host_cpu": host_cpu, "steal": steal, "load": [round(x, 2) for x in os.getloadavg()],
        "mem_total": total, "mem_available": avail, "mem_used": (total - avail) if total and avail else None,
        "swap_total": mi.get("SwapTotal"), "swap_used": (mi.get("SwapTotal", 0) - mi.get("SwapFree", 0)) if "SwapTotal" in mi else None,
        "disk_total": du.used + du.free, "disk_used": du.used, "disk_free": du.free,      # like df: reserved blocks left out
        "uptime_sec": int(float((_read("/proc/uptime") or "0").split()[0])),
        "backend": {"cpu": be_cpu, "rss": me.get("rss"), "cgroup_mem": cg["current"], "anon": cg["anon"], "page_cache": cg["file"], "limit": cg["limit"],
                    "threads": me.get("threads"), "open_files": me.get("open_files"), "tasks": len(asyncio.all_tasks()),
                    "loop_delay_ms": round(max(0.0, (dt - window) * 1000), 1)},
        "mongo": {**{k: m2.get(k) for k in ("resident", "connections", "version", "cache_used", "cache_max", "uptime_sec", "data_size", "storage_size",
                                            "index_size", "objects", "collections", "error")}, "cpu": mg_cpu},
    }


def _slim(s):
    return {"at": s["at"], "host_cpu": s["host_cpu"], "steal": s["steal"], "load1": s["load"][0], "mem_used": s["mem_used"], "mem_total": s["mem_total"],
            "swap_used": s["swap_used"], "disk_used": s["disk_used"], "disk_total": s["disk_total"], "be_cpu": s["backend"]["cpu"],
            "be_mem": s["backend"]["rss"] or s["backend"]["anon"], "mg_cpu": s["mongo"]["cpu"], "mg_mem": s["mongo"]["resident"],
            "mg_disk": (s["mongo"]["storage_size"] or 0) + (s["mongo"]["index_size"] or 0), "lag": s["backend"]["loop_delay_ms"]}


async def loop():
    """Every 5 minutes: a 5-second CPU sample is stored (kept 8 days)."""
    try:
        await db.server_stats.create_index("at_dt", expireAfterSeconds=KEEP_DAYS * 86400)
    except Exception as e:
        log.warning("server_stats index: %s", e)
    await asyncio.sleep(60)
    while True:
        try:
            s = await sample(5.0)
            await db.server_stats.insert_one({**_slim(s), "at_dt": now_utc()})
        except Exception as e:
            log.warning("server sample failed: %s", e)
        await asyncio.sleep(EVERY_SEC)


def _peak(rows, key):
    v = [r[key] for r in rows if r.get(key) is not None]
    return {"avg": round(sum(v) / len(v), 2) if v else None, "max": max(v) if v else None, "n": len(v)}


async def report():
    now = await sample(1.0)
    since = now_utc() - timedelta(days=7)
    hist = [{k: v for k, v in r.items() if k not in ("_id", "at_dt")} async for r in db.server_stats.find({"at_dt": {"$gte": since}}).sort("at_dt", 1)]
    day = [r for r in hist if r["at"] >= iso(now_utc() - timedelta(days=1))]
    keys = ("host_cpu", "steal", "load1", "mem_used", "swap_used", "be_cpu", "be_mem", "mg_cpu", "mg_mem", "lag")
    return {"now": now, "os": os_name(), "collections": await collections(), "history": hist,
            "summary": {"24h": {k: _peak(day, k) for k in keys}, "7d": {k: _peak(hist, k) for k in keys}},
            "disk_growth_per_day": _growth(hist)}


def _growth(hist):
    """Bytes a day the disk and the database grew over the stored history (None until a day of samples exists)."""
    if len(hist) < 2:
        return None
    a, b = hist[0], hist[-1]
    from core import parse_iso
    days = (parse_iso(b["at"]) - parse_iso(a["at"])).total_seconds() / 86400
    if days < 0.9:
        return None
    return {"disk": round((b["disk_used"] - a["disk_used"]) / days), "database": round((b["mg_disk"] - a["mg_disk"]) / days), "days": round(days, 1)}
