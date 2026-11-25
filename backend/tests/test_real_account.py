"""Real Account Tested (owner request, 25 Nov 2026): trade facts in, balances and P&L worked out, admin only."""
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
run = lambda c: asyncio.new_event_loop().run_until_complete(c)  # noqa: E731


@pytest.fixture()
def ra(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "real_account"):
        sys.modules.pop(m, None)
    return importlib.import_module("real_account")


def test_owner_rows_2_to_4_match_the_owner_table_from_the_same_balances(ra):
    """The formula reproduces the owner's own numbers when started from the owner's balances."""
    for t, bal, qty, gross, fees, net in ((ra.SEED[1], 124.83, 0.0196, -56.81, 1.44, -58.25), (ra.SEED[2], 66.58, 0.0114, 28.84, 0.80, 28.04),
                                          (ra.SEED[3], 94.62, 0.0155, 45.74, 1.14, 44.60)):
        ra.START_BAL = bal
        r = ra.compute([ra.clean(t)])[0][0]
        assert (round(r["qty"], 4), round(r["gross"], 2), round(r["fees"], 2), round(r["net"], 2)) == (qty, gross, fees, net)


def test_seed_with_row_1_corrected_to_its_exit_price(ra):
    rows, tot = ra.compute([ra.clean(t) for t in ra.SEED])
    r1 = rows[0]
    assert (round(r1["gross"], 2), round(r1["fees"], 2), round(r1["net"], 2), round(r1["end_bal"], 2)) == (17.71, 1.17, 16.54, 116.54)
    assert [round(r["start_bal"], 2) for r in rows] == [100.0, 116.54, 62.16, 88.34]
    assert [round(r["rr"], 2) for r in rows] == [0.33, 0.33, 0.33, 0.5]
    assert (tot["trades"], tot["wins"], tot["win_pct"], round(tot["net"], 2), round(tot["end_bal"], 2)) == (4, 3, 75.0, 29.97, 129.97)
    assert abs(tot["gross"] - tot["fees"] - tot["net"]) < 1e-9


def test_trade_checks(ra):
    ok = dict(ra.SEED[0])
    assert ra.clean(ok)["side"] == "SHORT"
    for bad, word in (({"side": "UP"}, "Side"), ({"entry_ist": "18/11/2026"}, "Entry time"), ({"entry_ist": "2026-13-40 10:30"}, "real date"),
                      ({"result": "Maybe"}, "Result"), ({"sl": 60000}, "SHORT"), ({"exit": 0}, "above 0")):
        with pytest.raises(ValueError, match=word):
            ra.clean({**ok, **bad})
    with pytest.raises(ValueError, match="LONG"):
        ra.clean({**ra.SEED[1], "tp": 60000})


def test_seed_once_add_and_remove(ra):
    async def go():
        d = await ra.dashboard()
        assert d["totals"]["trades"] == 4 and d["rows"][0]["id"] == "2026-11-18 10:30 SHORT"
        for r in d["rows"]:
            await ra.remove(r["id"])
        assert (await ra.dashboard())["totals"]["trades"] == 0          # deleted trades are not seeded again
        await ra.add({**ra.SEED[0]}, "admin@example.test")
        with pytest.raises(ValueError, match="already"):
            await ra.add({**ra.SEED[0]}, "admin@example.test")
        with pytest.raises(ValueError, match="not found"):
            await ra.remove("nope")
        assert (await ra.dashboard())["totals"]["trades"] == 1
    run(go())


def test_server_real_account_is_admin_only(monkeypatch):
    from test_access_control import load_server
    server = load_server()
    from fastapi.testclient import TestClient
    from auth_core import current_user
    role = {"r": "user"}
    server.app.dependency_overrides[current_user] = lambda: {"id": "u1", "email": "x@example.com", "role": role["r"]}

    async def dash():
        return {"rows": [], "totals": {"trades": 0}}
    monkeypatch.setattr(server.real_account, "dashboard", dash)
    try:
        c = TestClient(server.app, raise_server_exceptions=False)
        assert c.get("/api/real-account").status_code == 403
        assert c.post("/api/real-account/trades", json={**server.real_account.SEED[0]}).status_code == 403
        assert c.delete("/api/real-account/trades/x").status_code == 403
        role["r"] = "admin"
        assert c.get("/api/real-account").status_code == 200
    finally:
        server.app.dependency_overrides.clear()
