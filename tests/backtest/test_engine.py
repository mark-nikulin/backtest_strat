from typing import List
import pyarrow as pa
import pytest

from backtest_strat.backtest.engine import BacktestEngine
from backtest_strat.backtest.strategy import Context, Strategy
from backtest_strat.backtest.types import BUY, SELL, InstrumentSpec
from backtest_strat.normalizers.schema import BOOK_SNAPSHOT, BOOK_UPDATE, EVENT_SCHEMA, TRADE


def make_record_batch(rows: List[dict]) -> pa.RecordBatch:
    n = len(rows)
    ts = [r["ts"] for r in rows]
    et = [r["event_type"] for r in rows]
    side = [r["side"] for r in rows]
    price = [r["price"] for r in rows]
    size = [r["size"] for r in rows]
    msg_id = [r["msg_id"] for r in rows]
    num_orders = [r.get("num_orders") for r in rows]
    level = [r.get("level", 0) for r in rows]

    return pa.RecordBatch.from_arrays(
        [
            pa.array(ts, pa.int64()).cast(EVENT_SCHEMA.field("ts").type),
            pa.array(et, pa.int8()),
            pa.array(side, pa.int8()),
            pa.array(price, pa.float64()),
            pa.array(size, pa.float64()),
            pa.array(num_orders, pa.int32()),
            pa.array(msg_id, pa.int64()),
            pa.array(level, pa.int16()),
        ],
        schema=EVENT_SCHEMA,
    )


class TrackingStrategy(Strategy):
    def __init__(self):
        self.started = False
        self.ended = False
        self.book_calls = 0
        self.trade_calls = 0
        self.timer_calls = 0

    def on_start(self, ctx: Context) -> None:
        self.started = True
        ctx.set_timer(1_000_000_000)  # таймер раз в 1 секунду

    def on_book(self, ctx: Context) -> None:
        self.book_calls += 1

    def on_trade(self, ctx: Context, side: int, price: float, amount: float) -> None:
        self.trade_calls += 1

    def on_timer(self, ctx: Context) -> None:
        self.timer_calls += 1

    def on_end(self, ctx: Context) -> None:
        self.ended = True


def test_engine_message_split_across_batches_and_lifecycle():
    spec = InstrumentSpec(symbol="BTC-USDT", tick_size=0.1, lot_size=0.01)
    strat = TrackingStrategy()
    engine = BacktestEngine(strat, spec, equity_sample_ns=500_000_000)

    # 1. Сделка до валидной книги (должна быть проигнорирована)
    batch_early_trade = make_record_batch([
        {"ts": 100_000_000, "event_type": TRADE, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1}
    ])

    # 2. Сообщение стакана (snapshot msg_id=10, ts=1_000_000_000):
    # Разрезано на 2 батча: в первом батче bid, во втором батче ask!
    batch_part1 = make_record_batch([
        {"ts": 1_000_000_000, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 2.0, "msg_id": 10, "level": 0}
    ])
    batch_part2 = make_record_batch([
        {"ts": 1_000_000_000, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 2.0, "msg_id": 10, "level": 1}
    ])

    # 3. Последующая сделка в 2_500_000_000 (пересекает таймер в 2_000_000_000)
    batch_trade = make_record_batch([
        {"ts": 2_500_000_000, "event_type": TRADE, "side": SELL, "price": 100.0, "size": 0.5, "msg_id": 20}
    ])

    batches = [batch_early_trade, batch_part1, batch_part2, batch_trade]
    result = engine.run_batches(batches)

    # on_start вызван
    assert strat.started is True
    assert strat.ended is True

    # Ранняя сделка проигнорирована
    assert strat.trade_calls == 1

    # Сообщение стакана, разрезанное пополам, применилось как 1 сообщение стакана!
    assert result.book_messages == 1
    assert strat.book_calls == 1
    assert engine.book.best_bid() == 100.0
    assert engine.book.best_ask() == 101.0

    # Таймер сработал между 1.0s и 2.5s (в 2.0s)
    assert strat.timer_calls >= 1

    # Equity curve сэмплировалась
    assert len(engine.portfolio.equity_curve) > 0
    assert result.events == 4
    assert result.trades == 2


def test_engine_starts_only_after_first_snapshot_when_day_starts_with_updates():
    spec = InstrumentSpec(symbol="BTC-USDT", tick_size=0.1, lot_size=0.01)
    strat = TrackingStrategy()
    engine = BacktestEngine(strat, spec)

    # 1. День начинается с BOOK_UPDATE (не snapshot)
    batch_update = make_record_batch([
        {"ts": 1_000_000_000, "event_type": BOOK_UPDATE, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": 1_000_000_000, "event_type": BOOK_UPDATE, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1, "level": 0},
    ])

    # 2. Сделка до первого снапшота
    batch_trade_before_snap = make_record_batch([
        {"ts": 1_500_000_000, "event_type": TRADE, "side": BUY, "price": 100.5, "size": 0.5, "msg_id": 2},
    ])

    # 3. Первый снапшот
    batch_snapshot = make_record_batch([
        {"ts": 2_000_000_000, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 2.0, "msg_id": 3, "level": 0},
        {"ts": 2_000_000_000, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 2.0, "msg_id": 3, "level": 0},
    ])

    # 4. Сделка после снапшота
    batch_trade_after_snap = make_record_batch([
        {"ts": 2_500_000_000, "event_type": TRADE, "side": SELL, "price": 100.5, "size": 0.5, "msg_id": 4},
    ])

    batches = [batch_update, batch_trade_before_snap, batch_snapshot, batch_trade_after_snap]
    result = engine.run_batches(batches)

    # Стратегия стартовала и завершилась
    assert strat.started is True
    assert strat.ended is True

    # Сделка до снапшота была проигнорирована, обработана только сделка после снапшота
    assert strat.trade_calls == 1
    assert result.trades == 2
