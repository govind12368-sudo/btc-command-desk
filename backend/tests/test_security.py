"""Security hardening (offline): headers, hidden docs, body limit, rate limiter, CSV escaping, config warnings,
generic login errors + per-IP lock, admin TOTP, refresh-token rotation/reuse detection, registration approval."""
import os
import sys
import types
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from bson import ObjectId

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_access_control import load_server  # noqa: E402


# ------------------------------------------------------------ tiny in-memory Mongo stand-in
def _match(doc, flt):
    for k, v in flt.items():
        cur = doc.get(k)
        if isinstance(v, dict) and any(op.startswith("$") for op in v):
            for op, arg in v.items():
                if op == "$gte" and not (cur is not None and cur >= arg):
                    return False
                if op == "$ne" and cur == arg:
                    return False
                if op == "$in" and cur not in arg:
                    return False
        elif cur != v:
            return False
    return True


class FakeColl:
    def __init__(self):
        self.docs = []

    async def find_one(self, flt=None, *a, **k):
        for d in self.docs:
            if _match(d, flt or {}):
                return dict(d)
        return None

    async def insert_one(self, doc):
        doc = dict(doc)
        doc.setdefault("_id", ObjectId())
        self.docs.append(doc)
        return types.SimpleNamespace(inserted_id=doc["_id"])

    async def count_documents(self, flt):
        return sum(1 for d in self.docs if _match(d, flt))

    def _apply(self, d, upd):
        for k, v in upd.get("$set", {}).items():
            d[k] = v
        for k, v in upd.get("$inc", {}).items():
            d[k] = d.get(k, 0) + v
        for k in upd.get("$unset", {}):
            d.pop(k, None)
        for k, v in upd.get("$pull", {}).items():
            d[k] = [x for x in d.get(k, []) if x != v]

    async def update_one(self, flt, upd, upsert=False):
        for d in self.docs:
            if _match(d, flt):
                self._apply(d, upd)
                return types.SimpleNamespace(modified_count=1)
        if upsert:
            d = {k: v for k, v in flt.items() if not isinstance(v, dict)}
            self._apply(d, upd)
            self.docs.append(d)
        return types.SimpleNamespace(modified_count=0)

    async def update_many(self, flt, upd):
        n = 0
        for d in self.docs:
            if _match(d, flt):
                self._apply(d, upd)
                n += 1
        return types.SimpleNamespace(modified_count=n)

    async def delete_one(self, flt):
        for i, d in enumerate(self.docs):
            if _match(d, flt):
                self.docs.pop(i)
                break

    async def delete_many(self, flt):
        self.docs = [d for d in self.docs if not _match(d, flt)]


class FakeDB:
    def __init__(self):
        self._c = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._c.setdefault(name, FakeColl())


class FakeRequest:
    def __init__(self, ip="1.2.3.4", cookies=None):
        self.headers = {"x-forwarded-for": ip, "user-agent": "unit-test"}
        self.cookies = cookies or {}
        self.client = types.SimpleNamespace(host=ip)


def cookies_of(resp):
    out = {}
    for k, v in resp.raw_headers:
        if k == b"set-cookie":
            name, val = v.decode().split(";", 1)[0].split("=", 1)
            out[name] = val.strip('"')
    return out


