"""Offline by default. Tests that call a running server need REACT_APP_BACKEND_URL (e.g. http://localhost:8001)."""
import os

_LIVE = ["test_btc_desk_api.py", "test_iter2_alerts_workbook.py", "test_iter4_news_independence_api.py"]
collect_ignore = ([] if os.environ.get("REACT_APP_BACKEND_URL") else _LIVE)
# Live tests sign in with TEST_ADMIN_EMAIL / TEST_ADMIN_PASSWORD and TEST_VIEWER_EMAIL / TEST_VIEWER_PASSWORD.
