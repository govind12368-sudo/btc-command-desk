# BTC Command Desk — backend regression suite (auth, RBAC, state calc, settings, events, fills, validation, planner, audit, upgrades)
import os
import time
import pytest
import requests
from datetime import datetime, timedelta, timezone

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": os.environ.get("TEST_ADMIN_EMAIL", "admin@example.com"), "password": os.environ.get("TEST_ADMIN_PASSWORD", "")}
VIEWER = {"email": os.environ.get("TEST_VIEWER_EMAIL", "viewer@example.com"), "password": os.environ.get("TEST_VIEWER_PASSWORD", "")}


def _login(creds):
    r = requests.post(f"{BASE_URL}/api/auth/login", json=creds)
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code}")
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN)


@pytest.fixture(scope="module")
def viewer():
    return _login(VIEWER)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


# ---------- auth ----------
class TestAuth:
    def test_login_admin(self):
        r = requests.post(f"{BASE_URL}/api/auth/login", json=ADMIN)
        assert r.status_code == 200
        d = r.json()
        assert d["role"] == "admin" and isinstance(d["token"], str)
        sc = r.headers.get("set-cookie", "").lower()
        assert "access_token" in sc and "httponly" in sc

    def test_login_viewer_role(self):
        r = requests.post(f"{BASE_URL}/api/auth/login", json=VIEWER)
        assert r.status_code == 200 and r.json()["role"] == "user"  # iter3: view-only role renamed viewer -> user

    def test_wrong_password(self):
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": "nobody_test@example.com", "password": "x"})
        assert r.status_code == 401

    def test_me_and_unauth(self, admin):
        assert admin.get(f"{BASE_URL}/api/auth/me").json()["email"] == ADMIN["email"]
        assert requests.get(f"{BASE_URL}/api/state").status_code == 401

    def test_cookie_session_refresh_logout(self):
        s = requests.Session()
        assert s.post(f"{BASE_URL}/api/auth/login", json=ADMIN).status_code == 200
        assert s.get(f"{BASE_URL}/api/auth/me").status_code == 200
        assert s.post(f"{BASE_URL}/api/auth/refresh").status_code == 200
        assert s.post(f"{BASE_URL}/api/auth/logout").status_code == 200


