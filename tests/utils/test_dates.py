from datetime import date, datetime, timedelta, timezone
import pytest

from backtest_strat.utils.dates import date_range, parse_date, yesterday_utc


def test_date_range_inclusive():
    start = date(2024, 1, 1)
    end = date(2024, 1, 3)
    res = date_range(start, end)
    assert res == [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]


def test_date_range_single_day():
    d = date(2024, 5, 10)
    assert date_range(d, d) == [d]


def test_date_range_invalid_order_raises():
    start = date(2024, 1, 5)
    end = date(2024, 1, 1)
    with pytest.raises(ValueError, match="раньше start"):
        date_range(start, end)


def test_yesterday_utc():
    expected = datetime.now(timezone.utc).date() - timedelta(days=1)
    assert yesterday_utc() == expected


def test_parse_date():
    assert parse_date("2024-06-15") == date(2024, 6, 15)
