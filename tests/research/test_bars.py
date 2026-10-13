from datetime import date
import pandas as pd
import pytest

from backtest_strat.research.bars import minute_bars, to_hourly


def test_minute_bars_empty(tmp_path, monkeypatch):
    """Когда производных баров нет, minute_bars возвращает пустой DataFrame."""
    res = minute_bars("BTC-USDT", date(2024, 1, 1), date(2024, 1, 1), processed_dir=str(tmp_path))
    assert res.empty


def test_to_hourly_empty():
    """Пустой DataFrame на входе в to_hourly возвращает пустой результат."""
    assert to_hourly(pd.DataFrame()).empty


def test_to_hourly_aggregation():
    """to_hourly агрегирует минутные бары в часовые OHLCV, VWAP, notional, trades."""
    # 2 часа: в первом 3 минуты, во втором 1 минута
    idx = pd.to_datetime([
        "2024-01-01 00:00:00",
        "2024-01-01 00:01:00",
        "2024-01-01 00:02:00",
        "2024-01-01 01:30:00",
    ])
    df = pd.DataFrame({
        "open": [100.0, 101.0, 102.0, 105.0],
        "high": [102.0, 103.0, 104.0, 106.0],
        "low": [99.0, 100.0, 101.0, 104.0],
        "close": [101.0, 102.0, 103.0, 105.5],
        "volume": [10.0, 20.0, 30.0, 40.0],
        "vwap": [100.5, 101.5, 102.5, 105.0],
        "trades": [5, 10, 15, 20],
    }, index=idx)

    h = to_hourly(df)
    assert len(h) == 2  # 00:00 и 01:00

    h0 = h.iloc[0]
    assert h0["open"] == 100.0
    assert h0["high"] == 104.0
    assert h0["low"] == 99.0
    assert h0["close"] == 103.0
    assert h0["volume"] == 60.0
    assert h0["trades"] == 30
    assert h0["minutes_active"] == 3
    # notional = 10*100.5 + 20*101.5 + 30*102.5 = 1005 + 2030 + 3075 = 6110
    assert h0["notional"] == pytest.approx(6110.0)

    h1 = h.iloc[1]
    assert h1["open"] == 105.0
    assert h1["close"] == 105.5
    assert h1["minutes_active"] == 1
