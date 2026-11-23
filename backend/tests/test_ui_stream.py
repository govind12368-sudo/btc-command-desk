"""Menu hide/show, Data Health grading, the ETag/304 middleware and the live-push socket's sign-in check (23 Nov 2026)."""
import asyncio
import importlib
import os
import sys

import pytest

pytest.importorskip("mongomock_motor")
import motor.motor_asyncio as mm  # noqa: E402
from bson import ObjectId  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_db")


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def prefs(monkeypatch):
    monkeypatch.setattr(mm, "AsyncIOMotorClient", lambda *a, **k: AsyncMongoMockClient())
    for m in ("core", "ui_prefs"):
        sys.modules.pop(m, None)
    return importlib.import_module("ui_prefs")


def test_clean_drops_unknown_locked_and_duplicate_pages(prefs):
    assert prefs.clean(["/candle-runs", "/", "/nope", "/candle-runs", 5, None, "/news"]) == ["/candle-runs", "/news"]
    assert prefs.clean(None) == []
    assert "/" in prefs.LOCKED and "/" in prefs.PAGES


def test_user_and_global_lists_are_separate_and_reversible(prefs):
    async def go():
        from core import db
        uid = ObjectId()
        await db.users.insert_one({"_id": uid, "email": "a@example.test", "role": "admin"})
        user = {"id": str(uid), "email": "a@example.test", "role": "admin"}
        c = await prefs.config(user)
        assert c["hidden_user"] == [] and c["hidden_global"] == [] and c["locked"] == ["/"]
        c = await prefs.set_user(user, ["/news", "/"])
        assert c["hidden_user"] == ["/news"] and c["hidden_global"] == []
        c = await prefs.set_global(user, ["/delta-top3", "/bogus"])
        assert c["hidden_global"] == ["/delta-top3"] and c["global_by"] == "a@example.test" and c["hidden_user"] == ["/news"]
        c = await prefs.set_user(user, [])
        c = await prefs.set_global(user, [])
        assert c["hidden_user"] == [] and c["hidden_global"] == []
    run(go())


def test_health_grades():
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    dh = importlib.import_module("datahealth")
    assert dh._grade(5, 2) == "OK"
    assert dh._grade(100, 2) == "LATE"          # beyond 3 cycles (min 30 s) but within 10 (min 300 s)
    assert dh._grade(1000, 2) == "STALE"
    assert dh._grade(None, 60) == "WAITING"
    assert dh._grade(None, 60, err="boom") == "ERROR"
    assert dh._grade(400, 120) == "LATE" and dh._grade(100, 120) == "OK"
    assert dh._age(None) is None


def _server():
    from test_access_control import load_server
    return load_server()


def test_etag_middleware_answers_304_for_unchanged_json():
    server = _server()
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse
    from fastapi.testclient import TestClient
    app = FastAPI()

    @app.get("/api/thing")
    def thing():
        return {"a": 1, "b": [1, 2, 3]}

    @app.get("/api/x.csv")
    def csv():
        return PlainTextResponse("a,b\n1,2\n", media_type="text/csv")

    @app.post("/api/thing")
    def post():
        return {"ok": True}

    app.add_middleware(server.ETagMiddleware)
    c = TestClient(app)
    r = c.get("/api/thing")
    assert r.status_code == 200 and r.json() == {"a": 1, "b": [1, 2, 3]}
    tag = r.headers["etag"]
    assert tag.startswith('W/"') and r.headers["cache-control"] == "no-store" and int(r.headers["content-length"]) == len(r.content)
    r2 = c.get("/api/thing", headers={"If-None-Match": tag})
    assert r2.status_code == 304 and r2.content == b"" and r2.headers["etag"] == tag
    assert c.get("/api/thing", headers={"If-None-Match": 'W/"other"'}).status_code == 200
    assert "etag" not in c.get("/api/x.csv").headers            # downloads are left alone
    assert "etag" not in c.post("/api/thing").headers           # only GET


def test_new_admin_endpoints_reject_viewers_and_socket_needs_sign_in():
    server = _server()
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from auth_core import current_user
    server.app.dependency_overrides[current_user] = lambda: {"id": "u1", "email": "viewer@example.com", "role": "user"}
    try:
        c = TestClient(server.app, raise_server_exceptions=False)
        assert c.get("/api/data-health").status_code == 403
        assert c.put("/api/ui/hidden-global", json={"pages": ["/news"]}).status_code == 403
    finally:
        server.app.dependency_overrides.clear()
    c = TestClient(server.app, raise_server_exceptions=False)
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/api/ws") as ws:
            ws.send_text('{"token": "not-a-token"}')
            ws.receive_text()
    assert e.value.code == 4401
