from datetime import datetime, timezone
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.normalizers.schema import EVENT_SCHEMA, TRADE, BUY
from src.storage.parquet import Manifest, PartitionedParquetWriter, partition_dir


def _make_sample_table(timestamps_utc: list[datetime]) -> pa.Table:
    n = len(timestamps_utc)
    ts_ns = [int(dt.timestamp() * 1_000_000_000) for dt in timestamps_utc]
    return pa.table(
        {
            "ts": pa.array(ts_ns, pa.int64()).cast(EVENT_SCHEMA.field("ts").type),
            "event_type": pa.array([TRADE] * n, pa.int8()),
            "side": pa.array([BUY] * n, pa.int8()),
            "price": pa.array([100.0] * n, pa.float64()),
            "size": pa.array([1.5] * n, pa.float64()),
            "num_orders": pa.nulls(n, pa.int32()),
            "msg_id": pa.array(list(range(1, n + 1)), pa.int64()),
            "level": pa.array([0] * n, pa.int16()),
        },
        schema=EVENT_SCHEMA,
    )


def test_partition_dir():
    root = Path("/tmp/data")
    assert partition_dir(root, 2024, 1) == Path("/tmp/data/year=2024/month=01")
    assert partition_dir(root, 2024, 12) == Path("/tmp/data/year=2024/month=12")


def test_writer_single_month(tmp_path: Path):
    root = tmp_path / "events"
    table = _make_sample_table([
        datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc),
        datetime(2024, 1, 15, 11, 0, tzinfo=timezone.utc),
    ])

    with PartitionedParquetWriter(root, "day.parquet", EVENT_SCHEMA) as writer:
        writer.write(table)
        outputs = writer.commit()

    assert len(outputs) == 1
    expected = root / "year=2024" / "month=01" / "day.parquet"
    assert outputs[0] == expected
    assert expected.exists()
    assert not expected.with_name("day.parquet.part").exists()

    # Verify content
    read_table = pq.read_table(expected)
    assert read_table.num_rows == 2


def test_writer_splits_across_month_boundary(tmp_path: Path):
    root = tmp_path / "events"
    # Row 1 is Jan 31, Row 2 is Feb 1
    table = _make_sample_table([
        datetime(2024, 1, 31, 23, 59, 59, tzinfo=timezone.utc),
        datetime(2024, 2, 1, 0, 0, 1, tzinfo=timezone.utc),
    ])

    with PartitionedParquetWriter(root, "source.parquet", EVENT_SCHEMA) as writer:
        writer.write(table)
        outputs = writer.commit()

    assert len(outputs) == 2
    path_jan = root / "year=2024" / "month=01" / "source.parquet"
    path_feb = root / "year=2024" / "month=02" / "source.parquet"
    assert outputs == sorted([path_jan, path_feb])
    assert path_jan.exists()
    assert path_feb.exists()

    assert pq.read_table(path_jan).num_rows == 1
    assert pq.read_table(path_feb).num_rows == 1


def test_writer_empty_table_is_noop(tmp_path: Path):
    root = tmp_path / "events"
    empty = EVENT_SCHEMA.empty_table()

    with PartitionedParquetWriter(root, "empty.parquet", EVENT_SCHEMA) as writer:
        writer.write(empty)
        outputs = writer.commit()

    assert outputs == []


def test_writer_abort_removes_part_files(tmp_path: Path):
    root = tmp_path / "events"
    table = _make_sample_table([datetime(2024, 3, 1, tzinfo=timezone.utc)])

    writer = PartitionedParquetWriter(root, "aborted.parquet", EVENT_SCHEMA)
    writer.write(table)

    part_file = root / "year=2024" / "month=03" / "aborted.parquet.part"
    assert part_file.exists()

    writer.abort()
    assert not part_file.exists()
    assert not (root / "year=2024" / "month=03" / "aborted.parquet").exists()


def test_writer_context_manager_aborts_on_exception(tmp_path: Path):
    root = tmp_path / "events"
    table = _make_sample_table([datetime(2024, 4, 1, tzinfo=timezone.utc)])

    with pytest.raises(RuntimeError):
        with PartitionedParquetWriter(root, "failed.parquet", EVENT_SCHEMA) as writer:
            writer.write(table)
            raise RuntimeError("Processing failed")

    target = root / "year=2024" / "month=04" / "failed.parquet"
    assert not target.exists()
    assert not target.with_name("failed.parquet.part").exists()


def test_manifest_lifecycle(tmp_path: Path):
    manifest_path = tmp_path / "_manifest.json"
    manifest = Manifest(manifest_path)

    key = "trades:sample.zip"
    fingerprint = {"sample.zip": 1024}

    # Not current initially
    assert not manifest.is_current(key, fingerprint)
    assert manifest.get(key) is None

    # Set outputs
    out_file = tmp_path / "out.parquet"
    out_file.write_bytes(b"data")
    manifest.set(key, fingerprint, [out_file])

    assert manifest.is_current(key, fingerprint)
    assert manifest.get(key)["fingerprint"] == fingerprint
    assert manifest.get(key)["outputs"] == [str(out_file)]

    # New instance reads saved state from disk
    loaded_manifest = Manifest(manifest_path)
    assert loaded_manifest.is_current(key, fingerprint)

    # Different fingerprint is not current
    assert not loaded_manifest.is_current(key, {"sample.zip": 2048})

    # Missing output is not current
    out_file.unlink()
    assert not loaded_manifest.is_current(key, fingerprint)


def test_manifest_remove_outputs(tmp_path: Path):
    manifest_path = tmp_path / "_manifest.json"
    manifest = Manifest(manifest_path)

    out1 = tmp_path / "out1.parquet"
    out2 = tmp_path / "out2.parquet"
    out1.write_bytes(b"1")
    out2.write_bytes(b"2")

    key = "orderbook:test.tar.gz"
    fp = {"test.tar.gz": 500}
    manifest.set(key, fp, [out1, out2])

    assert out1.exists() and out2.exists()
    assert manifest.is_current(key, fp)

    # Remove outputs
    manifest.remove_outputs(key)
    assert not out1.exists()
    assert not out2.exists()
    assert manifest.get(key) is None


def test_writer_abort_with_close_error(tmp_path: Path):
    from unittest.mock import MagicMock
    root = tmp_path / "events"
    table = _make_sample_table([datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)])
    writer = PartitionedParquetWriter(root, "day.parquet", EVENT_SCHEMA)
    writer.write(table)
    for w in writer._writers.values():
        w.close = MagicMock(side_effect=RuntimeError("close failed"))
    writer.abort()
    for w in writer._writers.values():
        w.close = MagicMock()  # avoid exception in __del__


