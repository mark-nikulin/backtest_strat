from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backtest_strat.backtest.book import ReplayBook
from backtest_strat.derived.build import (
    BAR_SCHEMA,
    KINDS,
    L1_SCHEMA,
    NS,
    build_bars,
    build_day,
    build_l1,
    read_trades,
)
from backtest_strat.normalizers.schema import (
    BOOK_SNAPSHOT,
    BOOK_UPDATE,
    BUY,
    EVENT_SCHEMA,
    SELL,
    TRADE,
)

TS_TYPE = pa.timestamp("ns", tz="UTC")


def _day_start_ns(day_str: str = "2024-01-01") -> int:
    return int(datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * NS)


def _write_parquet(dest: Path, rows: List[dict], batch_size: int = 500_000) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "ts": pa.array([r["ts"] for r in rows], pa.int64()).cast(EVENT_SCHEMA.field("ts").type),
            "event_type": pa.array([r["event_type"] for r in rows], pa.int8()),
            "side": pa.array([r["side"] for r in rows], pa.int8()),
            "price": pa.array([r["price"] for r in rows], pa.float64()),
            "size": pa.array([r["size"] for r in rows], pa.float64()),
            "num_orders": pa.array([r.get("num_orders") for r in rows], pa.int32()),
            "msg_id": pa.array([r["msg_id"] for r in rows], pa.int64()),
            "level": pa.array([r.get("level", 0) for r in rows], pa.int16()),
        },
        schema=EVENT_SCHEMA,
    )
    pq.write_table(table, dest, row_group_size=batch_size)


# =========================================================================
# L1 Tests
# =========================================================================

