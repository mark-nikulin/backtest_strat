import itertools
from dataclasses import dataclass
from typing import Optional

from backtest_strat.normalizers.schema import BUY, SELL

__all__ = ["BUY", "SELL", "InstrumentSpec", "Order", "Fill", "TIFS"]

TIFS = ("GTC", "IOC", "FOK", "PostOnly")
EPS = 1e-12

_order_ids = itertools.count(1)
_fill_ids = itertools.count(1)


def next_order_id() -> int:
    return next(_order_ids)


def next_fill_id() -> int:
    return next(_fill_ids)


@dataclass(frozen=True)
class InstrumentSpec:
    """
    Параметры инструмента и исполнения. Время — в наносекундах, как в EVENT_SCHEMA.

    feed_latency:  задержка доставки рыночных данных до стратегии.
    order_latency: задержка доставки ордера/отмены до биржи.
    У OKX в архивах есть только биржевое время, локального времени получения нет,
    поэтому обе задержки моделируются явно: ордер, принятый стратегией по книге на момент t,
    активируется на бирже в t + feed_latency + order_latency.
    """

    symbol: str
    tick_size: float
    lot_size: float
    contract_multiplier: float = 1.0
    maker_fee: float = 0.0       # доля, 0.0002 = 2 bps
    taker_fee: float = 0.0005
    feed_latency_ns: int = 1_000_000   # 1 ms
    order_latency_ns: int = 2_000_000  # 2 ms

    @property
    def round_trip_ns(self) -> int:
        return self.feed_latency_ns + self.order_latency_ns

    def to_ticks(self, price: float) -> int:
        return int(round(price / self.tick_size))

    def to_price(self, ticks: int) -> float:
        return round(ticks * self.tick_size, 10)


@dataclass
class Order:
    order_id: int
    side: int            # BUY / SELL
    price_ticks: int
    amount: float
    remaining: float
    placed_ts: int       # когда стратегия приняла решение
    active_ts: int       # когда ордер доходит до биржи и начинает участвовать в матчинге
    tif: str = "GTC"
    tag: Optional[str] = None
    expiry_ts: Optional[int] = None
    active: bool = False

    @property
    def filled(self) -> float:
        return self.amount - self.remaining


@dataclass(frozen=True)
class Fill:
    fill_id: int
    order_id: int
    side: int          # сторона нашего ордера
    price: float
    amount: float
    ts: int
    is_maker: bool
    fee: float         # в валюте котировки
    tag: Optional[str] = None
