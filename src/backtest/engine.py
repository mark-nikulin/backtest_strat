"""
Синхронный event-driven движок: один процесс, один цикл, без потоков и сети.
Читает поток событий (src.storage.reader.iter_events) в порядке SORT_KEYS.

Порядок обработки события с временем t:
  1. таймеры стратегии, наступившие до t;
  2. отмены / истечения / активация наших ордеров с active_ts <= t
     (матчатся против книги, какой она была непосредственно до t);
  3. само событие: сообщение стакана применяется целиком (все уровни одного msg_id),
     сделка — сначала исполняет наши пассивные ордера, потом передается стратегии;
  4. колбэк стратегии (on_book / on_trade).
"""
import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Iterator, List, Optional

import pyarrow as pa

from src.backtest.book import ReplayBook
from src.backtest.portfolio import Portfolio
from src.backtest.strategy import Context, Strategy
from src.backtest.types import Fill, InstrumentSpec
from src.normalizers.schema import BOOK_SNAPSHOT, TRADE
from src.storage.reader import iter_events

logger = logging.getLogger(__name__)

COLUMNS = ["ts", "event_type", "side", "price", "size", "msg_id"]
NS_PER_S = 1_000_000_000


@dataclass
class BacktestResult:
    spec: InstrumentSpec
    portfolio: Portfolio
    start_ts: int
    end_ts: int
    events: int
    book_messages: int
    trades: int
    orders_sent: int
    wall_seconds: float
    final_mid: Optional[float]


class BacktestEngine:
    def __init__(self, strategy: Strategy, spec: InstrumentSpec, equity_sample_ns: int = 60 * NS_PER_S):
        self.strategy = strategy
        self.spec = spec
        self.book = ReplayBook(spec)
        self.portfolio = Portfolio(multiplier=spec.contract_multiplier)
        self.ctx = Context(self)
        self.now: int = 0
        self.orders_sent = 0
        self._timer_interval: Optional[int] = None
        self._next_timer: Optional[int] = None
        self._equity_sample_ns = equity_sample_ns
        self._next_sample: Optional[int] = None
        self._started = False
        self._seen_snapshot = False  # архив дня может начинаться с апдейтов: до снапшота книга неполна

    # --- вызывается из Context / книги ---

    def set_timer(self, interval_ns: int) -> None:
        if interval_ns <= 0:
            raise ValueError("interval_ns > 0")
        self._timer_interval = interval_ns
        self._next_timer = (self.now // interval_ns + 1) * interval_ns

    def _on_fill(self, fill: Fill) -> None:
        self.portfolio.apply(fill)
        self.strategy.on_fill(self.ctx, fill)

    # --- цикл ---

    def _advance_to(self, ts: int) -> None:
        if self._next_timer is not None:
            while self._next_timer is not None and self._next_timer <= ts:
                self.now = self._next_timer
                self.book.process_due(self.now, self._on_fill)
                if self.book.is_valid():
                    self.strategy.on_timer(self.ctx)
                self._next_timer = self._next_timer + self._timer_interval if self._timer_interval else None
        self.now = ts
        self.book.process_due(ts, self._on_fill)

    def _sample_equity(self, ts: int) -> None:
        if self._next_sample is None:
            self._next_sample = (ts // self._equity_sample_ns + 1) * self._equity_sample_ns
        while ts >= self._next_sample:
            mid = self.book.mid()
            if mid is not None:
                self.portfolio.equity_curve.append((self._next_sample, self.portfolio.equity(mid)))
            self._next_sample += self._equity_sample_ns

    def run_batches(self, batches: Iterable[pa.RecordBatch]) -> BacktestResult:
        wall = time.perf_counter()
        events = book_msgs = trades = 0
        start_ts: Optional[int] = None
        book, strategy, ctx = self.book, self.strategy, self.ctx

        # Буфер текущего сообщения стакана: его уровни могут лежать в двух соседних батчах
        msg_id = None
        msg_ts = 0
        msg_snapshot = False
        m_sides: List[int] = []
        m_prices: List[float] = []
        m_sizes: List[float] = []

        def flush_message() -> None:
            nonlocal msg_id, book_msgs
            if msg_id is None:
                return
            self._advance_to(msg_ts)
            book.apply_message(msg_snapshot, m_sides, m_prices, m_sizes, msg_ts, self._on_fill)
            book_msgs += 1
            msg_id = None
            m_sides.clear(); m_prices.clear(); m_sizes.clear()
            if msg_snapshot:
                self._seen_snapshot = True
            if not self._started and self._seen_snapshot and book.is_valid():
                self._started = True
                strategy.on_start(ctx)
            if self._started:
                self._sample_equity(msg_ts)
                strategy.on_book(ctx)

        for batch in batches:
            ts_l = batch.column("ts").cast(pa.int64()).to_pylist()
            et_l = batch.column("event_type").to_pylist()
            side_l = batch.column("side").to_pylist()
            price_l = batch.column("price").to_pylist()
            size_l = batch.column("size").to_pylist()
            mid_l = batch.column("msg_id").to_pylist()
            events += len(ts_l)
            if start_ts is None and ts_l:
                start_ts = ts_l[0]

            for ts, et, side, price, size, mid in zip(ts_l, et_l, side_l, price_l, size_l, mid_l):
                if et == TRADE:
                    flush_message()
                    trades += 1
                    if not self._started:
                        continue
                    self._advance_to(ts)
                    book.on_trade(side, price, size, ts, self._on_fill)
                    strategy.on_trade(ctx, side, price, size)
                    continue
                if mid != msg_id or ts != msg_ts:  # msg_id начинается с 0 в каждом дневном файле
                    flush_message()
                    msg_id, msg_ts, msg_snapshot = mid, ts, et == BOOK_SNAPSHOT
                m_sides.append(side); m_prices.append(price); m_sizes.append(size)

        flush_message()
        if self._started:
            strategy.on_end(ctx)
            self._sample_equity(self.now + self._equity_sample_ns)

        result = BacktestResult(
            spec=self.spec, portfolio=self.portfolio, start_ts=start_ts or 0, end_ts=self.now,
            events=events, book_messages=book_msgs, trades=trades, orders_sent=self.orders_sent,
            wall_seconds=time.perf_counter() - wall, final_mid=book.mid(),
        )
        logger.info(
            f"Backtest {self.spec.symbol}: {events} событий за {result.wall_seconds:.1f}s, "
            f"ордеров {self.orders_sent}, сделок {len(self.portfolio.fills)}"
        )
        return result

    def run(self, start: date, end: date, exchange: str = "okx", processed_dir: str = "data/processed",
            batch_size: int = 500_000) -> BacktestResult:
        batches = iter_events(self.spec.symbol, start, end, exchange=exchange, processed_dir=processed_dir,
                              columns=COLUMNS, batch_size=batch_size)
        return self.run_batches(batches)
