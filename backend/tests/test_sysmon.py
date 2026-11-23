"""Server page: one sample reads the whole server's CPU, memory, disk and load, the backend's own use, and stores a slim
history row; MongoDB statistics that are not available never break the page."""
import asyncio
import importlib
import os
import sys

import pytest

pytest.importorskip("mongomock_motor")
import motor.motor_asyncio as mm  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_db")


@pytest.fixture()
def sm(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "sysmon"):
        sys.modules.pop(m, None)
    return importlib.import_module("sysmon")


@pytest.mark.skipif(not os.path.exists("/proc/meminfo"), reason="Linux only")
def test_sample_and_report(sm):
    async def go():
        s = await sm.sample(0.2)
        assert s["cpus"] >= 1 and 0 <= s["host_cpu"] <= 100 and s["mem_total"] > s["mem_used"] > 0
        assert s["disk_total"] >= s["disk_used"] > 0 and len(s["load"]) == 3
        assert s["backend"]["rss"] > 0 and s["backend"]["loop_delay_ms"] >= 0
        row = sm._slim(s)
        assert set(row) >= {"host_cpu", "mem_used", "disk_used", "be_cpu", "be_mem", "mg_mem", "lag"}
        from core import db, now_utc
        await db.server_stats.insert_one({**row, "at_dt": now_utc()})
        r = await sm.report()
        assert r["summary"]["24h"]["mem_used"]["n"] == 1 and len(r["history"]) == 1 and "error" in r["now"]["mongo"]
        assert r["disk_growth_per_day"] is None
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(go())


def test_growth_needs_a_day(sm):
    h = [{"at": "2026-11-20T00:00:00Z", "disk_used": 10_000, "mg_disk": 100}, {"at": "2026-11-22T00:00:00Z", "disk_used": 30_000, "mg_disk": 300}]
    assert sm._growth(h) == {"disk": 10_000, "database": 100, "days": 2.0}
    assert sm._growth([h[0], {**h[1], "at": "2026-11-20T06:00:00Z"}]) is None
