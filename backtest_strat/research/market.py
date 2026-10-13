"""Синхронные обертки над данными биржи для исследований (работают в скриптах и в Jupyter)."""
from typing import Dict, Iterable

from backtest_strat.fetchers import get_fetcher
from backtest_strat.utils.sync import run_sync


def instrument_specs(exchange: str = "okx", inst_type: str = "SPOT") -> Dict[str, dict]:
    """{symbol: {tick_size, lot_size, min_size, multiplier}}."""
    return run_sync(get_fetcher(exchange).instrument_specs(inst_type))


def spread_snapshot(exchange: str = "okx", inst_type: str = "SPOT") -> Dict[str, float]:
    """{symbol: текущий спред, б.п.} — прокси издержек, не история."""
    return run_sync(get_fetcher(exchange).spread_snapshot(inst_type))