# ------------------------------------------------------------ middleware / helpers
class HeaderAndHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = load_server()
        from fastapi.testclient import TestClient
        cls.client = TestClient(cls.server.app, raise_server_exceptions=False)
        import security
        cls.SEC = security

    def test_security_headers_on_api(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        for h in ("strict-transport-security", "x-content-type-options", "x-frame-options", "referrer-policy", "permissions-policy"):
            self.assertIn(h, r.headers)
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_docs_hidden(self):
        for p in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.client.get(p).status_code, 404, p)

    def test_body_limit(self):
        r = self.client.post("/api/auth/login", content=b"x" * 1_200_000, headers={"content-type": "application/json"})
        self.assertEqual(r.status_code, 413)

    def test_sliding_window(self):
        w = self.SEC.SlidingWindow(3, 60)
        self.assertEqual([w.hit("k", t) for t in (0, 1, 2)], [0, 0, 0])
        self.assertGreater(w.hit("k", 3), 0)
        self.assertEqual(w.hit("k", 61), 0)

    def test_csv_formula_escape(self):
        f = self.SEC.csv_safe
        self.assertEqual(f("=HYPERLINK(\"x\")"), "'=HYPERLINK(\"x\")")
        self.assertEqual(f("@SUM(A1)"), "'@SUM(A1)")
        self.assertEqual(f("-12.5"), "-12.5")
        self.assertEqual(f(-3), -3)
        self.assertEqual(f("LONG"), "LONG")

    def test_weak_config_reported_not_fatal(self):
        with patch.dict(os.environ, {"JWT_SECRET": "short", "ADMIN_PASSWORD": "password"}):
            w = self.SEC.config_warnings()
        self.assertTrue(any("JWT_SECRET" in x for x in w))
        self.assertTrue(any("ADMIN_PASSWORD" in x for x in w))

    def test_totp_and_recovery_helpers(self):
        import pyotp
        s = self.SEC.new_totp_secret()
        self.assertTrue(self.SEC.totp_ok(s, pyotp.TOTP(s).now()))
        self.assertFalse(self.SEC.totp_ok(s, "000000") and pyotp.TOTP(s).now() != "000000")
        self.assertEqual(self.SEC.decrypt_secret(self.SEC.encrypt_secret(s)), s)
        codes, hashes = self.SEC.new_recovery_codes()
        self.assertEqual(len(codes), 8)
        self.assertEqual(self.SEC.hash_code(codes[0]), hashes[0])
        self.assertTrue(self.SEC.qr_svg("otpauth://totp/x").lstrip().startswith("<"))


