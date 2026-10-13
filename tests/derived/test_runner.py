from datetime import date, datetime, timezone
from pathlib import Path
from typing import List
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.derived.runner import DeriveReport, DeriveStep, derive_symbol
from src.normalizers.schema import BOOK_SNAPSHOT, BUY, EVENT_SCHEMA, SELL, TRADE

NS = 1_000_000_000


def _create_event_day(events_root: Path, d: date) -> Path:
    ts_base = int(datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc).timestamp() * NS)
    dest = events_root / f"year={d.year}" / f"month={d.month:02d}" / f"{d.isoformat()}.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"ts": ts_base + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": ts_base + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": ts_base + 2 * NS, "event_type": TRADE, "side": BUY, "price": 100.5, "size": 0.5, "msg_id": 2, "level": 0},
    ]
    table = pa.table(
        {
            "ts": pa.array([r["ts"] for r in rows], pa.int64()).cast(EVENT_SCHEMA.field("ts").type),
            "event_type": pa.array([r["event_type"] for r in rows], pa.int8()),
            "side": pa.array([r["side"] for r in rows], pa.int8()),
            "price": pa.array([r["price"] for r in rows], pa.float64()),
            "size": pa.array([r["size"] for r in rows], pa.float64()),
            "num_orders": pa.array([None] * len(rows), pa.int32()),
            "msg_id": pa.array([r["msg_id"] for r in rows], pa.int64()),
            "level": pa.array([r["level"] for r in rows], pa.int16()),
        },
        schema=EVENT_SCHEMA,
    )
    pq.write_table(table, dest)
    return dest


def test_derive_symbol_idempotency_and_skipped(tmp_path: Path):
    events_root = tmp_path / "okx" / "BTC-USDT" / "events"
    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 2)
    _create_event_day(events_root, d1)
    _create_event_day(events_root, d2)

    progress_records = []
    def on_prog(i, n):
        progress_records.append((i, n))

    # 1. Первый запуск: оба дня обработаны (processed)
    rep1 = derive_symbol("BTC-USDT", exchange="okx", processed_dir=str(tmp_path), on_progress=on_prog)
    assert rep1.ok is True
    assert len(rep1.results) == 2
    assert all(r.status == "processed" for r in rep1.results)
    assert "processed=2, skipped=0, failed=0" in rep1.summary()
    assert progress_records == [(1, 2), (2, 2)]

    # Проверяем, что созданы выходы для всех 3 типов датасетов
    for kind in ("l1", "bars_1s", "bars_1m"):
        for d in (d1, d2):
            out_file = tmp_path / "okx" / "BTC-USDT" / kind / f"year={d.year}" / f"month={d.month:02d}" / f"{d.isoformat()}.parquet"
            assert out_file.exists()

    # 2. Второй запуск: оба дня пропущены (skipped)
    rep2 = derive_symbol("BTC-USDT", exchange="okx", processed_dir=str(tmp_path))
    assert rep2.ok is True
    assert len(rep2.results) == 2
    assert all(r.status == "skipped" for r in rep2.results)
    assert "processed=0, skipped=2, failed=0" in rep2.summary()


