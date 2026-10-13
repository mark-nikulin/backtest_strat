"""Загрузка данных исследования через утилиты проекта: бары (load_derived), параметры инструментов, погода."""
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest_strat.external.weather import load_daily  # noqa: E402
from backtest_strat.research.market import instrument_specs as _specs, spread_snapshot as _spreads  # noqa: E402
from backtest_strat.research.bars import minute_bars, to_hourly as _to_hourly  # noqa: E402

from .config import PARAMS, Params  # noqa: E402


def load_minute_bars(symbol: str, p: Params = PARAMS) -> pd.DataFrame:
    return minute_bars(symbol, p.start, p.end, p.exchange, str(ROOT / p.processed_dir))


def to_hourly(m: pd.DataFrame) -> pd.DataFrame:
    return _to_hourly(m)


def weather(p: Params = PARAMS) -> pd.DataFrame:
    return load_daily(p.start, p.end, lag_days=p.weather_lag_days)


def instrument_specs(symbols: List[str]) -> Dict[str, dict]:
    sp = _specs(PARAMS.exchange)
    return {s: sp[s] for s in symbols if s in sp}


def spread_snapshot(symbols: List[str]) -> Dict[str, float]:
    sp = _spreads(PARAMS.exchange)
    return {s: sp[s] for s in symbols if s in sp}


def coverage(symbols: List[str], p: Params = PARAMS) -> pd.DataFrame:
    """Сводка полноты данных: дней, доля минут со сделками, пропуски — первый шаг любого исследования."""
    rows = []
    total_minutes = ((p.end - p.start).days + 1) * 1440
    for s in symbols:
        m = load_minute_bars(s, p)
        rows.append({
            "symbol": s, "minute_bars": len(m), "active_minutes_share": len(m) / total_minutes,
            "days": m.index.normalize().nunique() if len(m) else 0,
            "first": m.index.min() if len(m) else None, "last": m.index.max() if len(m) else None,
        })
    return pd.DataFrame(rows).set_index("symbol")
