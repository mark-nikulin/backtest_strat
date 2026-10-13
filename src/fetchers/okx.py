from datetime import date
from typing import Dict, List

import aiohttp

from src.fetchers.base import BaseExchangeFetcher
from src.utils.http import USER_AGENT
from src.utils.retry import RETRYABLE_STATUSES, RetryableError, retry_async

CDN = "https://static.okx.com/cdn"
REST = "https://www.okx.com/api/v5"


class OkxFetcher(BaseExchangeFetcher):
    """
    Публичные daily-архивы OKX (без API-ключей). Подробности: docs/okx_historical_data.md

    trades     — .zip с CSV
    orderbook  — L2 400 уровней, .tar.gz с NDJSON. У OKX два пути CDN (pro и classic),
                 пробуем оба: какой актуален для даты, заранее неизвестно.
    """

    supported_data_types = ("trades", "orderbook")
    inst_types = ("SPOT", "SWAP", "FUTURES")

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

    async def _live_instruments(self, inst_type: str) -> List[dict]:
        if inst_type not in self.inst_types:
            raise ValueError(f"inst_type: допустимо {self.inst_types}")

        async def _fetch() -> List[dict]:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get(f"{REST}/public/instruments", params={"instType": inst_type},
                                 headers={"User-Agent": USER_AGENT}) as resp:
                    if resp.status in RETRYABLE_STATUSES:
                        raise RetryableError(f"HTTP {resp.status} instruments")
                    resp.raise_for_status()
                    payload = await resp.json()
            return [i for i in payload.get("data", []) if i.get("state") == "live"]

        return await retry_async(_fetch, max_attempts=3, description="OKX instruments")

    async def list_symbols(self, inst_type: str = "SPOT") -> List[str]:
        return sorted(i["instId"] for i in await self._live_instruments(inst_type))

    async def instrument_specs(self, inst_type: str = "SPOT") -> Dict[str, dict]:
        out = {}
        for i in await self._live_instruments(inst_type):
            out[i["instId"]] = {
                "tick_size": float(i["tickSz"]),
                "lot_size": float(i["lotSz"]),
                "min_size": float(i.get("minSz") or i["lotSz"]),
                "multiplier": float(i.get("ctVal") or 1.0) * float(i.get("ctMult") or 1.0),
            }
        return out

    async def spread_snapshot(self, inst_type: str = "SPOT") -> Dict[str, float]:
        """
        Текущий спред (б.п.) по всем инструментам одним запросом /market/tickers.
        Это МГНОВЕННЫЙ снимок: для оценки издержек в прошлом годится лишь как прокси относительной ликвидности
        активов; исторический спред из потока L2 (датасет l1) точнее, но требует стакана.
        """
        if inst_type not in self.inst_types:
            raise ValueError(f"inst_type: допустимо {self.inst_types}")

        async def _fetch() -> Dict[str, float]:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get(f"{REST}/market/tickers", params={"instType": inst_type},
                                 headers={"User-Agent": USER_AGENT}) as resp:
                    if resp.status in RETRYABLE_STATUSES:
                        raise RetryableError(f"HTTP {resp.status} tickers")
                    resp.raise_for_status()
                    payload = await resp.json()
            out = {}
            for t in payload.get("data", []):
                try:
                    bid, ask = float(t["bidPx"]), float(t["askPx"])
                except (KeyError, ValueError):
                    continue
                if bid > 0 and ask >= bid:
                    out[t["instId"]] = (ask - bid) / ((ask + bid) / 2) * 1e4
            return out

        return await retry_async(_fetch, max_attempts=3, description="OKX tickers")
