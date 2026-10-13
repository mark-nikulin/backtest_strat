import numpy as np
import pandas as pd
import pytest

from backtest_strat.research.evaluate import fast_pnl, run_schedule


def test_fast_pnl_turnover_and_costs():
    """Проверяет формулу fast_pnl: разворот = 2 единицы оборота, комиссия за каждое изменение."""
    # pos: 0 -> 1 -> 1 -> -1 -> 0
    # turnover:
    # 0 -> 1: 1
    # 1 -> 1: 0
    # 1 -> -1: 2 (разворот)
    # -1 -> 0: 1
    # total turnover = 4
    idx = pd.date_range("2024-01-01 00:00", periods=5, freq="1h")
    pos = pd.Series([0.0, 1.0, 1.0, -1.0, 0.0], index=idx)
    ret = pd.Series([0.0, 0.01, -0.01, 0.02, 0.0], index=idx)
    notional = 10000.0
    cost_bps = 5.0

    pnl = fast_pnl(ret, pos, cost_bps_per_side=cost_bps, notional=notional)

    # Ручной расчет:
    # t0: pos=0, ret=0, turnover=0 -> 0
    assert pnl.iloc[0] == pytest.approx(0.0)

    # t1: pos=1, simple=np.expm1(0.01), turnover=1 -> notional * (simple - 1 * 5e-4)
    expected_t1 = notional * (np.expm1(0.01) - 1.0 * 5.0 / 1e4)
    assert pnl.iloc[1] == pytest.approx(expected_t1)

    # t2: pos=1, simple=np.expm1(-0.01), turnover=0 -> notional * (simple - 0)
    expected_t2 = notional * np.expm1(-0.01)
    assert pnl.iloc[2] == pytest.approx(expected_t2)

    # t3: pos=-1, simple=np.expm1(0.02), turnover=|-1 - 1| = 2 -> notional * (-simple - 2 * 5e-4)
    expected_t3 = notional * (-np.expm1(0.02) - 2.0 * 5.0 / 1e4)
    assert pnl.iloc[3] == pytest.approx(expected_t3)

    # t4: pos=0, simple=0, turnover=|0 - (-1)| = 1 -> notional * (0 - 1 * 5e-4)
    expected_t4 = notional * (-1.0 * 5.0 / 1e4)
    assert pnl.iloc[4] == pytest.approx(expected_t4)


def test_run_schedule_vs_fast_pnl():
    """run_schedule и fast_pnl расходятся пренебрежимо мало на синтетических минутных барах."""
    # 3 часа минутных баров (00:00..02:59)
    # Бары закрываются на ts + 60s.
    # Решения о позициях принимаются в 01:00 (target +1), 02:00 (target -1), 03:00 (target 0).
    dates = pd.date_range("2024-01-01 00:00", "2024-01-01 02:59", freq="1min", tz="UTC")
    prices = np.full(len(dates), 100.0)
    prices[60:120] = 100.10  # +10 bps за первый активный час
    prices[120:] = 100.05    # -5 bps за второй активный час

    minute_df = pd.DataFrame({"close": prices}, index=dates)

    t1 = pd.Timestamp("2024-01-01 01:00:00", tz="UTC")
    t2 = pd.Timestamp("2024-01-01 02:00:00", tz="UTC")
    t3 = pd.Timestamp("2024-01-01 03:00:00", tz="UTC")

    positions = pd.Series([1, -1, 0], index=[t1, t2, t3])
    notional = 10000.0
    fee_bps = 5.0
    half_spread_bps = 0.0

    ret1 = np.log(100.10 / 100.0)
    ret2 = np.log(100.05 / 100.10)
    ret = pd.Series([ret1, ret2, 0.0], index=[t1, t2, t3])

    res = run_schedule(
        minute=minute_df,
        positions=positions,
        symbol="BTC-USDT",
        tick_size=0.0001,
        lot_size=0.0001,
        half_spread_bps=half_spread_bps,
        taker_fee_bps=fee_bps,
        notional=notional,
        order_latency_ms=0.0,
    )

    pnl_fast_series = fast_pnl(ret, positions, cost_bps_per_side=fee_bps, notional=notional)
    fast_total = float(pnl_fast_series.sum())

    assert res["fills"] == 3
    assert res["notional_traded"] > 0

    # Разница между векторной и движковой оценкой пренебрежимо мала (< 0.1 bps от notional)
    diff = abs(res["net_pnl"] - fast_total)
    assert diff / notional * 10000 < 0.1  # менее 0.1 б.п.
