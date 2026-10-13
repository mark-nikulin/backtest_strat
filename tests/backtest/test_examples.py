import numpy as np
import pandas as pd
import pytest

from src.backtest import BacktestEngine, InstrumentSpec, bars_to_events
from src.backtest.examples import MidMomentum, TouchMarketMaker

NS = 10 ** 9


def test_touch_market_maker():
    spec = InstrumentSpec(symbol="BTC-USDT", tick_size=0.1, lot_size=0.001)
    strat = TouchMarketMaker(size=0.005, max_position=0.01)

    # 10 минут минутных баров
    dates = pd.date_range("2024-01-01 00:00", periods=10, freq="1min", tz="UTC")
    ts = dates.as_unit("ns").asi8
    events = bars_to_events(ts, np.array([100.0] * 10), 60 * NS, 0.1, 5.0)

    engine = BacktestEngine(strat, spec)
    res = engine.run_batches([events])
    assert res.portfolio is not None


def test_mid_momentum():
    spec = InstrumentSpec(symbol="BTC-USDT", tick_size=0.1, lot_size=0.001)
    # lookback_s = 2 секунды, threshold = 10 bps
    strat = MidMomentum(size=0.01, lookback_s=2, threshold_bps=5.0)

    # Секундные события с растущей ценой
    dates = pd.date_range("2024-01-01 00:00", periods=10, freq="1s", tz="UTC")
    ts = dates.as_unit("ns").asi8
    # Цены: сначала 100, затем 101 (скачок на 100 б.п.)
    px = np.array([100.0, 100.0, 100.0, 101.0, 101.0, 101.0, 101.0, 101.0, 101.0, 101.0])
    events = bars_to_events(ts, px, 1 * NS, 0.1, 2.0)

    engine = BacktestEngine(strat, spec)
    res = engine.run_batches([events])
    assert res.portfolio is not None
    assert len(res.portfolio.fills) >= 1
