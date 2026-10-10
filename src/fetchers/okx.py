from datetime import date
from typing import List

from src.fetchers.base import BaseExchangeFetcher

CDN = "https://static.okx.com/cdn"


class OkxFetcher(BaseExchangeFetcher):
    """
    Публичные daily-архивы OKX (без API-ключей). Подробности: docs/okx_historical_data.md

    trades     — .zip с CSV
    orderbook  — L2 400 уровней, .tar.gz с NDJSON. У OKX два пути CDN (pro и classic),
                 пробуем оба: какой актуален для даты, заранее неизвестно.
    """

    supported_data_types = ("trades", "orderbook")

    def __init__(self, base_dir: str = "data/raw", **kwargs):
        super().__init__("okx", base_dir=base_dir, **kwargs)

    def archive_filename(self, symbol: str, target_date: date, data_type: str) -> str:
        d = target_date.isoformat()
        if data_type == "trades":
            return f"{symbol}-trades-{d}.zip"
        return f"{symbol}-L2orderbook-400lv-{d}.tar.gz"

    def candidate_urls(self, symbol: str, target_date: date, data_type: str) -> List[str]:
        compact = target_date.strftime("%Y%m%d")
        filename = self.archive_filename(symbol, target_date, data_type)
        if data_type == "trades":
            return [f"{CDN}/okex/traderecords/trades/daily/{compact}/{filename}"]
        return [
            f"{CDN}/okx/match/orderbook/pro/L2/400lv/daily/{compact}/{filename}",
            f"{CDN}/okx/match/orderbook/L2/400lv/daily/{compact}/{filename}",
        ]