# ---------- RBAC / origin ----------
class TestRBAC:
    @pytest.mark.parametrize("method,path,body", [
        ("put", "/api/settings", {"version": 1, "values": {}}),
        ("post", "/api/fills", {"strategy": "scalp", "direction": "LONG", "entry_time": "2025-01-01T00:00:00Z", "entry_price": 1, "qty_btc": 1}),
        ("post", "/api/upgrades", {"prompt": "x"}),
        ("post", "/api/events", {"title": "TEST", "scheduled_ist": "2026-02-01T10:00", "impact": "LOW", "source_url": "https://x.com"}),
        ("post", "/api/validation", {"direction": "LONG", "entry": 100, "stop": 90, "target": 120}),
        ("put", "/api/review", {"version": 0, "risk": True}),
        ("post", "/api/recovery/run", None),
    ])
    def test_viewer_writes_forbidden(self, viewer, method, path, body):
        r = getattr(viewer, method)(f"{BASE_URL}{path}", json=body)
        assert r.status_code == 403, f"{path} -> {r.status_code}"

    def test_cross_origin_rejected(self, admin):
        r = admin.post(f"{BASE_URL}/api/upgrades", json={"prompt": "TEST_x"}, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403


# ---------- state calc ----------
class TestState:
    def test_state_shape(self, admin):
        d = admin.get(f"{BASE_URL}/api/state").json()
        ids = [f"F{i}" for i in range(26, 38) if i != 31]  # F31 excluded by policy
        for k in ("scalp", "swing"):
            assert [c["id"] for c in d[k]["checks"]] == ids
        # iteration 2: Bitbo ETF + multi-exchange funding proxy make the models complete
        assert d["four_factor"]["complete"] is True and d["four_factor"]["missing"] == []
        assert d["six_factor"]["complete"] is True
        assert 0 <= d["live_direction"]["coverage"] <= 5

    @pytest.mark.parametrize("tf", ["5m", "15m", "1h", "4h"])
    def test_candles(self, admin, tf):
        r = admin.get(f"{BASE_URL}/api/candles", params={"tf": tf})
        assert r.status_code == 200

    def test_market(self, admin):
        assert admin.get(f"{BASE_URL}/api/market").status_code == 200


# ---------- settings ----------
class TestSettings:
    def test_update_and_stale(self, admin):
        g = admin.get(f"{BASE_URL}/api/settings").json()
        ver, vals = g["version"], g["values"]
        key = "max_entries_per_day"
        orig = vals[key]
        r = admin.put(f"{BASE_URL}/api/settings", json={"version": ver, "values": {key: orig + 1}})
        assert r.status_code == 200, r.text
        g2 = admin.get(f"{BASE_URL}/api/settings").json()
        assert g2["version"] == ver + 1 and g2["values"][key] == orig + 1
        stale = admin.put(f"{BASE_URL}/api/settings", json={"version": ver, "values": {key: orig}})
        assert stale.status_code == 409
        bad = admin.put(f"{BASE_URL}/api/settings", json={"version": g2["version"], "values": {key: "abc"}})
        assert bad.status_code == 422
        rr = admin.put(f"{BASE_URL}/api/settings", json={"version": g2["version"], "values": {key: orig}})
        assert rr.status_code == 200


# ---------- events ----------
class TestEvents:
    def test_event_crud(self, admin):
        body = {"title": "TEST_event CPI", "scheduled_ist": "2026-03-01T18:00", "impact": "HIGH", "source_url": "https://www.bls.gov/x"}
        r = admin.post(f"{BASE_URL}/api/events", json=body)
        assert r.status_code == 200, r.text
        eid = r.json()["id"]
        u = admin.put(f"{BASE_URL}/api/events/{eid}", json={**body, "title": "TEST_event edited"})
        assert u.status_code == 200
        evs = admin.get(f"{BASE_URL}/api/events").json()
        items = evs["manual"]
        assert any(e.get("id") == eid and e["title"] == "TEST_event edited" for e in items)
        assert admin.delete(f"{BASE_URL}/api/events/{eid}").status_code == 200

    def test_event_invalid_url(self, admin):
        r = admin.post(f"{BASE_URL}/api/events", json={"title": "TEST_bad", "scheduled_ist": "2026-03-01T18:00", "impact": "HIGH", "source_url": "javascript:alert(1)"})
        assert r.status_code == 422


# ---------- fills ----------
class TestFills:
    created = []

    def test_fill_pnl(self, admin):
        now = datetime.now(timezone.utc)
        body = {"strategy": "scalp", "direction": "LONG", "entry_time": _iso(now - timedelta(hours=2)), "entry_price": 100000, "qty_btc": 0.01,
                "exit_time": _iso(now - timedelta(hours=1)), "exit_price": 101000, "fees_usd": 2, "funding_usd": 0.5, "stop_price": 99000,
                "exchange_id": f"TEST_{int(time.time())}"}
        r = admin.post(f"{BASE_URL}/api/fills", json=body)
        assert r.status_code == 200, r.text
        f = r.json(); TestFills.created.append(f["id"])
        items = admin.get(f"{BASE_URL}/api/fills").json()["items"]
        it = next(x for x in items if x["id"] == f["id"])
        pnl = it.get("pnl") or it
        assert abs(pnl["gross"] - 10.0) < 1e-6 and abs(pnl["net"] - 7.5) < 1e-6
        # duplicate exchange id
        assert admin.post(f"{BASE_URL}/api/fills", json=body).status_code == 409
        # edit with version, then stale
        e = admin.put(f"{BASE_URL}/api/fills/{f['id']}", json={**body, "version": f["version"], "fees_usd": None})
        assert e.status_code == 200 and e.json()["version"] == f["version"] + 1
        items = admin.get(f"{BASE_URL}/api/fills").json()["items"]
        it = next(x for x in items if x["id"] == f["id"])
        pnl = it.get("pnl") or it
        assert pnl["net"] is None and pnl["net_known"] is False
        assert admin.put(f"{BASE_URL}/api/fills/{f['id']}", json={**body, "version": f["version"]}).status_code == 409

    def test_fill_validation(self, admin):
        r = admin.post(f"{BASE_URL}/api/fills", json={"strategy": "scalp", "direction": "LONG", "entry_time": "2025-01-01T00:00:00", "entry_price": 1, "qty_btc": 1})
        assert r.status_code == 422

    def test_csv_import_dup(self, admin):
        xid = f"TEST_CSV_{int(time.time())}"
        csv = ("strategy,direction,entry_time,entry_price,qty_btc,exit_time,exit_price,fees_usd,funding_usd,exchange_id\n"
               f"swing,SHORT,2025-12-01T00:00:00Z,100000,0.01,2025-12-01T05:00:00Z,99000,1,0,{xid}\n"
               f"swing,SHORT,2025-12-01T00:00:00Z,100000,0.01,2025-12-01T05:00:00Z,99000,1,0,{xid}\n")
        r = admin.post(f"{BASE_URL}/api/fills/import", json={"csv_text": csv})
        assert r.status_code == 200
        d = r.json()
        assert d["added"] == 1 and d["duplicates_skipped"] == 1
        items = admin.get(f"{BASE_URL}/api/fills").json()["items"]
        TestFills.created += [x["id"] for x in items if x.get("exchange_id") == xid]

    def test_export_csv(self, admin):
        r = admin.get(f"{BASE_URL}/api/export/fills")
        assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
        assert r.text.startswith("id,exchange_id")

    def test_zz_archive(self, admin):
        for fid in TestFills.created:
            assert admin.delete(f"{BASE_URL}/api/fills/{fid}").status_code == 200
        ids = [x["id"] for x in admin.get(f"{BASE_URL}/api/fills").json()["items"]]
        assert not set(TestFills.created) & set(ids)


# ---------- validation ----------
class TestValidation:
    def test_history(self, admin):
        d = admin.get(f"{BASE_URL}/api/validation").json()
        h = d["history"]
        print("history:", {k: v for k, v in h.items() if not isinstance(v, (list, dict))})
        assert h.get("bars", 0) >= 8000

    def test_snapshot_flow(self, admin):
        assert admin.post(f"{BASE_URL}/api/validation", json={"direction": "LONG", "entry": 100, "stop": 110, "target": 120}).status_code == 422
        assert admin.post(f"{BASE_URL}/api/validation", json={"direction": "SHORT", "entry": 100, "stop": 90, "target": 80}).status_code == 422
        r = admin.post(f"{BASE_URL}/api/validation", json={"direction": "LONG", "entry": 100000, "stop": 99000, "target": 102000, "note": "TEST_snap"})
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["outcome"] == "PENDING" and s["model"] == "MANUAL"
        u = admin.put(f"{BASE_URL}/api/validation/{s['id']}", json={"version": s["version"], "direction": "LONG", "entry": 100000, "stop": 98000, "target": 103000})
        assert u.status_code == 200 and u.json()["outcome"] == "PENDING" and u.json()["stop"] == 98000


# ---------- planner ----------
class TestPlanner:
    @pytest.mark.parametrize("body", [{"direction": "LONG", "entry_mode": "spot"}, {"direction": "SHORT", "entry_mode": "spot"}])
    def test_plan(self, admin, body):
        r = admin.post(f"{BASE_URL}/api/planner", json=body)
        assert r.status_code == 200, r.text
        d = r.json()
        print("planner keys:", list(d.keys()))
        assert len(d.get("guardrails", [])) == 9  # iter4: review guardrail removed
        assert len(d.get("scenarios", [])) == 9

    def test_plan_limit(self, admin):
        st = admin.get(f"{BASE_URL}/api/state").json()
        px = st.get("price") or 100000
        r = admin.post(f"{BASE_URL}/api/planner", json={"direction": "LONG", "entry_mode": "limit", "limit_price": round(float(px) * 0.99, 1)})
        assert r.status_code == 200, r.text

    def test_plan_invalid(self, admin):
        assert admin.post(f"{BASE_URL}/api/planner", json={"direction": "UP"}).status_code == 422


# ---------- audit ----------
class TestAudit:
    def test_audit(self, admin):
        d = admin.get(f"{BASE_URL}/api/audit").json()
        assert d["workbook"]["inspected"] is True and len(d["workbook"]["coverage"]) == 25
        names = " ".join(str(i).lower() for i in d["current_issues"])
        print("current_issues:", names[:400])
        assert "farside" not in names  # source removed: it never returned data

    def test_recovery(self, admin):
        r = admin.post(f"{BASE_URL}/api/recovery/run")
        assert r.status_code in (200, 429), r.text
        print("recovery:", r.text[:300])


# ---------- upgrades ----------
class TestUpgrades:
    def test_upgrade_flow(self, admin, viewer):
        r = admin.post(f"{BASE_URL}/api/upgrades", json={"title": "TEST_up", "prompt": "Add a thing", "expected": "Better"})
        assert r.status_code == 200, r.text
        u = r.json()
        e = admin.put(f"{BASE_URL}/api/upgrades/{u['id']}", json={"version": u["version"], "title": "TEST_up2", "prompt": "Add a thing", "done": True})
        assert e.status_code == 200 and e.json()["done"] is True
        assert viewer.get(f"{BASE_URL}/api/upgrades/{u['id']}/prepared").status_code == 403  # admin-only
        p = admin.get(f"{BASE_URL}/api/upgrades/{u['id']}/prepared")
        assert p.status_code == 200
        t = p.text
        assert "Website:" in t and "TEST_up2" in t and "diagnostics" in t
        for secret in (ADMIN["password"] or "JWT", "JWT", "api_key", "COINGLASS_API_KEY", "password"):
            assert secret not in t
        assert admin.delete(f"{BASE_URL}/api/upgrades/{u['id']}").status_code == 200
        assert all(x["id"] != u["id"] for x in admin.get(f"{BASE_URL}/api/upgrades").json()["items"])

    def test_prompt_too_long(self, admin):
        assert admin.post(f"{BASE_URL}/api/upgrades", json={"prompt": "x" * 6001}).status_code == 422

    def test_limit_20(self, admin):
        existing = len(admin.get(f"{BASE_URL}/api/upgrades").json()["items"])
        made = []
        try:
            for i in range(20 - existing):
                made.append(admin.post(f"{BASE_URL}/api/upgrades", json={"prompt": f"TEST_lim {i}"}).json()["id"])
            assert admin.post(f"{BASE_URL}/api/upgrades", json={"prompt": "TEST_over"}).status_code == 422
        finally:
            for m in made:
                admin.delete(f"{BASE_URL}/api/upgrades/{m}")


# ---------- review / news ----------
class TestReview:
    def test_review_persist(self, admin):
        g = admin.get(f"{BASE_URL}/api/review").json()
        print("review:", g)
        ver = g.get("version", 0)
        r = admin.put(f"{BASE_URL}/api/review", json={"version": ver, "risk": True, "event_decision": "PASS"})
        assert r.status_code == 200, r.text
        g2 = admin.get(f"{BASE_URL}/api/review").json()
        assert g2["risk"]["ok"] is True and g2["event_decision"] == "PASS" and g2["version"] == ver + 1

    def test_news(self, admin):
        d = admin.get(f"{BASE_URL}/api/news").json()
        items = d.get("items", [])
        print("news count", len(items))
        if items:
            nid = items[0]["id"]
            assert admin.put(f"{BASE_URL}/api/news/{nid}/review", json={"status": "REVIEWED"}).status_code == 200
            assert admin.put(f"{BASE_URL}/api/news/{nid}/review", json={"status": "BAD"}).status_code == 422
            admin.put(f"{BASE_URL}/api/news/{nid}/review", json={"status": items[0].get("review_status") or "UNREVIEWED"})


# ---------- robustness ----------
class TestRobustness:
    def test_invalid_object_ids(self, admin):
        codes = {
            "fill_put": admin.put(f"{BASE_URL}/api/fills/notanid", json={"strategy": "scalp", "direction": "LONG", "entry_time": "2025-01-01T00:00:00Z", "entry_price": 1, "qty_btc": 1, "version": 1}).status_code,
            "upgrade_del": admin.delete(f"{BASE_URL}/api/upgrades/notanid").status_code,
            "prepared": admin.get(f"{BASE_URL}/api/upgrades/notanid/prepared").status_code,
        }
        print(codes)
        assert all(c < 500 for c in codes.values()), codes
