"""
Интерфейс стратегии. Одна библиотека на все альфы: стратегия реализует только
колбэки и общается с рынком через Context.

    class MyAlpha(Strategy):
        def on_start(self, ctx):
            ctx.set_timer(1_000_000_000)          # раз в секунду
        def on_timer(self, ctx):
            if ctx.position == 0:
                ctx.market(BUY, 0.01)
"""
from typing import TYPE_CHECKING, List, Optional

from src.backtest.types import BUY, SELL, TIFS, Fill, Order, next_order_id

if TYPE_CHECKING:
    from src.backtest.engine import BacktestEngine


class Context:
    """Все, что стратегия видит и может сделать. Время — наносекунды биржевого времени."""

    def __init__(self, engine: "BacktestEngine"):
        self._e = engine

    # --- рынок ---
    @property
    def now(self) -> int:
        return self._e.now

    @property
    def book(self):
        return self._e.book

    @property
    def spec(self):
        return self._e.spec

    # --- счет ---
    @property
    def position(self) -> float:
        return self._e.portfolio.position

    @property
    def portfolio(self):
        return self._e.portfolio

    def equity(self) -> float:
        mid = self._e.book.mid()
        return self._e.portfolio.equity(mid) if mid is not None else self._e.portfolio.realized_pnl - self._e.portfolio.fees

    def open_orders(self) -> List[Order]:
        return self._e.book.open_orders()

    # --- действия (все доходят до биржи с задержкой) ---
    def limit(self, side: int, price: float, amount: float, tif: str = "GTC",
              tag: Optional[str] = None, ttl_ns: Optional[int] = None) -> int:
        if side not in (BUY, SELL):
            raise ValueError("side должен быть BUY или SELL")
        if tif not in TIFS:
            raise ValueError(f"tif: {TIFS}")
        amount = self._round_lot(amount)
        if amount <= 0:
            raise ValueError("amount меньше лота")
        now = self._e.now
        active = now + self._e.spec.round_trip_ns
        order = Order(next_order_id(), side, self._e.spec.to_ticks(price), amount, amount,
                      placed_ts=now, active_ts=active, tif=tif, tag=tag,
                      expiry_ts=active + ttl_ns if ttl_ns else None)
        self._e.book.submit(order, now, self._e._on_fill)
        self._e.orders_sent += 1
        return order.order_id

    def market(self, side: int, amount: float, tag: Optional[str] = None) -> int:
        """IOC по худшей допустимой цене: проходит по стакану на момент прихода на биржу."""
        far = 10 ** 12 if side == BUY else 1
        return self.limit(side, self._e.spec.to_price(far), amount, tif="IOC", tag=tag)

    def cancel(self, order_id: int) -> None:
        self._e.book.request_cancel(order_id, self._e.now + self._e.spec.round_trip_ns)

    def cancel_all(self) -> None:
        for o in self._e.book.open_orders():
            self.cancel(o.order_id)

    def set_timer(self, interval_ns: int) -> None:
        self._e.set_timer(interval_ns)

    def _round_lot(self, amount: float) -> float:
        lot = self._e.spec.lot_size
        return round(int(amount / lot + 1e-9) * lot, 12)


class Strategy:
    """Базовый класс. Переопределяйте только нужные колбэки."""

    def on_start(self, ctx: Context) -> None: ...
    def on_book(self, ctx: Context) -> None: ...
    def on_trade(self, ctx: Context, side: int, price: float, amount: float) -> None: ...
    def on_fill(self, ctx: Context, fill: Fill) -> None: ...
    def on_timer(self, ctx: Context) -> None: ...
    def on_end(self, ctx: Context) -> None: ...
