from unittest.mock import AsyncMock, MagicMock
import pytest

from backtest_strat.research.market import instrument_specs, spread_snapshot


def test_market_instrument_specs(monkeypatch):
    """instrument_specs вызывает fetcher.instrument_specs синхронно."""
    mock_fetcher = MagicMock()
    mock_fetcher.instrument_specs = AsyncMock(return_value={"BTC-USDT": {"tick_size": 0.1}})
    monkeypatch.setattr("backtest_strat.research.market.get_fetcher", lambda ex: mock_fetcher)

    specs = instrument_specs("okx", "SPOT")
    assert specs == {"BTC-USDT": {"tick_size": 0.1}}
    mock_fetcher.instrument_specs.assert_awaited_once_with("SPOT")


def test_market_spread_snapshot(monkeypatch):
    """spread_snapshot вызывает fetcher.spread_snapshot синхронно."""
    mock_fetcher = MagicMock()
    mock_fetcher.spread_snapshot = AsyncMock(return_value={"BTC-USDT": 0.5})
    monkeypatch.setattr("backtest_strat.research.market.get_fetcher", lambda ex: mock_fetcher)

    spreads = spread_snapshot("okx", "SPOT")
    assert spreads == {"BTC-USDT": 0.5}
    mock_fetcher.spread_snapshot.assert_awaited_once_with("SPOT")
