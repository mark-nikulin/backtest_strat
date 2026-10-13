from typing import Dict
import numpy as np
import pyarrow as pa
import pytest

from src.backtest.bar_feed import bars_to_events
from src.backtest.engine import BacktestEngine
from src.backtest.schedule import ScheduledPosition
from src.backtest.types import BUY, InstrumentSpec, SELL
from src.normalizers.schema import BOOK_SNAPSHOT, EVENT_SCHEMA

NS = 1_000_000_000


def test_bars_to_events_structure_and_types():
    bar_ts = np.array([0, 60 * NS], dtype=np.int64)
    close = np.array([100.05, 101.07], dtype=np.float64)
    interval_ns = 60 * NS
    tick_size = 0.1
    half_spread_bps = 5.0  # 5 bps of 100 is 0.05, which is < tick_size (0.1), so max(0.05, 0.1) = 0.1

    batch = bars_to_events(bar_ts, close, interval_ns, tick_size, half_spread_bps, fill_gaps=False)

    assert isinstance(batch, pa.RecordBatch)
    assert batch.schema.names == ["ts", "event_type", "side", "price", "size", "msg_id"]
    assert batch.schema.field("ts").type == pa.timestamp("ns", tz="UTC")
    assert batch.schema.field("event_type").type == pa.int8()
    assert batch.schema.field("side").type == pa.int8()
    assert batch.schema.field("price").type == pa.float64()
    assert batch.schema.field("size").type == pa.float64()
    assert batch.schema.field("msg_id").type == pa.int64()

    # 2 bars -> 4 rows (bid, ask for each)
    assert batch.num_rows == 4
    # All events are BOOK_SNAPSHOT
    assert all(et == BOOK_SNAPSHOT for et in batch.column("event_type").to_pylist())

    # Snapshot is at close of bar: bar 0 ends at 60s, bar 1 ends at 120s
    ts_list = batch.column("ts").cast(pa.int64()).to_pylist()
    assert ts_list == [60 * NS, 60 * NS, 120 * NS, 120 * NS]

    # Check bid and ask rounding to tick_size (0.1)
    # For close = 100.05, half = max(100.05 * 5e-4, 0.1) = 0.1
    # bid = floor((100.05 - 0.1)/0.1) * 0.1 = floor(99.95/0.1)*0.1 = 99.9
    # ask = ceil((100.05 + 0.1)/0.1) * 0.1 = ceil(100.15/0.1)*0.1 = 100.2
    prices = batch.column("price").to_pylist()
    assert prices[0] == 99.9   # bid bar 0
    assert prices[1] == 100.2  # ask bar 0
    # Both are exact multiples of tick_size
    assert round(prices[0] % tick_size, 6) in (0.0, tick_size)
    assert round(prices[1] % tick_size, 6) in (0.0, tick_size)
    assert prices[1] - prices[0] >= tick_size


def test_bars_to_events_fill_gaps():
    # Bars at t=0 and t=120s (missing minute at t=60s)
    bar_ts = np.array([0, 120 * NS], dtype=np.int64)
    close = np.array([100.0, 105.0], dtype=np.float64)
    interval_ns = 60 * NS

    # 1. fill_gaps=True: grid has t=0, t=60, t=120 -> 3 bars = 6 rows
    batch_filled = bars_to_events(bar_ts, close, interval_ns, tick_size=0.1, half_spread_bps=10.0, fill_gaps=True)
    assert batch_filled.num_rows == 6
    ts_filled = batch_filled.column("ts").cast(pa.int64()).to_pylist()
    assert ts_filled == [60 * NS, 60 * NS, 120 * NS, 120 * NS, 180 * NS, 180 * NS]

    # Bar at t=60 gets previous close (100.0)
    prices = batch_filled.column("price").to_pylist()
    mid_gap = (prices[2] + prices[3]) / 2.0
    assert mid_gap == pytest.approx(100.0)

    # 2. fill_gaps=False: only 2 bars = 4 rows
    batch_unfilled = bars_to_events(bar_ts, close, interval_ns, tick_size=0.1, half_spread_bps=10.0, fill_gaps=False)
    assert batch_unfilled.num_rows == 4


def test_scheduled_position_engine():
    # Setup 5 minutes of bars: closes at 60s, 120s, 180s, 240s, 300s
    bar_ts = np.array([0, 60 * NS, 120 * NS, 180 * NS, 240 * NS], dtype=np.int64)
    close = np.array([100.0, 100.0, 100.0, 100.0, 100.0], dtype=np.float64)
    spec = InstrumentSpec(
        symbol="BTC-USDT",
        tick_size=0.1,
        lot_size=0.05,
        maker_fee=0.0,
        taker_fee=0.0,
        feed_latency_ns=0,
        order_latency_ns=0,
    )

    batch = bars_to_events(bar_ts, close, 60 * NS, tick_size=0.1, half_spread_bps=10.0)

    # Schedule:
    # at t=60s: sign = +1 (entry)
    # at t=120s: sign = +1 (same sign -> NO trade)
    # at t=180s: sign = -1 (reversal -> single SELL order of size 2 * qty)
    # at t=240s: sign = 0  (exit -> single BUY order of size qty)
    # at t=999s: non-snapshot timestamp -> NO trade
    schedule: Dict[int, int] = {
        60 * NS: 1,
        120 * NS: 1,
        180 * NS: -1,
        240 * NS: 0,
        999 * NS: 1,
    }

    strat = ScheduledPosition(schedule, notional=1000.0)
    engine = BacktestEngine(strat, spec)
    res = engine.run_batches([batch])

    fills = res.portfolio.fills
    # Should have exactly 3 fills:
    # 1. Entry at t=60s
    # 2. Reversal at t=180s
    # 3. Exit at t=240s
    assert len(fills) == 3

    # Expected qty for notional 1000 and mid 100:
    # lot_size * int(1000 / 100 / 0.05) = 0.05 * int(200) = 10.0
    expected_qty = 10.0

    # Fill 1: BUY expected_qty
    f1 = fills[0]
    assert f1.side == BUY
    assert f1.amount == pytest.approx(expected_qty)

    # Fill 2: SELL 2 * expected_qty (reversal with single order!)
    f2 = fills[1]
    assert f2.side == SELL
    assert f2.amount == pytest.approx(2 * expected_qty)

    # Fill 3: BUY expected_qty (back to 0)
    f3 = fills[2]
    assert f3.side == BUY
    assert f3.amount == pytest.approx(expected_qty)

    # Final position is 0
    assert res.portfolio.position == pytest.approx(0.0)
