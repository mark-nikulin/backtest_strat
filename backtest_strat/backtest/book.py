"""
Восстановленный L2-стакан + слой исполнения наших ордеров.

Ядро перенесено из hft_sim (simulator/replay.py: ReplayBook) и адаптировано
под EVENT_SCHEMA. Принципы:
  * Записанный рынок неизменяем: наши ордера живут в отдельном слое и не меняют
    восстановленную книгу и ленту сделок.
  * Агрессивный ордер проходит по уровням записанной книги.
  * Пассивный ордер встает в конец очереди своего уровня и исполняется только
    после того, как записанные сделки съедят объем перед ним (queue-ahead).

Отличия от исходника:
  * Съеденная нами ликвидность вычитается из уровня до следующего апдейта этого
    уровня — повторные агрессивные ордера не могут дважды забрать один объем.
  * queue-ahead ограничен сверху текущим объемом уровня (отмены перед нами
    сокращают очередь); удаление уровня обнуляет очередь.
  * Сделка по цене хуже нашей (trade-through) исполняет нас без учета очереди.
  * Если книга прошла сквозь наш пассивный ордер без сделки, он исполняется как maker.
  * Отмена тоже идет с задержкой order_latency (в исходнике — мгновенно).
"""
from typing import Callable, Dict, List, Optional, Set, Tuple

from backtest_strat.backtest.types import BUY, EPS, SELL, Fill, InstrumentSpec, Order, next_fill_id

FillCallback = Callable[[Fill], None]