def test_build_l1_only_on_top_change(tmp_path: Path):
    day_ns = _day_start_ns("2024-01-01")
    file_path = tmp_path / "2024-01-01.parquet"

    rows = [
        # 1. Snapshot: best bid = 100 (sz 1.0), best ask = 102 (sz 1.0) -> emitted
        {"ts": day_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": day_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 102.0, "size": 1.0, "msg_id": 1, "level": 0},

        # 2. Update at deep level: bid 90 (sz 5.0) -> top of book unchanged -> NOT emitted
        {"ts": day_ns + 2 * NS, "event_type": BOOK_UPDATE, "side": BUY, "price": 90.0, "size": 5.0, "msg_id": 2, "level": 1},

        # 3. Update at best ask: ask 101 (sz 2.0) -> ask price improves -> emitted
        {"ts": day_ns + 3 * NS, "event_type": BOOK_UPDATE, "side": SELL, "price": 101.0, "size": 2.0, "msg_id": 3, "level": 0},

        # 4. Update at best bid size: bid 100 (sz 3.0) -> bid size changed -> emitted
        {"ts": day_ns + 4 * NS, "event_type": BOOK_UPDATE, "side": BUY, "price": 100.0, "size": 3.0, "msg_id": 4, "level": 0},
    ]
    _write_parquet(file_path, rows)

    l1 = build_l1(file_path)
    assert l1.schema == L1_SCHEMA
    assert l1.num_rows == 3

    # Row 1
    assert l1["bid_px"][0].as_py() == 100.0
    assert l1["bid_sz"][0].as_py() == 1.0
    assert l1["ask_px"][0].as_py() == 102.0
    assert l1["ask_sz"][0].as_py() == 1.0

    # Row 2 (ask price improved to 101.0, size 2.0)
    assert l1["bid_px"][1].as_py() == 100.0
    assert l1["bid_sz"][1].as_py() == 1.0
    assert l1["ask_px"][1].as_py() == 101.0
    assert l1["ask_sz"][1].as_py() == 2.0

    # Row 3 (bid size changed to 3.0)
    assert l1["bid_px"][2].as_py() == 100.0
    assert l1["bid_sz"][2].as_py() == 3.0
    assert l1["ask_px"][2].as_py() == 101.0
    assert l1["ask_sz"][2].as_py() == 2.0


def test_build_l1_multi_level_message_one_row(tmp_path: Path):
    day_ns = _day_start_ns("2024-01-01")
    file_path = tmp_path / "2024-01-01.parquet"

    # Multi-level snapshot: 3 bids and 3 asks, all in msg_id=1
    rows = []
    for i in range(3):
        rows.append({"ts": day_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0 - i, "size": 1.0 + i, "msg_id": 1, "level": i})
        rows.append({"ts": day_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0 + i, "size": 1.0 + i, "msg_id": 1, "level": i})

    _write_parquet(file_path, rows)
    l1 = build_l1(file_path)
    # Even though 6 rows in parquet, only 1 L1 row should be emitted
    assert l1.num_rows == 1
    assert l1["bid_px"][0].as_py() == 100.0
    assert l1["bid_sz"][0].as_py() == 1.0
    assert l1["ask_px"][0].as_py() == 101.0
    assert l1["ask_sz"][0].as_py() == 1.0


def test_build_l1_no_rows_before_first_snapshot(tmp_path: Path):
    day_ns = _day_start_ns("2024-01-01")
    file_path = tmp_path / "2024-01-01.parquet"

    rows = [
        # 1. Day starts with BOOK_UPDATE -> book incomplete, must NOT emit L1
        {"ts": day_ns + 1 * NS, "event_type": BOOK_UPDATE, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": day_ns + 1 * NS, "event_type": BOOK_UPDATE, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1, "level": 0},
        # 2. Trade
        {"ts": day_ns + 2 * NS, "event_type": TRADE, "side": BUY, "price": 100.5, "size": 0.5, "msg_id": 2},
        # 3. First snapshot arrives at t=10s -> L1 emitted starting here
        {"ts": day_ns + 10 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 2.0, "msg_id": 3, "level": 0},
        {"ts": day_ns + 10 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 2.0, "msg_id": 3, "level": 0},
    ]
    _write_parquet(file_path, rows)

    l1 = build_l1(file_path)
    assert l1.num_rows == 1
    assert l1["ts"][0].as_py() == datetime.fromtimestamp((day_ns + 10 * NS) / NS, tz=timezone.utc)
    assert l1["bid_sz"][0].as_py() == 2.0


def test_build_l1_level_deletion_uncovers_next_level(tmp_path: Path):
    day_ns = _day_start_ns("2024-01-01")
    file_path = tmp_path / "2024-01-01.parquet"

    rows = [
        # Snapshot with best bid 100 (sz 1.0) and second bid 99 (sz 2.0)
        {"ts": day_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": day_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 99.0, "size": 2.0, "msg_id": 1, "level": 1},
        {"ts": day_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1, "level": 0},

        # Delete level 100 (size 0.0) -> next level 99 becomes best bid
        {"ts": day_ns + 2 * NS, "event_type": BOOK_UPDATE, "side": BUY, "price": 100.0, "size": 0.0, "msg_id": 2, "level": 0},
    ]
    _write_parquet(file_path, rows)

    l1 = build_l1(file_path)
    assert l1.num_rows == 2
    # Second row has bid_px 99.0 and bid_sz 2.0
    assert l1["bid_px"][1].as_py() == 99.0
    assert l1["bid_sz"][1].as_py() == 2.0


def test_build_l1_split_message_counted_as_one(tmp_path: Path):
    day_ns = _day_start_ns("2024-01-01")
    file_path = tmp_path / "2024-01-01.parquet"

    rows = [
        {"ts": day_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": day_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1, "level": 1},
    ]
    # Write with batch_size=1 so each row is in a separate batch
    _write_parquet(file_path, rows, batch_size=1)

    l1 = build_l1(file_path, batch_size=1)
    assert l1.num_rows == 1
    assert l1["bid_px"][0].as_py() == 100.0
    assert l1["ask_px"][0].as_py() == 101.0


# =========================================================================
# Bar Tests
# =========================================================================

def test_build_bars_ohlcv_imbalance_metrics():
    # 3 trades within the same 1-second interval (interval_ns = 1 * NS)
    t0 = 1_000_000_000
    trades = {
        "ts": np.array([t0 + 100_000_000, t0 + 200_000_000, t0 + 500_000_000], dtype=np.int64),
        "side": np.array([BUY, SELL, BUY], dtype=np.int8),
        "price": np.array([100.0, 105.0, 102.0], dtype=np.float64),
        "size": np.array([2.0, 1.0, 1.0], dtype=np.float64),
    }
    empty_l1 = L1_SCHEMA.empty_table()
    bars = build_bars(trades, 1 * NS, empty_l1)

    assert bars.schema == BAR_SCHEMA
    assert bars.num_rows == 1

    # Check OHLC
    assert bars["open"][0].as_py() == 100.0
    assert bars["high"][0].as_py() == 105.0
    assert bars["low"][0].as_py() == 100.0
    assert bars["close"][0].as_py() == 102.0

    # Volume: 2 + 1 + 1 = 4.0
    assert bars["volume"][0].as_py() == 4.0
    # VWAP: (100*2 + 105*1 + 102*1) / 4 = (200 + 105 + 102) / 4 = 407 / 4 = 101.75
    assert bars["vwap"][0].as_py() == pytest.approx(101.75)

    # Buy / Sell volume
    assert bars["buy_volume"][0].as_py() == 3.0
    assert bars["sell_volume"][0].as_py() == 1.0

    # Imbalance: (3 - 1) / 4 = 0.5
    imb = bars["imbalance"][0].as_py()
    assert imb == pytest.approx(0.5)
    assert -1.0 <= imb <= 1.0

    # Trade count
    assert bars["trades"][0].as_py() == 3


def test_build_bars_interval_boundaries():
    interval_ns = 1 * NS  # 1 second
    t0 = 10_000_000_000

    # Trade 1 at t=0 of interval 10s
    # Trade 2 at t=999_999_999ns (end of interval 10s)
    # Trade 3 at t=1_000_000_000ns (start of interval 11s)
    trades = {
        "ts": np.array([t0, t0 + 999_999_999, t0 + 1_000_000_000], dtype=np.int64),
        "side": np.array([BUY, BUY, SELL], dtype=np.int8),
        "price": np.array([100.0, 101.0, 102.0], dtype=np.float64),
        "size": np.array([1.0, 1.0, 1.0], dtype=np.float64),
    }
    empty_l1 = L1_SCHEMA.empty_table()
    bars = build_bars(trades, interval_ns, empty_l1)

    assert bars.num_rows == 2
    # Bar 0 starts at t0
    assert bars["ts"][0].as_py() == datetime.fromtimestamp(t0 / NS, tz=timezone.utc)
    assert bars["trades"][0].as_py() == 2
    assert bars["close"][0].as_py() == 101.0

    # Bar 1 starts at t0 + 1_000_000_000
    assert bars["ts"][1].as_py() == datetime.fromtimestamp((t0 + 1_000_000_000) / NS, tz=timezone.utc)
    assert bars["trades"][1].as_py() == 1
    assert bars["open"][1].as_py() == 102.0


def test_build_bars_empty_intervals_absent():
    interval_ns = 1 * NS
    # Trade at t=1s and trade at t=5s (seconds 2, 3, 4 have no trades)
    trades = {
        "ts": np.array([1 * NS, 5 * NS], dtype=np.int64),
        "side": np.array([BUY, SELL], dtype=np.int8),
        "price": np.array([100.0, 102.0], dtype=np.float64),
        "size": np.array([1.0, 1.0], dtype=np.float64),
    }
    bars = build_bars(trades, interval_ns, L1_SCHEMA.empty_table())
    # Exactly 2 bars, empty intervals are not generated
    assert bars.num_rows == 2
    ts0 = bars["ts"][0].as_py()
    ts1 = bars["ts"][1].as_py()
    assert ts0 == datetime.fromtimestamp(1.0, tz=timezone.utc)
    assert ts1 == datetime.fromtimestamp(5.0, tz=timezone.utc)


def test_build_bars_empty_trades():
    trades = {
        "ts": np.array([], dtype=np.int64),
        "side": np.array([], dtype=np.int8),
        "price": np.array([], dtype=np.float64),
        "size": np.array([], dtype=np.float64),
    }
    bars = build_bars(trades, 1 * NS, L1_SCHEMA.empty_table())
    assert bars.num_rows == 0
    assert bars.schema == BAR_SCHEMA


def test_build_bars_numerical_precision_huge_volumes():
    # Large number of trades with large base volume
    n = 10_000
    rng = np.random.default_rng(42)
    # Huge base volume to test potential loss of precision if subtracting cumsums
    large_size = 1e11 + rng.uniform(0.1, 10.0, size=n)
    prices = 50_000.0 + rng.uniform(-10.0, 10.0, size=n)
    sides = rng.choice([BUY, SELL], size=n)
    ts = np.sort(rng.integers(1 * NS, 2 * NS, size=n))

    trades = {
        "ts": ts,
        "side": sides,
        "price": prices,
        "size": large_size,
    }
    bars = build_bars(trades, 1 * NS, L1_SCHEMA.empty_table())
    assert bars.num_rows > 0

    vwap = bars["vwap"].to_numpy()
    low = bars["low"].to_numpy()
    high = bars["high"].to_numpy()
    imbalance = bars["imbalance"].to_numpy()

    # VWAP must be strictly between low and high (within machine precision)
    assert np.all(vwap >= low - 1e-6)
    assert np.all(vwap <= high + 1e-6)
    # Imbalance must be strictly bounded in [-1.0, 1.0]
    assert np.all(imbalance >= -1.0)
    assert np.all(imbalance <= 1.0)


def test_build_bars_as_of_mid_close_spread_close():
    interval_ns = 1 * NS  # 1s bars
    # Trades at t = 0.5s, 1.5s, 2.5s
    trades = {
        "ts": np.array([500_000_000, 1_500_000_000, 2_500_000_000], dtype=np.int64),
        "side": np.array([BUY, BUY, BUY], dtype=np.int8),
        "price": np.array([100.0, 100.0, 100.0], dtype=np.float64),
        "size": np.array([1.0, 1.0, 1.0], dtype=np.float64),
    }

    # L1: first record arrives at t = 1.2s (after first bar ends at t=1.0s)
    # L1 updates at t = 2.1s
    l1_table = pa.table(
        {
            "ts": pa.array([1_200_000_000, 2_100_000_000], pa.int64()).cast(TS_TYPE),
            "bid_px": pa.array([100.0, 101.0], pa.float64()),
            "bid_sz": pa.array([1.0, 1.0], pa.float64()),
            "ask_px": pa.array([102.0, 103.0], pa.float64()),
            "ask_sz": pa.array([1.0, 1.0], pa.float64()),
        },
        schema=L1_SCHEMA,
    )

    bars = build_bars(trades, interval_ns, l1_table)
    assert bars.num_rows == 3

    # Bar 0 (ts=0s to 1s): ends at 1.0s before first L1 (1.2s) -> mid_close and spread_close must be null
    assert bars["mid_close"][0].as_py() is None
    assert bars["spread_close"][0].as_py() is None

    # Bar 1 (ts=1s to 2s): ends at 2.0s -> latest L1 before 2.0s is at 1.2s (bid=100, ask=102)
    # mid = 101.0, spread = 2.0
    assert bars["mid_close"][1].as_py() == pytest.approx(101.0)
    assert bars["spread_close"][1].as_py() == pytest.approx(2.0)

    # Bar 2 (ts=2s to 3s): ends at 3.0s -> latest L1 before 3.0s is at 2.1s (bid=101, ask=103)
    # mid = 102.0, spread = 2.0
    assert bars["mid_close"][2].as_py() == pytest.approx(102.0)
    assert bars["spread_close"][2].as_py() == pytest.approx(2.0)


# =========================================================================
# build_day Tests
# =========================================================================

def test_build_day(tmp_path: Path):
    day_ns = _day_start_ns("2024-01-01")
    file_path = tmp_path / "2024-01-01.parquet"

    rows = [
        {"ts": day_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": day_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": day_ns + 2 * NS, "event_type": TRADE, "side": BUY, "price": 100.5, "size": 0.5, "msg_id": 2},
    ]
    _write_parquet(file_path, rows)

    res = build_day(file_path)
    assert set(res.keys()) == {"l1", "bars_1s", "bars_1m"}
    assert res["l1"].num_rows == 1
    assert res["bars_1s"].num_rows == 1
    assert res["bars_1m"].num_rows == 1


# =========================================================================
# Reference Verification (slow)
# =========================================================================

@pytest.mark.slow
def test_reference_verification_bars_and_l1(tmp_path: Path):
    import pandas as pd

    # 1. Compare bars against pandas groupby
    n = 200
    rng = np.random.default_rng(123)
    ts = np.sort(rng.integers(0, 10 * NS, size=n))
    sides = rng.choice([BUY, SELL], size=n)
    prices = 100.0 + rng.uniform(-5.0, 5.0, size=n)
    sizes = rng.uniform(0.1, 2.0, size=n)

    trades = {"ts": ts, "side": sides, "price": prices, "size": sizes}
    bars_arrow = build_bars(trades, 1 * NS, L1_SCHEMA.empty_table())
    df_bars = bars_arrow.to_pandas()

    # Pandas reference computation
    df_raw = pd.DataFrame({"ts": ts, "side": sides, "price": prices, "size": sizes})
    df_raw["bucket"] = df_raw["ts"] // (1 * NS)
    grouped = df_raw.groupby("bucket")

    ref_open = grouped["price"].first().values
    ref_high = grouped["price"].max().values
    ref_low = grouped["price"].min().values
    ref_close = grouped["price"].last().values
    ref_volume = grouped["size"].sum().values
    ref_trades = grouped.size().values

    np.testing.assert_allclose(df_bars["open"].values, ref_open)
    np.testing.assert_allclose(df_bars["high"].values, ref_high)
    np.testing.assert_allclose(df_bars["low"].values, ref_low)
    np.testing.assert_allclose(df_bars["close"].values, ref_close)
    np.testing.assert_allclose(df_bars["volume"].values, ref_volume)
    np.testing.assert_array_equal(df_bars["trades"].values, ref_trades)

    # 2. Compare L1 against ReplayBook
    from backtest_strat.backtest.types import InstrumentSpec
    spec = InstrumentSpec(symbol="BTC-USDT", tick_size=0.1, lot_size=0.01)
    book = ReplayBook(spec)
    file_path = tmp_path / "2024-01-01.parquet"
    rows = [
        {"ts": 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": 2 * NS, "event_type": BOOK_UPDATE, "side": BUY, "price": 100.5, "size": 2.0, "msg_id": 2, "level": 0},
        {"ts": 3 * NS, "event_type": BOOK_UPDATE, "side": SELL, "price": 100.8, "size": 1.5, "msg_id": 3, "level": 0},
    ]
    _write_parquet(file_path, rows)
    l1_res = build_l1(file_path)

    # Apply manually to ReplayBook
    noop = lambda *args: None
    book.apply_message(True, [BUY, SELL], [100.0, 101.0], [1.0, 1.0], 1 * NS, noop)
    assert l1_res["bid_px"][0].as_py() == book.best_bid()
    assert l1_res["ask_px"][0].as_py() == book.best_ask()

    book.apply_message(False, [BUY], [100.5], [2.0], 2 * NS, noop)
    assert l1_res["bid_px"][1].as_py() == book.best_bid()

    book.apply_message(False, [SELL], [100.8], [1.5], 3 * NS, noop)
    assert l1_res["ask_px"][2].as_py() == book.best_ask()