# ------------------------------------------------------------ account flows against the fake DB
class AccountFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        load_server()
        import accounts
        import security
        import auth_core
        self.A, self.SEC, self.AC = accounts, security, auth_core
        self.db = FakeDB()
        self.patches = [patch.object(accounts, "db", self.db), patch.object(security, "db", self.db), patch.object(auth_core, "db", self.db)]
        for p in self.patches:
            p.start()
        self.pw = "Corr3ct!Horse#"
        await self.db.users.insert_one({"email": "admin@example.com", "username": "admin", "name": "Admin", "role": "admin",
                                        "status": "active", "pv": 0, "password_hash": auth_core.hash_pw(self.pw)})

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()

    async def login(self, ident="admin@example.com", pw=None, ip="1.2.3.4", otp=None):
        from fastapi import Response
        resp = Response()
        body = self.A.LoginIn(email=ident, password=pw or self.pw, otp=otp)
        out = await self.A.login(body, FakeRequest(ip), resp)
        return out, resp

    async def test_generic_error_for_unknown_user_and_wrong_password(self):
        from fastapi import HTTPException
        for ident, pw in (("nobody@example.com", "x"), ("admin@example.com", "wrong")):
            with self.assertRaises(HTTPException) as c:
                await self.login(ident, pw)
            self.assertEqual((c.exception.status_code, c.exception.detail), (401, "Invalid username or password"))

    async def test_per_ip_lock_after_10_failures(self):
        from fastapi import HTTPException
        for i in range(10):
            with self.assertRaises(HTTPException):
                await self.login(f"user{i}@example.com", "bad", ip="9.9.9.9")
        with self.assertRaises(HTTPException) as c:
            await self.login(ip="9.9.9.9")  # even the right password is refused from this IP for 15 min
        self.assertEqual(c.exception.status_code, 429)
        out, _ = await self.login(ip="8.8.8.8")
        self.assertEqual(out["role"], "admin")

    async def test_two_factor_required_after_enrolment(self):
        import pyotp
        from fastapi import HTTPException
        secret = self.SEC.new_totp_secret()
        codes, hashes = self.SEC.new_recovery_codes()
        await self.db.users.update_one({"email": "admin@example.com"}, {"$set": {"totp_enabled": True, "totp_secret_enc": self.SEC.encrypt_secret(secret),
                                                                                "recovery_hashes": hashes}})
        with self.assertRaises(HTTPException) as c:
            await self.login()
        self.assertEqual(c.exception.detail, "2FA_REQUIRED")
        with self.assertRaises(HTTPException) as c:
            await self.login(otp="123456" if pyotp.TOTP(secret).now() != "123456" else "654321")
        self.assertEqual(c.exception.detail, "Invalid two-factor code")
        out, _ = await self.login(otp=pyotp.TOTP(secret).now())
        self.assertTrue(out["totp_enabled"])
        out, _ = await self.login(otp=codes[0])  # recovery code works once
        u = await self.db.users.find_one({"email": "admin@example.com"})
        self.assertEqual(len(u["recovery_hashes"]), 7)

    async def test_refresh_rotation_and_reuse_detection(self):
        from fastapi import Response, HTTPException
        _, resp = await self.login()
        first = cookies_of(resp)["refresh_token"]
        r2 = Response()
        await self.A.refresh(FakeRequest(cookies={"refresh_token": first}), r2)
        second = cookies_of(r2)["refresh_token"]
        self.assertNotEqual(first, second)
        # within the grace window a duplicate refresh (two tabs) is tolerated
        await self.A.refresh(FakeRequest(cookies={"refresh_token": first}), Response())
        # later reuse of the old token = theft: every session ends and access tokens are invalidated (pv+1)
        for s in self.db.sessions.docs:
            if s.get("revoked_reason") == "rotated":
                s["revoked_at"] = self.A.iso(self.A.now_utc() - timedelta(minutes=5))
        with self.assertRaises(HTTPException) as c:
            await self.A.refresh(FakeRequest(cookies={"refresh_token": first}), Response())
        self.assertEqual(c.exception.status_code, 401)
        self.assertTrue(all(s["revoked"] for s in self.db.sessions.docs))
        u = await self.db.users.find_one({"email": "admin@example.com"})
        self.assertEqual(u["pv"], 1)
        kinds = [e["kind"] for e in self.db.security_events.docs]
        self.assertIn("refresh_token_reuse", kinds)

    async def test_logout_revokes_session(self):
        from fastapi import Response, HTTPException
        _, resp = await self.login()
        tok = cookies_of(resp)["refresh_token"]
        await self.A.logout(FakeRequest(cookies={"refresh_token": tok}), Response())
        sess = self.db.sessions.docs[0]
        self.assertEqual((sess["revoked"], sess["revoked_reason"]), (True, "logout"))

    async def registration(self, mode):
        from fastapi import Response
        async def fake_settings():
            return {"registration_mode": mode}, 1
        body = self.A.RegisterIn(name="New Person", username="newperson", email="new@example.com", password="N3w!Person#pw", confirm_password="N3w!Person#pw")
        resp = Response()
        with patch.object(self.A, "get_settings", fake_settings), patch.object(self.A, "send_verify", new=lambda u: _noop()):
            out = await self.A.register(body, FakeRequest(), resp)
        return out, resp

    async def test_registration_approval_and_closed(self):
        from fastapi import HTTPException
        out, resp = await self.registration("approval")
        self.assertTrue(out["pending"])
        self.assertNotIn("refresh_token", cookies_of(resp))
        u = await self.db.users.find_one({"email": "new@example.com"})
        self.assertEqual(u["status"], "pending")
        with self.assertRaises(HTTPException) as c:
            await self.login("new@example.com", "N3w!Person#pw")
        self.assertEqual(c.exception.status_code, 403)
        await self.db.users.delete_one({"email": "new@example.com"})
        with self.assertRaises(HTTPException) as c:
            await self.registration("closed")
        self.assertEqual(c.exception.status_code, 403)


async def _noop():
    return None


if __name__ == "__main__":
    unittest.main()
