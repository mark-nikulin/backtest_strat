from dataclasses import dataclass, field
from typing import List, Tuple

from backtest_strat.backtest.types import BUY, EPS, Fill


@dataclass
class Portfolio:
    """
    Позиция, деньги и PnL для линейного контракта (spot / USDT-margined perp).
    Учет средней цены входа: realized фиксируется при сокращении/развороте позиции.
    Один общий учет вместо подсчета PnL внутри каждой стратегии.
    """

    multiplier: float = 1.0
    position: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0
    fees: float = 0.0
    volume: float = 0.0           # в контрактах
    notional: float = 0.0         # оборот в валюте котировки
    maker_fills: int = 0
    taker_fills: int = 0
    fills: List[Fill] = field(default_factory=list)
    equity_curve: List[Tuple[int, float]] = field(default_factory=list)  # (ts, equity)

    def apply(self, fill: Fill) -> None:
        signed = fill.amount if fill.side == BUY else -fill.amount
        pos = self.position
        if pos == 0 or (pos > 0) == (signed > 0):  # открытие / наращивание
            new_pos = pos + signed
            self.avg_price = (self.avg_price * abs(pos) + fill.price * abs(signed)) / abs(new_pos)
            self.position = new_pos
        else:  # сокращение / разворот
            closed = min(abs(signed), abs(pos))
            direction = 1.0 if pos > 0 else -1.0
            self.realized_pnl += (fill.price - self.avg_price) * closed * direction * self.multiplier
            new_pos = pos + signed
            if abs(new_pos) <= EPS:
                self.position, self.avg_price = 0.0, 0.0
            elif (new_pos > 0) != (pos > 0):  # разворот: остаток открыт по цене сделки
                self.position, self.avg_price = new_pos, fill.price
            else:
                self.position = new_pos

        self.fees += fill.fee
        self.volume += fill.amount
        self.notional += fill.amount * fill.price * self.multiplier
        if fill.is_maker:
            self.maker_fills += 1
        else:
            self.taker_fills += 1
        self.fills.append(fill)

    def unrealized_pnl(self, mark: float) -> float:
        return (mark - self.avg_price) * self.position * self.multiplier if self.position else 0.0

    def equity(self, mark: float) -> float:
        """PnL с начала теста: realized + unrealized - комиссии."""
        return self.realized_pnl + self.unrealized_pnl(mark) - self.fees
