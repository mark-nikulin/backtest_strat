"""
Стратегии-примеры: показывают API, а не альфу. Ожидать от них прибыли не стоит.
"""
from collections import deque
from typing import Deque, Optional, Tuple

from src.backtest.strategy import Context, Strategy
from src.backtest.types import BUY, SELL, Fill

NS = 1_000_000_000


class TouchMarketMaker(Strategy):
    """Котирует по лучшим ценам с обеих сторон, переставляется при смене лучшей цены, держит лимит позиции."""

    def __init__(self, size: float = 0.01, max_position: float = 0.05):
        self.size = size
        self.max_position = max_position
        self.bid_id: Optional[int] = None
        self.ask_id: Optional[int] = None
        self.quoted: Tuple[Optional[int], Optional[int]] = (None, None)

    def on_book(self, ctx: Context) -> None:
        touch = (ctx.book.best_bid_ticks(), ctx.book.best_ask_ticks())
        if touch == self.quoted:
            return
        self.quoted = touch
        for oid in (self.bid_id, self.ask_id):
            if oid is not None:
                ctx.cancel(oid)
        self.bid_id = self.ask_id = None
        if ctx.position < self.max_position:
            self.bid_id = ctx.limit(BUY, ctx.book.best_bid(), self.size, tif="PostOnly", tag="bid")
        if ctx.position > -self.max_position:
            self.ask_id = ctx.limit(SELL, ctx.book.best_ask(), self.size, tif="PostOnly", tag="ask")

    def on_end(self, ctx: Context) -> None:
        ctx.cancel_all()


class MidMomentum(Strategy):
    """Раз в секунду: если mid за lookback вырос/упал больше порога — встает в позицию рыночным ордером."""

    def __init__(self, size: float = 0.01, lookback_s: int = 60, threshold_bps: float = 10.0):
        self.size = size
        self.lookback_s = lookback_s
        self.threshold = threshold_bps / 1e4
        self.mids: Deque[float] = deque(maxlen=lookback_s + 1)
        self.target = 0.0
        self.in_flight = False

    def on_start(self, ctx: Context) -> None:
        ctx.set_timer(NS)

    def on_timer(self, ctx: Context) -> None:
        mid = ctx.book.mid()
        if mid is None:
            return
        self.mids.append(mid)
        if len(self.mids) <= self.lookback_s or self.in_flight:
            return
        ret = mid / self.mids[0] - 1
        self.target = self.size if ret > self.threshold else -self.size if ret < -self.threshold else self.target
        diff = round(self.target - ctx.position, 10)
        if abs(diff) >= ctx.spec.lot_size:
            ctx.market(BUY if diff > 0 else SELL, abs(diff))
            self.in_flight = True

    def on_fill(self, ctx: Context, fill: Fill) -> None:
        self.in_flight = False

    def on_trade(self, ctx: Context, side: int, price: float, amount: float) -> None:
        # IOC, не нашедший ликвидности, не дает fill — снимаем флаг по времени
        if self.in_flight and not ctx.open_orders():
            self.in_flight = False
