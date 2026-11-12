"""Federal Reserve holiday calendar used for H.10/H.15 publication deadlines."""
from datetime import date

from models_calc import _next_business, fed_holidays, is_fed_business_day


def test_2026_holidays():
    h = fed_holidays(2026)
    assert date(2026, 11, 11) in h          # Veterans Day (Wednesday)
    assert date(2026, 11, 26) in h          # Thanksgiving
    assert date(2026, 10, 12) in h          # Columbus Day
    assert date(2026, 7, 4) not in h and date(2026, 7, 3) not in h  # Saturday holiday: Fed open Friday


def test_sunday_holiday_moves_to_monday():
    assert date(2027, 7, 5) in fed_holidays(2027)  # 4 July 2027 is a Sunday


def test_next_business_skips_holiday_and_weekend():
    assert _next_business(date(2026, 11, 10)) == date(2026, 11, 12)
    assert _next_business(date(2026, 12, 24)) == date(2026, 12, 28)
    assert not is_fed_business_day(date(2026, 11, 14))
