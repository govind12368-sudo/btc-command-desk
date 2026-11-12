"""Admin-only endpoints and viewer state filtering. Offline: the real FastAPI app is imported with a stub
Mongo client; viewers are rejected by the admin_user dependency before any database access."""
import os
import sys
import types
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ENV = {"MONGO_URL": "mongodb://offline.invalid:27017", "DB_NAME": "offline_access", "FRONTEND_URL": "https://desk.example",
       "JWT_SECRET": "t" * 48, "ADMIN_EMAIL": "admin@example.com", "ADMIN_PASSWORD": "Str0ng!Passw0rd#"}


def load_server():
    for k, v in ENV.items():
        os.environ.setdefault(k, v)

    class OfflineClient:
        def __init__(self, *a, **k):
            pass

        def __getitem__(self, name):
            return types.SimpleNamespace()

    motor = types.ModuleType("motor")
    motor.__path__ = []
    ma = types.ModuleType("motor.motor_asyncio")
    ma.AsyncIOMotorClient = OfflineClient
    sys.modules.setdefault("motor", motor)
    sys.modules.setdefault("motor.motor_asyncio", ma)
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import server  # noqa: E402
    return server


ADMIN_ONLY = [
    ("GET", "/api/settings"), ("GET", "/api/audit"), ("GET", "/api/upgrades"), ("GET", "/api/upgrades/abc/prepared"),
    ("GET", "/api/alerts"), ("GET", "/api/summary"), ("GET", "/api/delta-sync"), ("GET", "/api/fills"),
    ("GET", "/api/export/fills"), ("GET", "/api/export/signals"), ("GET", "/api/export/validation"),
    ("GET", "/api/reference/sheets"), ("GET", "/api/reference/sheet?name=x"), ("PUT", "/api/auth/profile"),
    ("GET", "/api/security/events"), ("GET", "/api/source-download"), ("GET", "/api/admin/users"),
    ("GET", "/api/auth/2fa"), ("POST", "/api/auth/2fa/setup"),
]


class AccessControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = load_server()
        from fastapi.testclient import TestClient
        from auth_core import current_user
        cls.server.app.dependency_overrides[current_user] = lambda: {"id": "u1", "email": "viewer@example.com", "role": "user"}
        cls.client = TestClient(cls.server.app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        cls.server.app.dependency_overrides.clear()

    def test_viewer_gets_403_on_every_admin_endpoint(self):
        for method, path in ADMIN_ONLY:
            with self.subTest(path=path):
                body = {"name": "New Name"} if method == "PUT" else None
                r = self.client.request(method, path, json=body)
                self.assertEqual(r.status_code, 403, f"{method} {path} -> {r.status_code} {r.text[:120]}")

    def test_admin_dependency_is_declared_on_routes(self):
        from auth_core import admin_user
        want = {p.split("?")[0].replace("/abc/", "/{uid}/") for _, p in ADMIN_ONLY}
        seen = set()
        for r in self.server.app.routes:
            path = getattr(r, "path", "")
            if path in want or path == "/api/export/{kind}":
                calls = {d.call for d in r.dependant.dependencies}
                if admin_user in calls:
                    seen.add(path)
        missing = {p for p in want if not p.startswith("/api/export/")} - seen
        self.assertFalse(missing, f"routes without admin_user: {missing}")
        self.assertIn("/api/export/{kind}", seen)

    def test_signed_out_requests_are_401(self):
        from auth_core import current_user
        overrides = dict(self.server.app.dependency_overrides)
        self.server.app.dependency_overrides.pop(current_user, None)
        try:
            for path in ("/api/settings", "/api/state", "/api/fills"):
                self.assertEqual(self.client.get(path).status_code, 401, path)
        finally:
            self.server.app.dependency_overrides.update(overrides)


class ViewerStateTests(unittest.TestCase):
    def test_public_state_removes_admin_data(self):
        load_server()
        import security as SEC
        state = {"limits": {"realized_net_today": -12.5, "entries_today": 2}, "settings_version": 9, "settings_errors": [],
                 "ws": {"status": "connected", "error": "x", "reconnects": 3}, "decision": {"decision": "WAIT"},
                 "review": {"ok": False, "status": "REVIEW", "detail": "secret detail", "doc": {"news": 1}},
                 "scalp": {"checks": [{"id": "F33", "status": "PASS", "detail": "1/4 entries today, realized net -12.50 USD"},
                                      {"id": "F35", "status": "PASS", "detail": "net R 1.06"}]}}
        sources = [{"key": "okx", "name": "OKX", "family": "okx", "status": "error", "error": "HTTP 451 at https://x", "url": "https://x", "last_success": None}]
        out = SEC.public_state(state, sources, {"state": "idle", "log": ["internal"]})
        for k in ("limits", "settings_version", "settings_errors", "user"):
            self.assertNotIn(k, out)
        self.assertEqual(out["ws"], {"status": "connected"})
        self.assertEqual(out["recovery"], {"state": "idle"})
        self.assertNotIn("error", out["sources"][0])
        self.assertNotIn("url", out["sources"][0])
        self.assertNotIn("detail", out["review"])
        f33 = [c for c in out["scalp"]["checks"] if c["id"] == "F33"][0]
        self.assertNotIn("realized", f33["detail"])
        self.assertEqual(out["decision"], {"decision": "WAIT"})
        self.assertEqual(state["limits"]["entries_today"], 2)  # original untouched


if __name__ == "__main__":
    unittest.main()