def test_derive_symbol_force_and_event_modification(tmp_path: Path, monkeypatch):
    events_root = tmp_path / "okx" / "BTC-USDT" / "events"
    d1 = date(2024, 1, 1)
    ev_path = _create_event_day(events_root, d1)

    # 1. Начальный запуск
    rep = derive_symbol("BTC-USDT", exchange="okx", processed_dir=str(tmp_path))
    assert rep.results[0].status == "processed"

    # 2. force=True вызывает пересчет
    rep_force = derive_symbol("BTC-USDT", exchange="okx", processed_dir=str(tmp_path), force=True)
    assert rep_force.results[0].status == "processed"

    # 3. Изменение файла событий (перезаписываем с другими данными) вызывает пересчет без force
    ts_base = int(datetime(d1.year, d1.month, d1.day, 12, 0, 0, tzinfo=timezone.utc).timestamp() * NS)
    rows_extra = [
        {"ts": ts_base + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": ts_base + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": ts_base + 2 * NS, "event_type": TRADE, "side": BUY, "price": 100.5, "size": 0.5, "msg_id": 2, "level": 0},
        {"ts": ts_base + 3 * NS, "event_type": TRADE, "side": SELL, "price": 100.6, "size": 0.8, "msg_id": 3, "level": 0},
    ]
    table_extra = pa.table(
        {
            "ts": pa.array([r["ts"] for r in rows_extra], pa.int64()).cast(EVENT_SCHEMA.field("ts").type),
            "event_type": pa.array([r["event_type"] for r in rows_extra], pa.int8()),
            "side": pa.array([r["side"] for r in rows_extra], pa.int8()),
            "price": pa.array([r["price"] for r in rows_extra], pa.float64()),
            "size": pa.array([r["size"] for r in rows_extra], pa.float64()),
            "num_orders": pa.array([None] * len(rows_extra), pa.int32()),
            "msg_id": pa.array([r["msg_id"] for r in rows_extra], pa.int64()),
            "level": pa.array([r["level"] for r in rows_extra], pa.int16()),
        },
        schema=EVENT_SCHEMA,
    )
    pq.write_table(table_extra, ev_path)
    rep_modified = derive_symbol("BTC-USDT", exchange="okx", processed_dir=str(tmp_path))
    assert rep_modified.results[0].status == "processed"

    # 4. Изменение DERIVE_VERSION вызывает пересчет
    monkeypatch.setattr("src.derived.runner.DERIVE_VERSION", 999)
    rep_ver = derive_symbol("BTC-USDT", exchange="okx", processed_dir=str(tmp_path))
    assert rep_ver.results[0].status == "processed"


def test_derive_symbol_missing_output_recomputes(tmp_path: Path):
    events_root = tmp_path / "okx" / "BTC-USDT" / "events"
    d1 = date(2024, 1, 1)
    _create_event_day(events_root, d1)

    # Начальный запуск
    derive_symbol("BTC-USDT", exchange="okx", processed_dir=str(tmp_path))

    # Удаляем один из сгенерированных файлов
    bars_file = tmp_path / "okx" / "BTC-USDT" / "bars_1s" / "year=2024" / "month=01" / "2024-01-01.parquet"
    assert bars_file.exists()
    bars_file.unlink()

    # Повторный запуск без force должен обнаружить пропажу файла и пересчитать
    rep = derive_symbol("BTC-USDT", exchange="okx", processed_dir=str(tmp_path))
    assert rep.results[0].status == "processed"
    assert bars_file.exists()


def test_derive_symbol_isolation_of_day_failure(tmp_path: Path):
    events_root = tmp_path / "okx" / "BTC-USDT" / "events"
    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 2)
    _create_event_day(events_root, d1)

    # Создаем поврежденный файл за день 2
    bad_dest = events_root / f"year={d2.year}" / f"month={d2.month:02d}" / f"{d2.isoformat()}.parquet"
    bad_dest.parent.mkdir(parents=True, exist_ok=True)
    bad_dest.write_bytes(b"corrupt parquet data")

    rep = derive_symbol("BTC-USDT", exchange="okx", processed_dir=str(tmp_path))
    assert rep.ok is False
    assert len(rep.results) == 2
    assert rep.results[0].day == "2024-01-01"
    assert rep.results[0].status == "processed"
    assert rep.results[1].day == "2024-01-02"
    assert rep.results[1].status == "failed"
    assert rep.results[1].error is not None
    assert "failed=1" in rep.summary()


def test_derive_symbol_date_bounds(tmp_path: Path):
    events_root = tmp_path / "okx" / "BTC-USDT" / "events"
    _create_event_day(events_root, date(2024, 1, 1))
    _create_event_day(events_root, date(2024, 1, 2))
    _create_event_day(events_root, date(2024, 1, 3))

    rep = derive_symbol(
        "BTC-USDT",
        exchange="okx",
        processed_dir=str(tmp_path),
        start=date(2024, 1, 2),
        end=date(2024, 1, 2),
    )
    assert len(rep.results) == 1
    assert rep.results[0].day == "2024-01-02"


def test_derive_symbol_partitioned_paths(tmp_path: Path):
    events_root = tmp_path / "okx" / "BTC-USDT" / "events"
    d = date(2024, 5, 10)
    _create_event_day(events_root, d)

    rep = derive_symbol("BTC-USDT", exchange="okx", processed_dir=str(tmp_path))
    assert rep.ok is True

    # Проверяем структуру директорий year=2024/month=05
    for kind in ("l1", "bars_1s", "bars_1m"):
        target = tmp_path / "okx" / "BTC-USDT" / kind / "year=2024" / "month=05" / "2024-05-10.parquet"
        assert target.is_file()
