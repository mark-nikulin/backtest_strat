from backtest_strat.fetchers.base import BackfillReport, BaseExchangeFetcher, DownloadResult, DownloadStatus
from backtest_strat.fetchers.okx import OkxFetcher

# Реестр бирж: CLI и Web UI берут список отсюда. Новая биржа = одна строка.
FETCHERS = {
    "okx": OkxFetcher,
}


def get_fetcher(exchange: str, **kwargs) -> BaseExchangeFetcher:
    try:
        return FETCHERS[exchange.lower()](**kwargs)
    except KeyError:
        raise ValueError(f"Неизвестная биржа {exchange!r}. Доступно: {', '.join(FETCHERS)}") from None


__all__ = [
    "BackfillReport", "BaseExchangeFetcher", "DownloadResult", "DownloadStatus",
    "OkxFetcher", "FETCHERS", "get_fetcher",
]
