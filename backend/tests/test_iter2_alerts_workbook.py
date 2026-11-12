# Iteration 2: four/six-factor completeness, workbook audit, reference archive, alerts (email), regressions
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api"


def _login(email, pw):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def admin():
    return {"Authorization": f"Bearer {_login(os.environ.get('TEST_ADMIN_EMAIL', 'admin@example.com'), os.environ.get('TEST_ADMIN_PASSWORD', ''))}"}


@pytest.fixture(scope="module")
def viewer():
    return {"Authorization": f"Bearer {_login(os.environ.get('TEST_VIEWER_EMAIL', 'viewer@example.com'), os.environ.get('TEST_VIEWER_PASSWORD', ''))}"}


# Four / six factor models
class TestModels:
    def test_four_six_complete(self, admin):
        s = requests.get(f"{API}/state", headers=admin, timeout=60).json()
        ff = s["four_factor"]
        assert ff["complete"] is True, ff
        assert ff["factors"]["etf"]["valid"] and "Bitbo" in ff["factors"]["etf"]["detail"]
        fa = ff["factors"]["funding_all"]
        assert fa["valid"] and fa.get("proxy") is True and "annualized" in fa["detail"]
        assert isinstance(ff["score"], (int, float)) and ff["band"]
        sx = s["six_factor"]
        assert sx["complete"] is True and isinstance(sx["score"], (int, float)) and sx["direction"]


# Workbook audit
class TestWorkbookAudit:
    def test_audit_workbook(self, admin):
        w = requests.get(f"{API}/audit", headers=admin, timeout=60).json()["workbook"]
        assert w["inspected"] is True
        assert len(w["coverage"]) == 25
        for r in w["coverage"]:
            assert isinstance(r["formulas"], int) and isinstance(r["values"], int) and r["findings"]
        assert len(w["hidden"]) > 0
        assert ["DeltaAPIHost", "#REF!"] in [list(x) for x in w["defined_names"]]
        assert isinstance(w["queries"], list) and len(w["queries"]) > 0
        assert len(w["five_pillar_fields"]) == 99


# Reference archive (read-only)
class TestReference:
    def test_sheets(self, admin):
        d = requests.get(f"{API}/reference/sheets", headers=admin).json()
        assert "Backtest" in d["sheets"] and d["sheets"]["Backtest"].get("primary", 0) > 0
        assert any(v.get("backup-unique") for v in d["sheets"].values())

    def test_sheet_page_admin_only(self, admin, viewer):
        assert requests.get(f"{API}/reference/sheet", params={"name": "Backtest", "page": 1}, headers=viewer).status_code == 403
        r = requests.get(f"{API}/reference/sheet", params={"name": "Backtest", "page": 1}, headers=admin)
        assert r.status_code == 200
        d = r.json()
        assert len(d["rows"]) == 50 and d["total"] >= 50 and d["page"] == 1
        assert "_id" not in d["rows"][0]
        r2 = requests.get(f"{API}/reference/sheet", params={"name": "Backtest", "page": 2}, headers=admin).json()
        assert r2["rows"][0]["row"] != d["rows"][0]["row"]

    def test_sheet_bad_params(self, admin):
        r = requests.get(f"{API}/reference/sheet", params={"name": "Backtest", "page": 0}, headers=admin)
        print("page=0 ->", r.status_code, r.text[:200])
        r = requests.get(f"{API}/reference/sheet", params={"name": "Backtest", "size": 0}, headers=admin)
        print("size=0 ->", r.status_code, r.text[:200])

    def test_unauth(self):
        assert requests.get(f"{API}/reference/sheets").status_code == 401


# Alerts (sends a REAL email — max 2 calls)
class TestAlerts:
    def test_alerts_get(self, admin, viewer):
        assert requests.get(f"{API}/alerts", headers=viewer).status_code == 403  # admin-only since the access-control update
        d = requests.get(f"{API}/alerts", headers=admin).json()
        to = os.environ.get("ALERT_EMAIL", "")
        if "@" in to:  # recipient is masked: first 2 letters + domain only
            assert d["email_to"] == to.split("@")[0][:2] + "…@" + to.split("@")[1]
            assert to.split("@")[0] not in str(d) or len(to.split("@")[0]) <= 2
        assert "not configured" in d["sms"]

    def test_viewer_forbidden(self, viewer):
        assert requests.post(f"{API}/alerts/test", headers=viewer).status_code == 403

    def test_send_then_429(self, admin):
        log = requests.get(f"{API}/alerts", headers=admin).json()["log"]
        st = requests.get(f"{API}/health").json()
        print("health", st, "last log", log[:1])
        r1 = requests.post(f"{API}/alerts/test", headers=admin, timeout=60)
        print("call1", r1.status_code, r1.text)
        r2 = requests.post(f"{API}/alerts/test", headers=admin, timeout=60)
        print("call2", r2.status_code, r2.text)
        assert r1.status_code in (200, 429)
        if r1.status_code == 200:
            assert r1.json()["result"] == "sent"
        assert r2.status_code == 429


# Regressions from iteration 1
class TestRegression:
    def test_origin_guard(self, admin):
        r = requests.put(f"{API}/review", headers={**admin, "Origin": BASE_URL}, json={})
        assert r.status_code != 403 or "Cross-origin" not in r.text
        r = requests.put(f"{API}/review", headers={**admin, "Origin": "https://evil.example.com"}, json={})
        assert r.status_code == 403

    def test_invalid_oid(self, admin):
        assert requests.delete(f"{API}/events/notanoid", headers=admin).status_code == 404

    def test_bls_events(self, admin):
        d = requests.get(f"{API}/events", headers=admin).json()
        print(str(d)[:600])
