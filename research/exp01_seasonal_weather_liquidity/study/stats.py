"""Статистика перенесена в проект (src/research/stats.py) — здесь реэкспорт для обратной совместимости."""
from src.research.stats import *  # noqa: F401,F403
from src.research.stats import (  # noqa: F401
    benjamini_hochberg, cluster_ols, daily_tstat, interaction_regression, placebo_edge, placebo_selectivity, sharpe_hourly,
)
