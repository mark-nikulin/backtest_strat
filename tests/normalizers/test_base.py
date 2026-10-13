from datetime import date, datetime, timezone
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.normalizers import get_normalizer
from src.normalizers.base import StepStatus, sort_events
from src.normalizers.schema import BOOK_SNAPSHOT, BOOK_UPDATE, BUY, EVENT_SCHEMA, SELL, TRADE
from tests.normalizers.test_okx import make_orderbook_targz, make_trades_zip


def test_sort_events():
    ts1 = int(datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    ts2 = int(datetime(2024, 1, 1, 10, 0, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)

    # In unordered table:
    # 0: book update at ts1
    # 1: trade at ts1
    # 2: book snapshot at ts2
    # 3: trade at ts2
    table = pa.table(
        {
            "ts": pa.array([ts1, ts1, ts2, ts2], pa.int64()).cast(EVENT_SCHEMA.field("ts").type),
            "event_type": pa.array([BOOK_UPDATE, TRADE, BOOK_SNAPSHOT, TRADE], pa.int8()),
            "side": pa.array([BUY, BUY, SELL, SELL], pa.int8()),
            "price": pa.array([100.0, 100.0, 101.0, 101.0], pa.float64()),
            "size": pa.array([1.0, 1.0, 2.0, 2.0], pa.float64()),
            "num_orders": pa.nulls(4, pa.int32()),
            "msg_id": pa.array([1, 10, 2, 20], pa.int64()),
            "level": pa.array([0, 0, 0, 0], pa.int16()),
        },
        schema=EVENT_SCHEMA,
    )

    sorted_t = sort_events(table)

    # At ts1: TRADE should come before BOOK_UPDATE
    assert sorted_t.column("event_type")[0].as_py() == TRADE
    assert sorted_t.column("event_type")[1].as_py() == BOOK_UPDATE
    # At ts2: TRADE should come before BOOK_SNAPSHOT
    assert sorted_t.column("event_type")[2].as_py() == TRADE
    assert sorted_t.column("event_type")[3].as_py() == BOOK_SNAPSHOT


def test_build_events_missing_trades_returns_incomplete(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    norm = get_normalizer("okx", raw_dir=str(raw_dir), processed_dir=str(processed_dir))

    d = date(2024, 1, 1)
    # Create orderbook for day D, but NO trades for day D+1
    book_file = raw_dir / "okx" / "BTC-USDT" / "orderbook" / f"BTC-USDT-L2orderbook-400lv-{d.isoformat()}.tar.gz"
    make_orderbook_targz(book_file, [])

    # Normalize file first
    res_book = norm.normalize_file("BTC-USDT", "orderbook", book_file)
    assert res_book.status == StepStatus.PROCESSED

    # Build events should return INCOMPLETE
    res_events = norm.build_events("BTC-USDT", d)
    assert res_events.status == StepStatus.INCOMPLETE
    assert "нет сырых данных: trades 2024-01-01, trades 2024-01-02" in res_events.error


def test_full_pipeline_run_and_idempotency(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    norm = get_normalizer("okx", raw_dir=str(raw_dir), processed_dir=str(processed_dir))

    d = date(2024, 1, 1)
    d_next = date(2024, 1, 2)

    # 1. Prepare raw files
    # Trades D: contains trades in [2023-12-31 16:00, 2024-01-01 16:00) UTC
    # e.g. trade at 2024-01-01 02:00:00 UTC (created_time ms: 1704074400000)
    p_trades_d = raw_dir / "okx" / "BTC-USDT" / "trades" / f"BTC-USDT-trades-{d.isoformat()}.zip"
    make_trades_zip(p_trades_d, [
        {"trade_id": 1, "side": "buy", "price": 40000.0, "size": 0.5, "created_time": 1704074400000}
    ])

    # Trades D+1: contains trades in [2024-01-01 16:00, 2024-01-02 16:00) UTC
    # e.g. trade at 2024-01-01 20:00:00 UTC (created_time ms: 1704139200000)
    # and trade at 2024-01-02 02:00:00 UTC (created_time ms: 1704160800000) - outside day D
    p_trades_next = raw_dir / "okx" / "BTC-USDT" / "trades" / f"BTC-USDT-trades-{d_next.isoformat()}.zip"
    make_trades_zip(p_trades_next, [
        {"trade_id": 2, "side": "sell", "price": 40100.0, "size": 1.0, "created_time": 1704139200000},
        {"trade_id": 3, "side": "buy", "price": 40200.0, "size": 0.2, "created_time": 1704160800000},
    ])

    # Orderbook D: UTC day 2024-01-01
    p_book_d = raw_dir / "okx" / "BTC-USDT" / "orderbook" / f"BTC-USDT-L2orderbook-400lv-{d.isoformat()}.tar.gz"
    make_orderbook_targz(p_book_d, [
        {
            "instId": "BTC-USDT",
            "action": "snapshot",
            "ts": 1704074400000,  # exactly same ts as trade 1
            "bids": [["40000.0", "1.0", "2"]],
            "asks": [["40010.0", "1.0", "2"]],
        }
    ])

    progress_calls = []

    def on_progress(done, total):
        progress_calls.append((done, total))

    # Run normalizer
    report = norm.run("BTC-USDT", start=d, end=d, on_progress=on_progress)
    assert report.ok
    assert len(report.by_status(StepStatus.PROCESSED)) > 0
    assert len(progress_calls) > 0
    assert progress_calls[-1][0] == progress_calls[-1][1]

    # Verify generated events file
    events_file = processed_dir / "okx" / "BTC-USDT" / "events" / "year=2024" / "month=01" / f"{d.isoformat()}.parquet"
    assert events_file.exists()

    events_table = pq.read_table(events_file)
    # Trade 1 (at 02:00), Book snapshot bids & asks (at 02:00), Trade 2 (at 20:00).
    # Trade 3 is on 2024-01-02, so it must NOT be included in day 2024-01-01 events!
    assert events_table.num_rows == 4
    trade_ids = [t for t in events_table.column("msg_id").to_pylist() if t in (1, 2, 3)]
    assert trade_ids == [1, 2]

    # Check that at 02:00 (ts=1704074400000), trade came before book
    assert events_table.column("event_type")[0].as_py() == TRADE
    assert events_table.column("msg_id")[0].as_py() == 1
    assert events_table.column("event_type")[1].as_py() == BOOK_SNAPSHOT

    # Second run: should be SKIPPED (idempotency)
    report_2 = norm.run("BTC-USDT", start=d, end=d)
    assert report_2.ok
    for res in report_2.results:
        assert res.status == StepStatus.SKIPPED

    # Modifying raw file (changing its size) -> causes re-processing
    make_trades_zip(p_trades_d, [
        {"trade_id": 1, "side": "buy", "price": 40000.0, "size": 0.5, "created_time": 1704074400000},
        {"trade_id": 99, "side": "sell", "price": 40050.0, "size": 0.1, "created_time": 1704074401000},
    ])
    report_3 = norm.run("BTC-USDT", start=d, end=d)
    assert report_3.ok
    # Step for trades D was reprocessed
    assert any(r.name == f"trades:{p_trades_d.name}" and r.status == StepStatus.PROCESSED for r in report_3.results)

    # Deleting output file -> causes re-processing
    events_file.unlink()
    report_4 = norm.run("BTC-USDT", start=d, end=d)
    assert report_4.ok
    assert any(r.name == f"events:{d.isoformat()}" and r.status == StepStatus.PROCESSED for r in report_4.results)
    assert events_file.exists()

    # force=True -> causes re-processing
    report_5 = norm.run("BTC-USDT", start=d, end=d, force=True)
    assert report_5.ok
    for res in report_5.results:
        assert res.status == StepStatus.PROCESSED


def test_corrupt_raw_file_produces_failed_step(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    norm = get_normalizer("okx", raw_dir=str(raw_dir), processed_dir=str(processed_dir))

    d = date(2024, 1, 1)
    corrupt_file = raw_dir / "okx" / "BTC-USDT" / "trades" / f"BTC-USDT-trades-{d.isoformat()}.zip"
    corrupt_file.parent.mkdir(parents=True, exist_ok=True)
    corrupt_file.write_bytes(b"not a valid zip file corrupt data")

    # Valid orderbook
    valid_book = raw_dir / "okx" / "BTC-USDT" / "orderbook" / f"BTC-USDT-L2orderbook-400lv-{d.isoformat()}.tar.gz"
    make_orderbook_targz(valid_book, [])

    report = norm.run("BTC-USDT", start=d, end=d)
    # Corrupt trades file failed
    failed = report.by_status(StepStatus.FAILED)
    assert len(failed) == 1
    assert failed[0].name == f"trades:{corrupt_file.name}"
    assert failed[0].error is not None

    # Valid orderbook still got processed
    processed = report.by_status(StepStatus.PROCESSED)
    assert any(p.name == f"orderbook:{valid_book.name}" for p in processed)


def test_build_events_unnormalized_inputs_and_exception(tmp_path: Path, monkeypatch):
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    norm = get_normalizer("okx", raw_dir=str(raw_dir), processed_dir=str(processed_dir))

    d = date(2024, 1, 1)
    book_file = raw_dir / "okx" / "BTC-USDT" / "orderbook" / f"BTC-USDT-L2orderbook-400lv-{d.isoformat()}.tar.gz"
    make_orderbook_targz(book_file, [])
    trade_file_d = raw_dir / "okx" / "BTC-USDT" / "trades" / f"BTC-USDT-trades-{d.isoformat()}.zip"
    make_trades_zip(trade_file_d, [])
    trade_file_d1 = raw_dir / "okx" / "BTC-USDT" / "trades" / f"BTC-USDT-trades-2024-01-02.zip"
    make_trades_zip(trade_file_d1, [])

    # 1. Raw files exist, but build_events called before normalize_file -> INCOMPLETE ("входы еще не нормализованы")
    res = norm.build_events("BTC-USDT", d)
    assert res.status == StepStatus.INCOMPLETE
    assert "входы еще не нормализованы" in res.error

    # Normalize files
    norm.normalize_file("BTC-USDT", "orderbook", book_file)
    norm.normalize_file("BTC-USDT", "trades", trade_file_d)
    norm.normalize_file("BTC-USDT", "trades", trade_file_d1)

    # 2. Exception inside build_events -> FAILED
    monkeypatch.setattr("pyarrow.dataset.dataset", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("disk read error")))
    res_err = norm.build_events("BTC-USDT", d, force=True)
    assert res_err.status == StepStatus.FAILED
    assert "disk read error" in res_err.error

