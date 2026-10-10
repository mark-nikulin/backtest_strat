from datetime import date, datetime, timezone
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq

from src.normalizers.schema import EVENT_SCHEMA, TRADE, BUY
from src.storage.parquet import PartitionedParquetWriter
from src.storage.reader import event_files, iter_events, load_events


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
