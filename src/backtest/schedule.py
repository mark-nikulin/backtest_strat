"""
Исполнение по расписанию целевых позиций: {ts_ns: -1|0|+1} -> рыночные ордера при смене знака.
Стандартная «ручка» для низкочастотных сигналов: исследователь считает позиции векторно,
движок отвечает за исполнение, комиссии, задержки и учет PnL (один и тот же код для всех идей).
"""
from typing import Dict

from src.backtest.strategy import Context, Strategy
from src.backtest.types import BUY, SELL


class ScheduledPosition(Strategy):
    """Держит позицию ±notional/цена согласно расписанию; торгует только при смене знака."""

    def __init__(self, schedule: Dict[int, int], notional: float):
        self.schedule = schedule
        self.notional = notional
        self.sign = 0
        self.qty = 0.0

    def on_book(self, ctx: Context) -> None:
        target = self.schedule.get(ctx.now)
        if target is None or target == self.sign:
            return
        mid = ctx.book.mid()
        if mid is None:
            return
        if self.sign == 0:
            self.qty = ctx.spec.lot_size * int(self.notional / mid / ctx.spec.lot_size)
        delta = (target - self.sign) * self.qty
        if abs(delta) < ctx.spec.lot_size:
            return
        ctx.market(BUY if delta > 0 else SELL, abs(delta), tag=f"to{target}")
        self.sign = target
