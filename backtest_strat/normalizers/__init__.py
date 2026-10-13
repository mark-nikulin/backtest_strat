from backtest_strat.normalizers.base import BaseNormalizer, NormalizeReport, StepResult, StepStatus, sort_events
from backtest_strat.normalizers.okx import OkxNormalizer

# Реестр бирж, ключи совпадают с backtest_strat.fetchers.FETCHERS
NORMALIZERS = {
    "okx": OkxNormalizer,
}


def get_normalizer(exchange: str, **kwargs) -> BaseNormalizer:
    try:
        return NORMALIZERS[exchange.lower()](**kwargs)
    except KeyError:
        raise ValueError(f"Неизвестная биржа {exchange!r}. Доступно: {', '.join(NORMALIZERS)}") from None


__all__ = [
    "BaseNormalizer", "NormalizeReport", "StepResult", "StepStatus", "sort_events",
    "OkxNormalizer", "NORMALIZERS", "get_normalizer",
]