class ReplayBook:
    def __init__(self, spec: InstrumentSpec):
        self.spec = spec
        self.exchange_ts: Optional[int] = None
        self._bids: Dict[int, float] = {}
        self._asks: Dict[int, float] = {}
        self._best_bid: Optional[int] = None
        self._best_ask: Optional[int] = None
        # слой исполнения
        self._orders: Dict[int, Order] = {}
        self._pending: List[Order] = []
        self._pending_cancels: List[Tuple[int, int]] = []  # (effective_ts, order_id)
        self._queue_ahead: Dict[int, float] = {}
        self._taken: Dict[int, Dict[int, float]] = {BUY: {}, SELL: {}}  # side книги -> ticks -> съедено нами
        self._order_levels: Dict[int, Dict[int, Set[int]]] = {BUY: {}, SELL: {}}  # side -> ticks -> order_ids

    # ------------------------------------------------------------------ рынок

    def _side_book(self, side: int) -> Dict[int, float]:
        return self._bids if side == BUY else self._asks

    def apply_message(self, is_snapshot: bool, sides: List[int], prices: List[float],
                      sizes: List[float], ts: int, on_fill: FillCallback) -> None:
        """Применяет одно сообщение стакана (все его уровни) атомарно."""
        if is_snapshot:
            self._bids.clear()
            self._asks.clear()
            self._best_bid = self._best_ask = None
            self._taken[BUY].clear()
            self._taken[SELL].clear()

        to_ticks = self.spec.to_ticks
        for side, price, qty in zip(sides, prices, sizes):
            ticks = to_ticks(price)
            book = self._bids if side == BUY else self._asks
            self._taken[side].pop(ticks, None)  # новый записанный объем уже отражает реальность
            if qty <= 0:
                book.pop(ticks, None)
            else:
                book[ticks] = qty
                if side == BUY:
                    if self._best_bid is None or ticks > self._best_bid:
                        self._best_bid = ticks
                elif self._best_ask is None or ticks < self._best_ask:
                    self._best_ask = ticks
            # Наши ордера на этом уровне: очередь не может быть длиннее самого уровня
            ids = self._order_levels[side].get(ticks)
            if ids:
                cap = max(qty, 0.0)
                for oid in ids:
                    if self._queue_ahead.get(oid, 0.0) > cap:
                        self._queue_ahead[oid] = cap

        # Пересчет лучших цен только если кэш стал невалиден (как в исходнике)
        if self._best_bid not in self._bids:
            self._best_bid = max(self._bids, default=None)
        if self._best_ask not in self._asks:
            self._best_ask = min(self._asks, default=None)
        self.exchange_ts = ts

        if self._orders:
            self._fill_crossed_resting(ts, on_fill)

    def best_bid_ticks(self) -> Optional[int]:
        return self._best_bid

    def best_ask_ticks(self) -> Optional[int]:
        return self._best_ask

    def best_bid(self) -> Optional[float]:
        return self.spec.to_price(self._best_bid) if self._best_bid is not None else None

    def best_ask(self) -> Optional[float]:
        return self.spec.to_price(self._best_ask) if self._best_ask is not None else None

    def mid(self) -> Optional[float]:
        b, a = self.best_bid(), self.best_ask()
        return None if b is None or a is None else (b + a) / 2.0

    def spread(self) -> Optional[float]:
        b, a = self.best_bid(), self.best_ask()
        return None if b is None or a is None else a - b

    def depth(self, levels: int = 10) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
        to_price = self.spec.to_price
        bids = [(to_price(p), self._bids[p]) for p in sorted(self._bids, reverse=True)[:levels]]
        asks = [(to_price(p), self._asks[p]) for p in sorted(self._asks)[:levels]]
        return bids, asks

    def volume_at(self, side: int, price: float) -> float:
        return self._side_book(side).get(self.spec.to_ticks(price), 0.0)

    def is_valid(self) -> bool:
        return self._best_bid is not None and self._best_ask is not None

    # ------------------------------------------------------------ исполнение

    def submit(self, order: Order, now: int, on_fill: FillCallback) -> None:
        if order.active_ts > now:
            self._pending.append(order)
        else:
            self._activate(order, now, on_fill)

    def request_cancel(self, order_id: int, effective_ts: int) -> None:
        self._pending_cancels.append((effective_ts, order_id))

    def process_due(self, now: int, on_fill: FillCallback) -> List[int]:
        """Истечение, отмены и активация ордеров, чье время наступило. Возвращает отмененные id."""
        cancelled: List[int] = []
        if self._pending_cancels:
            due = [oid for ts, oid in self._pending_cancels if ts <= now]
            if due:
                self._pending_cancels = [(ts, oid) for ts, oid in self._pending_cancels if ts > now]
                cancelled += [oid for oid in due if self._cancel_now(oid)]
        if self._orders or self._pending:
            expired = [o.order_id for o in list(self._orders.values()) + self._pending
                       if o.expiry_ts is not None and o.expiry_ts <= now]
            cancelled += [oid for oid in expired if self._cancel_now(oid)]
        if self._pending:
            due_orders = [o for o in self._pending if o.active_ts <= now]
            if due_orders:
                self._pending = [o for o in self._pending if o.active_ts > now]
                for order in sorted(due_orders, key=lambda o: o.active_ts):
                    self._activate(order, now, on_fill)
        return cancelled

    def _crosses(self, order: Order) -> bool:
        if order.side == BUY:
            return self._best_ask is not None and order.price_ticks >= self._best_ask
        return self._best_bid is not None and order.price_ticks <= self._best_bid

    def _eligible_levels(self, side: int, price_ticks: int) -> List[int]:
        if side == BUY:
            return sorted(p for p in self._asks if p <= price_ticks)
        return sorted((p for p in self._bids if p >= price_ticks), reverse=True)

    def _free(self, book_side: int, ticks: int) -> float:
        return self._side_book(book_side).get(ticks, 0.0) - self._taken[book_side].get(ticks, 0.0)

    def _available(self, order: Order) -> float:
        book_side = SELL if order.side == BUY else BUY
        return sum(max(self._free(book_side, p), 0.0) for p in self._eligible_levels(order.side, order.price_ticks))

    def _activate(self, order: Order, now: int, on_fill: FillCallback) -> None:
        crosses = self._crosses(order)
        if order.tif == "PostOnly" and crosses:
            return  # отклонен биржей
        if crosses:
            if order.tif == "FOK" and self._available(order) + EPS < order.remaining:
                return
            self._take(order, now, on_fill)
        if order.remaining <= EPS or order.tif in ("IOC", "FOK"):
            return
        order.active = True
        self._orders[order.order_id] = order
        self._queue_ahead[order.order_id] = self._side_book(order.side).get(order.price_ticks, 0.0)
        self._order_levels[order.side].setdefault(order.price_ticks, set()).add(order.order_id)

    def _take(self, order: Order, now: int, on_fill: FillCallback) -> None:
        book_side = SELL if order.side == BUY else BUY
        taken = self._taken[book_side]
        for ticks in self._eligible_levels(order.side, order.price_ticks):
            if order.remaining <= EPS:
                break
            amount = min(order.remaining, self._free(book_side, ticks))
            if amount <= EPS:
                continue
            order.remaining -= amount
            taken[ticks] = taken.get(ticks, 0.0) + amount
            self._emit(order, ticks, amount, now, is_maker=False, on_fill=on_fill)

    def on_trade(self, aggressor_side: int, price: float, amount: float, now: int, on_fill: FillCallback) -> None:
        """Записанная сделка: может исполнить наши пассивные ордера на противоположной стороне."""
        if not self._orders:
            return
        trade_ticks = self.spec.to_ticks(price)
        maker_side = SELL if aggressor_side == BUY else BUY
        candidates = [o for o in self._orders.values() if o.side == maker_side]
        # Лучшая цена первой, при равной — кто раньше встал
        candidates.sort(key=lambda o: (o.price_ticks if maker_side == SELL else -o.price_ticks, o.placed_ts))
        available = float(amount)
        for order in candidates:
            if available <= EPS:
                break
            if maker_side == SELL:
                reached, through = order.price_ticks <= trade_ticks, order.price_ticks < trade_ticks
            else:
                reached, through = order.price_ticks >= trade_ticks, order.price_ticks > trade_ticks
            if not reached:
                continue
            if not through:
                ahead = self._queue_ahead.get(order.order_id, 0.0)
                consumed = min(ahead, available)
                ahead -= consumed
                available -= consumed
                self._queue_ahead[order.order_id] = ahead
                if ahead > EPS or available <= EPS:
                    continue
            traded = min(order.remaining, available)
            order.remaining -= traded
            available -= traded
            self._emit(order, order.price_ticks, traded, now, is_maker=True, on_fill=on_fill)
            if order.remaining <= EPS:
                self._remove(order.order_id)

    def _fill_crossed_resting(self, now: int, on_fill: FillCallback) -> None:
        """Книга прошла сквозь наш пассивный ордер: встречная сторона торгует по нашей цене."""
        for order in [o for o in self._orders.values() if self._crosses(o)]:
            amount = min(order.remaining, self._available(order))
            if amount <= EPS:
                continue
            order.remaining -= amount
            self._emit(order, order.price_ticks, amount, now, is_maker=True, on_fill=on_fill)
            if order.remaining <= EPS:
                self._remove(order.order_id)

    def _emit(self, order: Order, ticks: int, amount: float, now: int, is_maker: bool, on_fill: FillCallback) -> None:
        price = self.spec.to_price(ticks)
        rate = self.spec.maker_fee if is_maker else self.spec.taker_fee
        fee = price * amount * self.spec.contract_multiplier * rate
        on_fill(Fill(next_fill_id(), order.order_id, order.side, price, round(amount, 12), now, is_maker, fee, order.tag))

    def _remove(self, order_id: int) -> None:
        order = self._orders.pop(order_id, None)
        self._queue_ahead.pop(order_id, None)
        if order is not None:
            ids = self._order_levels[order.side].get(order.price_ticks)
            if ids:
                ids.discard(order_id)
                if not ids:
                    del self._order_levels[order.side][order.price_ticks]

    def _cancel_now(self, order_id: int) -> bool:
        if order_id in self._orders:
            self._remove(order_id)
            return True
        for i, order in enumerate(self._pending):
            if order.order_id == order_id:
                self._pending.pop(i)
                return True
        return False

    # ---------------------------------------------------------- для стратегии

    def open_orders(self) -> List[Order]:
        return list(self._orders.values()) + list(self._pending)

    def queue_ahead(self, order_id: int) -> Optional[float]:
        return self._queue_ahead.get(order_id)
