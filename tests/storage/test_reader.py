from datetime import date, datetime, timezone
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq

from backtest_strat.normalizers.schema import EVENT_SCHEMA, TRADE, BUY
from backtest_strat.storage.parquet import PartitionedParquetWriter
from backtest_strat.storage.reader import event_files, iter_events, load_events


def _create_event_day(root: Path, d: date, rows: int = 10) -> Path:
    ts_base = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc)
    ts_ns = [int(ts_base.timestamp() * 1_000_000_000) + i * 1_000_000 for i in range(rows)]
    table = pa.table(
        {
            "ts": pa.array(ts_ns, pa.int64()).cast(EVENT_SCHEMA.field("ts").type),
            "event_type": pa.array([TRADE] * rows, pa.int8()),
            "side": pa.array([BUY] * rows, pa.int8()),
            "price": pa.array([50000.0 + i for i in range(rows)], pa.float64()),
            "size": pa.array([0.1 * (i + 1) for i in range(rows)], pa.float64()),
            "num_orders": pa.nulls(rows, pa.int32()),
            "msg_id": pa.array(list(range(1, rows + 1)), pa.int64()),
            "level": pa.array([0] * rows, pa.int16()),
        },
        schema=EVENT_SCHEMA,
    )
    with PartitionedParquetWriter(root, f"{d.isoformat()}.parquet", EVENT_SCHEMA) as writer:
        writer.write(table)
        outputs = writer.commit()
    return outputs[0]


def test_event_files_filtering_and_order(tmp_path: Path):
    events_root = tmp_path / "okx" / "BTC-USDT" / "events"
    d1 = date(2024, 1, 1)
    d3 = date(2024, 1, 3)
    # Only create day 1 and day 3 (day 2 is missing)
    f1 = _create_event_day(events_root, d1)
    f3 = _create_event_day(events_root, d3)

    found = event_files("BTC-USDT", date(2024, 1, 1), date(2024, 1, 3), exchange="okx", processed_dir=str(tmp_path))
    assert found == [f1, f3]

    # Outside range returns empty
    assert event_files("BTC-USDT", date(2024, 1, 4), date(2024, 1, 5), exchange="okx", processed_dir=str(tmp_path)) == []


def test_iter_events_batches(tmp_path: Path):
    events_root = tmp_path / "okx" / "BTC-USDT" / "events"
    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 2)
    _create_event_day(events_root, d1, rows=5)
    _create_event_day(events_root, d2, rows=5)

    batches = list(
        iter_events(
            "BTC-USDT",
            d1,
            d2,
            exchange="okx",
            processed_dir=str(tmp_path),
            batch_size=3,
            columns=["ts", "price"],
        )
    )

    # 5 rows in day 1 -> 2 batches (3, 2)
    # 5 rows in day 2 -> 2 batches (3, 2)
    assert len(batches) == 4
    total_rows = sum(b.num_rows for b in batches)
    assert total_rows == 10
    assert batches[0].schema.names == ["ts", "price"]


def test_load_events_success_and_empty(tmp_path: Path):
    events_root = tmp_path / "okx" / "BTC-USDT" / "events"
    d1 = date(2024, 1, 1)
    _create_event_day(events_root, d1, rows=5)

    table = load_events("BTC-USDT", d1, d1, exchange="okx", processed_dir=str(tmp_path))
    assert table.num_rows == 5
    assert table.schema == EVENT_SCHEMA

    # With selected columns
    table_cols = load_events("BTC-USDT", d1, d1, exchange="okx", processed_dir=str(tmp_path), columns=["price", "size"])
    assert table_cols.num_rows == 5
    assert table_cols.schema.names == ["price", "size"]

    # Empty range returns empty table with schema
    empty_table = load_events(
        "BTC-USDT", date(2024, 2, 1), date(2024, 2, 2), exchange="okx", processed_dir=str(tmp_path)
    )
    assert empty_table.num_rows == 0
    assert empty_table.schema == EVENT_SCHEMA

    # Empty range with selected columns
    empty_cols = load_events(
        "BTC-USDT",
        date(2024, 2, 1),
        date(2024, 2, 2),
        exchange="okx",
        processed_dir=str(tmp_path),
        columns=["price"],
    )
    assert empty_cols.num_rows == 0
    assert empty_cols.schema.names == ["price"]


