# Iteration 4 — live API checks: news/calendar/daily review have NO effect on readiness, decisions, direction or planner
import os
import time
import copy
import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api"
MDB = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _login(user, pw):
    r = requests.post(f"{API}/auth/login", json={"email": user, "password": pw})
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code}")
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}", "Origin": BASE_URL})
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(os.environ.get("TEST_ADMIN_EMAIL", "admin@example.com"), os.environ.get("TEST_ADMIN_PASSWORD", ""))


@pytest.fixture(scope="module")
def user():
    return _login(os.environ.get("TEST_VIEWER_EMAIL", "viewer@example.com"), os.environ.get("TEST_VIEWER_PASSWORD", ""))


def _state(s):
    r = s.get(f"{API}/state")
    assert r.status_code == 200
    return r.json()


def _invariants(st):
    ld = st["live_direction"]
    return {"decision": st["decision"]["decision"],
            "scalp": (st["scalp"]["status"], st["scalp"]["passed"]),
            "swing": (st["swing"]["status"], st["swing"]["passed"]),
            "direction": ld.get("direction"), "pending_count": ld.get("pending_count"),
            "candle_close": ld.get("candle_close"),
            "risk_capital": st["readiness"]["components"]["risk_capital_controls"]["fraction"],
            "weights": {k: round(v["weight"], 9) for k, v in st["readiness"]["components"].items()}}


