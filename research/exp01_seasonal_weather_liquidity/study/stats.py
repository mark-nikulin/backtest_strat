"""Статистика перенесена в проект (backtest_strat/research/stats.py) — здесь реэкспорт для обратной совместимости."""
from backtest_strat.research.stats import *  # noqa: F401,F403
from backtest_strat.research.stats import (  # noqa: F401
    benjamini_hochberg, cluster_ols, daily_tstat, interaction_regression, placebo_edge, placebo_selectivity, sharpe_hourly,
)
