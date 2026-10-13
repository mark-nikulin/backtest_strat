import numpy as np
import pandas as pd
import pytest

from src.research.stats import (
    benjamini_hochberg,
    cluster_ols,
    daily_tstat,
    placebo_edge,
    placebo_selectivity,
)


def test_daily_tstat():
    # 10 days, 24 hours per day = 240 hours
    # Each day has mean 10.0 and identical values -> daily sum = 240.0
    idx = pd.date_range("2024-01-01", periods=240, freq="1h")
    # Day i has sum (i + 1) * 24.0
    hourly = pd.Series(
        [float((t.day) * 1.0) for t in idx],
        index=idx,
    )
    t = daily_tstat(hourly)
    # Daily sums: [24, 48, 72, ..., 240] -> positive mean, small variance
    assert not np.isnan(t)
    assert t > 0

    # Constant 0 daily pnl -> std = 0 -> nan
    zero_pnl = pd.Series(0.0, index=idx)
    assert np.isnan(daily_tstat(zero_pnl))


def test_placebo_edge():
    # 20 days of hourly data = 480 hours
    rng = np.random.default_rng(42)
    n = 480
    n_days = n // 24

    # 1. Signal with known strong relationship: signal == sign(ret)
    ret_true = rng.normal(0.001, 0.01, size=n)
    signal_true = np.sign(ret_true)
    contrib_true = signal_true * ret_true  # always >= 0

    res_true = placebo_edge(contrib_true, signal_true, ret_true, rng, draws=100, min_shift=2)
    assert res_true["edge_bps"] > 0
    # Small p-value because true signal beats almost all shifted null signals
    assert res_true["p"] <= 0.05
    assert res_true["n_active"] == n

    # 2. Random pure noise signal uncorrelated with return
    ret_noise = rng.normal(0.0, 0.01, size=n)
    signal_noise = rng.choice([-1.0, 1.0], size=n)
    contrib_noise = signal_noise * ret_noise

    res_noise = placebo_edge(contrib_noise, signal_noise, ret_noise, rng, draws=100, min_shift=2)
    # p-value is not strongly rejecting null
    assert res_noise["p"] > 0.05


def test_placebo_selectivity():
    # 30 days of hourly data = 720 hours
    rng = np.random.default_rng(100)
    n = 720

    signal = np.ones(n)
    ret = rng.normal(0.0, 0.005, size=n)
    mask = np.zeros(n, dtype=bool)

    # Filter selects specific days that have strongly positive returns
    special_days = {2, 5, 8, 11, 14, 17, 20, 23}
    for day in range(n // 24):
        if day in special_days:
            ret[day * 24 : (day + 1) * 24] += 0.02
            mask[day * 24 : (day + 1) * 24] = True

    contrib = signal * ret
    res = placebo_selectivity(contrib, signal, mask, rng, draws=100, min_shift=2)
    # Filter on selects hours with ~+200 bps higher return than filter off
    assert res["selectivity_bps"] > 100.0
    assert res["p"] <= 0.05
    assert res["n_on"] == len(special_days) * 24


def test_cluster_ols_against_direct_formula():
    # Small regression: y = 2 + 3 * x + e, 4 groups
    X = np.array([
        [1.0, 1.0],
        [1.0, 2.0],
        [1.0, 3.0],
        [1.0, 4.0],
        [1.0, 5.0],
        [1.0, 6.0],
    ])
    y = np.array([5.1, 7.9, 11.2, 13.8, 17.1, 20.0])
    groups = np.array([1, 1, 2, 2, 3, 4])

    res = cluster_ols(y, X, groups)

    # Compare beta with standard OLS formula: (X'X)^-1 X'y
    expected_beta = np.linalg.pinv(X.T @ X) @ X.T @ y
    np.testing.assert_allclose(res["beta"], expected_beta)

    # Standard errors must be positive and finite
    assert np.all(res["se"] > 0)
    assert np.all(np.isfinite(res["t"]))
    np.testing.assert_allclose(res["t"], res["beta"] / res["se"])


def test_benjamini_hochberg():
    # Known vector with NaNs and arbitrary order
    p = pd.Series([0.05, 0.001, np.nan, 0.20, 0.02], index=["a", "b", "c", "d", "e"])
    q = benjamini_hochberg(p)

    # NaN remains NaN
    assert np.isnan(q["c"])

    # Valid values
    valid_p = p.dropna()
    valid_q = q.dropna()

    # q-values must be bounded in [0, 1]
    assert (valid_q >= 0.0).all()
    assert (valid_q <= 1.0).all()

    # Monotonicity: if p_i < p_j, then q_i <= q_j
    sorted_idx = valid_p.sort_values().index
    assert valid_q[sorted_idx].is_monotonic_increasing

    # For smallest p=0.001 (rank 1 out of 4), q should be 0.001 * 4 / 1 = 0.004
    assert q["b"] == pytest.approx(0.004)


def test_sharpe_hourly():
    from src.research.stats import sharpe_hourly

    # Less than 24 hours -> nan
    short = pd.Series([10.0] * 10)
    assert np.isnan(sharpe_hourly(short, 10000.0))

    # Zero std -> nan
    const = pd.Series([10.0] * 48)
    assert np.isnan(sharpe_hourly(const, 10000.0))

    # Non-zero variance
    rng = np.random.default_rng(42)
    pnl = pd.Series(rng.normal(5.0, 10.0, size=100))
    sh = sharpe_hourly(pnl, 10000.0)
    assert np.isfinite(sh)
    assert sh > 0


def test_edge_ttest():
    from src.research.stats import edge_ttest

    # < 48 valid observations -> nan
    res_short = edge_ttest(np.ones(20), np.ones(20), np.ones(20, dtype=bool))
    assert np.isnan(res_short["edge_bps"])

    # Valid run: 50 days (1200 hours)
    rng = np.random.default_rng(42)
    n = 1200
    ret = rng.normal(0.001, 0.005, size=n)
    signal = np.sign(ret)
    valid = np.ones(n, dtype=bool)

    res = edge_ttest(signal, ret, valid)
    assert res["edge_bps"] > 0
    assert res["t"] > 0
    assert res["p"] < 0.05
    assert res["n_days"] == 50

    # Also test large J >= 400 branch
    n_large = 450 * 24
    ret_large = rng.normal(0.0, 0.005, size=n_large)
    res_large = edge_ttest(np.ones(n_large), ret_large, np.ones(n_large, dtype=bool))
    assert res_large["n_days"] == 450


def test_interaction_regression():
    from src.research.stats import interaction_regression

    # < 200 rows -> nan
    df_short = pd.DataFrame({
        "ret": [0.001] * 50,
        "seasonal_sig": [1.0] * 50,
        "weather_z": [0.5] * 50,
        "rel_liq": [1.2] * 50,
    })
    res_short = interaction_regression(df_short)
    assert np.isnan(res_short["b_seasonal"])
    assert res_short["n"] == 50

    # >= 200 rows
    idx = pd.date_range("2024-01-01", periods=300, freq="1h")
    rng = np.random.default_rng(42)
    df = pd.DataFrame({
        "ret": rng.normal(0.001, 0.01, size=300),
        "seasonal_sig": rng.choice([-1.0, 1.0], size=300),
        "weather_z": rng.normal(0.0, 1.0, size=300),
        "rel_liq": rng.uniform(0.5, 2.0, size=300),
    }, index=idx)
    res = interaction_regression(df)
    assert np.isfinite(res["b_seasonal"])
    assert np.isfinite(res["t_seasonal"])
    assert res["n"] == 300

