"""Исполнение позиций: реэкспорт из проекта + оценка полуспреда для исследования."""
import numpy as np

from backtest_strat.research.evaluate import fast_pnl, run_schedule  # noqa: F401

from .config import PARAMS, Params

HOUR_NS = 3600 * 10 ** 9


def half_spread_bps(snapshot_spread_bps: float, tick: float, price: float, p: Params = PARAMS) -> float:
    """
    Полуспред для расчета издержек: половина текущего спреда с биржи, но не меньше половины тика.
    Corwin–Schultz по часовым high/low отвергнут: для BTC давал 9.8 б.п. при реальном спреде ~0.02 б.п. (см. PROBLEMS.md, P5).
    """
    tick_bps = tick / price * 1e4
    return float(np.clip(max(snapshot_spread_bps / 2, tick_bps / 2), p.spread_half_min_bps, p.spread_half_max_bps))


def run_engine(minute_bars, positions, spec_raw, symbol, half_spread_bps, taker_fee_bps, p: Params = PARAMS) -> dict:
    return run_schedule(minute_bars, positions, symbol, spec_raw["tick_size"], spec_raw["lot_size"],
                        half_spread_bps, taker_fee_bps, p.notional)
