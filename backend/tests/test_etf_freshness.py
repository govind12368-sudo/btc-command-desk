import os
from datetime import date

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_offline")

from models_calc import us_trading_days_since, ETF_MAX_TRADING_DAYS_OLD  # noqa: E402


def test_weekend_plus_provider_lag_stays_valid():
    # Thu 29 Oct 2026 data checked on Tue 3 Nov (Fri, Mon, Tue = 3 trading days)
    assert us_trading_days_since(date(2026, 10, 29), date(2026, 11, 3)) == 3
    assert us_trading_days_since(date(2026, 10, 29), date(2026, 11, 3)) <= ETF_MAX_TRADING_DAYS_OLD


def test_friday_data_on_monday_is_one_day():
    assert us_trading_days_since(date(2026, 10, 30), date(2026, 11, 2)) == 1
    assert us_trading_days_since(date(2026, 10, 30), date(2026, 10, 31)) == 0  # Saturday


def test_week_old_data_rejected():
    assert us_trading_days_since(date(2026, 10, 26), date(2026, 11, 3)) > ETF_MAX_TRADING_DAYS_OLD