def test_derived_files_and_invalid_kind(tmp_path: Path):
    from backtest_strat.storage.reader import derived_files, load_derived
    import pytest

    # 1. Invalid kind raises ValueError
    with pytest.raises(ValueError, match="kind должен быть одним из"):
        derived_files("unknown_kind", "BTC-USDT", date(2024, 1, 1), date(2024, 1, 2), processed_dir=str(tmp_path))

    with pytest.raises(ValueError, match="kind должен быть одним из"):
        load_derived("unknown_kind", "BTC-USDT", date(2024, 1, 1), date(2024, 1, 2), processed_dir=str(tmp_path))


def test_load_derived_multi_days_and_missing_day(tmp_path: Path):
    from backtest_strat.derived.build import BAR_SCHEMA
    from backtest_strat.storage.reader import derived_files, load_derived

    # Create synthetic bars for day 1 and day 3 (day 2 missing)
    d1 = date(2024, 1, 1)
    d3 = date(2024, 1, 3)

    def _write_bars(d: date, rows_count: int):
        dest = tmp_path / "okx" / "BTC-USDT" / "bars_1s" / f"year={d.year}" / f"month={d.month:02d}" / f"{d.isoformat()}.parquet"
        dest.parent.mkdir(parents=True, exist_ok=True)
        ts_ns = [int(datetime(d.year, d.month, d.day, 12, 0, i, tzinfo=timezone.utc).timestamp() * 1_000_000_000) for i in range(rows_count)]
        table = pa.table(
            {
                "ts": pa.array(ts_ns, pa.int64()).cast(BAR_SCHEMA.field("ts").type),
                "open": pa.array([100.0] * rows_count, pa.float64()),
                "high": pa.array([105.0] * rows_count, pa.float64()),
                "low": pa.array([99.0] * rows_count, pa.float64()),
                "close": pa.array([102.0] * rows_count, pa.float64()),
                "volume": pa.array([10.0] * rows_count, pa.float64()),
                "vwap": pa.array([101.0] * rows_count, pa.float64()),
                "buy_volume": pa.array([6.0] * rows_count, pa.float64()),
                "sell_volume": pa.array([4.0] * rows_count, pa.float64()),
                "imbalance": pa.array([0.2] * rows_count, pa.float64()),
                "trades": pa.array([5] * rows_count, pa.int32()),
                "mid_close": pa.array([101.0] * rows_count, pa.float64()),
                "spread_close": pa.array([2.0] * rows_count, pa.float64()),
            },
            schema=BAR_SCHEMA,
        )
        pq.write_table(table, dest)
        return dest

    f1 = _write_bars(d1, 3)
    f3 = _write_bars(d3, 2)

    # derived_files ignores missing day 2 and returns existing files
    files = derived_files("bars_1s", "BTC-USDT", d1, d3, processed_dir=str(tmp_path))
    assert files == [f1, f3]

    # load_derived concatenates chronologically across missing day
    table = load_derived("bars_1s", "BTC-USDT", d1, d3, processed_dir=str(tmp_path))
    assert table.num_rows == 5
    assert table.schema == BAR_SCHEMA

    # With selected columns
    table_cols = load_derived("bars_1s", "BTC-USDT", d1, d3, processed_dir=str(tmp_path), columns=["open", "close"])
    assert table_cols.num_rows == 5
    assert table_cols.schema.names == ["open", "close"]


def test_load_derived_empty_and_columns(tmp_path: Path):
    from backtest_strat.derived.build import BAR_SCHEMA
    from backtest_strat.storage.reader import load_derived

    # Empty range returns empty table with full schema
    empty_table = load_derived("bars_1s", "BTC-USDT", date(2024, 2, 1), date(2024, 2, 2), processed_dir=str(tmp_path))
    assert empty_table.num_rows == 0
    assert empty_table.schema == BAR_SCHEMA

    # Empty range with column selection returns empty table with selected schema
    empty_cols = load_derived(
        "bars_1s", "BTC-USDT", date(2024, 2, 1), date(2024, 2, 2), processed_dir=str(tmp_path), columns=["ts", "vwap"]
    )
    assert empty_cols.num_rows == 0
    assert empty_cols.schema.names == ["ts", "vwap"]
