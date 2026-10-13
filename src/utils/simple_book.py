"""Облегченная книга (цена -> объем) для проходов по событиям: валидация, производные датасеты."""
from typing import Dict, Optional

from src.normalizers.schema import BUY


class SimpleBook:
    __slots__ = ("bids", "asks", "bb", "ba")

    def __init__(self) -> None:
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.bb: Optional[float] = None
        self.ba: Optional[float] = None

    def reset(self) -> None:
        self.bids.clear()
        self.asks.clear()
        self.bb = self.ba = None

    def set(self, side: int, price: float, size: float) -> None:
        book = self.bids if side == BUY else self.asks
        if size <= 0:
            book.pop(price, None)
        else:
            book[price] = size
            if side == BUY:
                if self.bb is None or price > self.bb:
                    self.bb = price
            elif self.ba is None or price < self.ba:
                self.ba = price

    def finalize(self) -> None:
        if self.bb not in self.bids:
            self.bb = max(self.bids, default=None)
        if self.ba not in self.asks:
            self.ba = min(self.asks, default=None)

    def valid(self) -> bool:
        return self.bb is not None and self.ba is not None