def _wait_review_version(s, version, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        st = _state(s)
        if (st.get("review") or {}).get("doc", {}).get("version") == version:
            return st
        time.sleep(1.5)
    pytest.fail(f"state.review never reflected review version {version}")


# --- readiness structure
class TestReadinessStructure:
    def test_components_and_weights(self, admin):
        st = _state(admin)
        rd = st["readiness"]
        comps = rd["components"]
        assert "news_event_flow_gate" not in comps
        assert set(comps) == {"combined_mandatory", "five_pillar_coverage", "scalping_checks", "swing_checks", "risk_capital_controls"}
        assert abs(sum(c["weight"] for c in comps.values()) - 1.0) < 1e-9
        assert abs(sum(c["weight"] * c["fraction"] for c in comps.values()) - rd["score"]) < 1e-9
        assert rd["excluded_inputs"] == ["news", "calendar", "daily_review"]
        assert "News, calendar events and daily review have no effect" in rd["note"]

    def test_review_advisory_flags(self, admin, user):
        for s in (admin, user):
            rv = _state(s).get("review")
            assert rv is not None
            assert rv["advisory"] is True and rv["affects_decision"] is False and rv["affects_direction"] is False

    def test_checks_eleven_f31_excluded(self, admin):
        st = _state(admin)
        for k in ("scalp", "swing"):
            ids = [c["id"] for c in st[k]["checks"]]
            assert len(ids) == 11 and "F31" not in ids


# --- review edits do not change decisions/readiness/direction
class TestReviewInvariance:
    def test_review_toggle_invariance(self, admin):
        orig = MDB.reviews.find_one({"_id": admin.get(f"{API}/review").json()["id"]})
        orig_copy = copy.deepcopy(orig)
        try:
            cur = admin.get(f"{API}/review").json()
            before = _state(admin)
            r = admin.put(f"{API}/review", json={"version": cur["version"], "event_decision": "BLOCKED", "flow_decision": "BLOCKED",
                                                  "news": False, "risk": False})
            assert r.status_code == 200, r.text
            v1 = r.json()["version"]
            blocked = _wait_review_version(admin, v1)
            assert blocked["review"]["status"] == "BLOCKED"
            r = admin.put(f"{API}/review", json={"version": v1, "event_decision": "PASS", "flow_decision": "PASS",
                                                  "news": True, "risk": True, "venue": True, "journal": True})
            assert r.status_code == 200, r.text
            v2 = r.json()["version"]
            passed = _wait_review_version(admin, v2)
            assert passed["review"]["status"] in ("PASS", "BLOCKED", "REVIEW")
            print("review statuses:", blocked["review"]["status"], passed["review"]["status"])
            a, b, c = _invariants(before), _invariants(blocked), _invariants(passed)
            print("before", a, "\nblocked", b, "\npass", c)
            # risk/capital + weights are fully independent of market drift
            assert a["weights"] == b["weights"] == c["weights"]
            assert a["risk_capital"] == b["risk_capital"] == c["risk_capital"]
            # market-driven fields: only compare when the candle did not move
            if a["candle_close"] == b["candle_close"] == c["candle_close"]:
                assert a["direction"] == b["direction"] == c["direction"]
                assert a["pending_count"] == b["pending_count"] == c["pending_count"]
            if a["scalp"] == b["scalp"] == c["scalp"] and a["swing"] == b["swing"] == c["swing"]:
                assert a["decision"] == b["decision"] == c["decision"]
            # readiness score = components only (no hidden review term)
            for st in (blocked, passed):
                rd = st["readiness"]
                assert abs(sum(x["weight"] * x["fraction"] for x in rd["components"].values()) - rd["score"]) < 1e-9
        finally:
            latest = MDB.reviews.find_one({"_id": orig_copy["_id"]}) if orig_copy else None
            if orig_copy:
                restored = dict(orig_copy)
                restored["version"] = (latest or {}).get("version", 0) + 1
                MDB.reviews.replace_one({"_id": orig_copy["_id"]}, restored, upsert=True)


# --- news/event settings do not reset direction confirmation
class TestSettingsInvariance:
    def test_news_settings_do_not_reset_direction(self, admin):
        g = admin.get(f"{API}/settings").json()
        vals, ver = g["values"], g["version"]
        orig = {"critical_news_window_min": vals["critical_news_window_min"], "event_before_min": vals["event_before_min"]}
        ds_before = MDB.direction_state.find_one()
        ld_before = _state(admin)["live_direction"]
        new_ver = None
        try:
            r = admin.put(f"{API}/settings", json={"version": ver, "values": {"critical_news_window_min": orig["critical_news_window_min"] + 7,
                                                                            "event_before_min": orig["event_before_min"] + 3}})
            assert r.status_code == 200, r.text
            new_ver = r.json()["version"]
            time.sleep(8)
            ds_after = MDB.direction_state.find_one()
            ld_after = _state(admin)["live_direction"]
            print("before", {k: ds_before.get(k) for k in ("last_bar_close", "pending_count", "pause_reason", "confirmed_direction")})
            print("after ", {k: ds_after.get(k) for k in ("last_bar_close", "pending_count", "pause_reason", "confirmed_direction")})
            if ds_before["last_bar_close"] == ds_after["last_bar_close"]:
                assert ds_after["context"] == ds_before["context"]
                assert ds_after["pending_count"] == ds_before["pending_count"]
                assert ds_after["pause_reason"] == ds_before["pause_reason"]
                assert ds_after["confirmed_direction"] == ds_before["confirmed_direction"]
                assert ld_after["direction"] == ld_before["direction"]
                assert ld_after["pending_count"] == ld_before["pending_count"]
            assert "settings changed within this candle" not in (ld_after.get("reason") or "") or \
                "settings changed within this candle" in (ld_before.get("reason") or "")
        finally:
            if new_ver is not None:
                rr = admin.put(f"{API}/settings", json={"version": new_ver, "values": orig})
                assert rr.status_code == 200, rr.text


# --- planner guardrails
class TestPlanner:
    @pytest.mark.parametrize("direction", ["LONG", "SHORT"])
    def test_nine_guardrails(self, user, direction):
        r = user.post(f"{API}/planner", json={"direction": direction, "entry_mode": "spot"})
        assert r.status_code == 200, r.text
        g = r.json().get("guardrails")
        if g is None:
            pytest.skip(f"planner unavailable: {r.json()}")
        names = [x["name"] for x in g]
        assert len(names) == 9, names
        assert not any("review" in n.lower() or "news" in n.lower() for n in names)
        assert "Daily limits/capacity clear" in names
