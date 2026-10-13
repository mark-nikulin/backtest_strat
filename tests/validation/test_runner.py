from datetime import date
from pathlib import Path
import json
import pytest

from src.validation.day import FAIL, OK, WARN
from src.validation.runner import load_reports, validate_symbol
from tests.validation.test_day import _day_start_ns, _write_parquet, NS
from src.normalizers.schema import BOOK_SNAPSHOT, BUY, SELL


def _create_sample_day_parquet(events_root: Path, d: date):
    p = events_root / f"year={d.year}" / f"month={d.month:02d}" / f"{d.isoformat()}.parquet"
    start_ns = _day_start_ns(d.isoformat())
    rows = [
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1, "level": 1},
    ]
    _write_parquet(p, rows)
    return p


def test_validate_symbol_idempotency_and_force(tmp_path: Path):
    proc_dir = tmp_path / "processed"
    events_root = proc_dir / "okx" / "BTC-USDT" / "events"

    d1 = date(2024, 1, 1)
    p1 = _create_sample_day_parquet(events_root, d1)

    # 1. Первый запуск: вычисляет отчет (from_cache=False)
    summary1 = validate_symbol("BTC-USDT", exchange="okx", processed_dir=str(proc_dir))
    assert summary1.ok
    assert len(summary1.days) == 1
    assert summary1.days[0]["from_cache"] is False

    # 2. Второй запуск: читает из кэша (from_cache=True)
    summary2 = validate_symbol("BTC-USDT", exchange="okx", processed_dir=str(proc_dir))
    assert summary2.days[0]["from_cache"] is True

    # 3. force=True -> пересчет
    summary_force = validate_symbol("BTC-USDT", exchange="okx", processed_dir=str(proc_dir), force=True)
    assert summary_force.days[0]["from_cache"] is False

    # 4. Изменение размера файла событий -> пересчет
    start_ns = _day_start_ns(d1.isoformat())
    rows_extra = [
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1, "level": 1},
        {"ts": start_ns + 2 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 99.0, "size": 2.0, "msg_id": 1, "level": 2},
    ]
    _write_parquet(p1, rows_extra)
    summary_mod = validate_symbol("BTC-USDT", exchange="okx", processed_dir=str(proc_dir))
    assert summary_mod.days[0]["from_cache"] is False


def test_validate_symbol_corrupt_json_recomputes(tmp_path: Path):
    proc_dir = tmp_path / "processed"
    events_root = proc_dir / "okx" / "BTC-USDT" / "events"
    d1 = date(2024, 1, 1)
    _create_sample_day_parquet(events_root, d1)

    # Запускаем валидацию
    validate_symbol("BTC-USDT", exchange="okx", processed_dir=str(proc_dir))
    report_file = proc_dir / "okx" / "BTC-USDT" / "validation" / f"{d1.isoformat()}.json"
    assert report_file.exists()

    # Повреждаем JSON файл отчета
    report_file.write_text("corrupted json {")

    # Должен пересчитать без исключения
    summary = validate_symbol("BTC-USDT", exchange="okx", processed_dir=str(proc_dir))
    assert summary.days[0]["from_cache"] is False


def test_validate_symbol_corrupt_events_file(tmp_path: Path):
    proc_dir = tmp_path / "processed"
    events_root = proc_dir / "okx" / "BTC-USDT" / "events"

    # Создаем 2 дня: 1-й валидный, 2-й битый
    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 2)
    _create_sample_day_parquet(events_root, d1)

    p2 = events_root / f"year={d2.year}" / f"month={d2.month:02d}" / f"{d2.isoformat()}.parquet"
    p2.parent.mkdir(parents=True, exist_ok=True)
    p2.write_bytes(b"corrupted parquet bytes")

    summary = validate_symbol("BTC-USDT", exchange="okx", processed_dir=str(proc_dir))
    assert not summary.ok
    assert len(summary.days) == 2

    d1_rep = next(d for d in summary.days if d["day"] == d1.isoformat())
    d2_rep = next(d for d in summary.days if d["day"] == d2.isoformat())

    assert d1_rep["status"] == OK
    assert d2_rep["status"] == FAIL
    assert any(c["name"] == "validator_error" for c in d2_rep["checks"])


def test_validate_symbol_missing_days_and_date_bounds(tmp_path: Path):
    proc_dir = tmp_path / "processed"
    events_root = proc_dir / "okx" / "BTC-USDT" / "events"

    d1 = date(2024, 1, 1)
    d3 = date(2024, 1, 3)
    _create_sample_day_parquet(events_root, d1)
    _create_sample_day_parquet(events_root, d3)

    # День 2 пропущен -> missing_days содержит 2024-01-02
    summary = validate_symbol("BTC-USDT", exchange="okx", processed_dir=str(proc_dir))
    assert summary.missing_days == ["2024-01-02"]
    assert summary.status == FAIL  # missing_days переводит статус символа в FAIL

    # С границами start=2024-01-03, end=2024-01-03 валидируется только день 3
    summary_filtered = validate_symbol(
        "BTC-USDT", exchange="okx", processed_dir=str(proc_dir), start=d3, end=d3
    )
    assert len(summary_filtered.days) == 1
    assert summary_filtered.days[0]["day"] == d3.isoformat()


def test_load_reports(tmp_path: Path):
    proc_dir = tmp_path / "processed"
    events_root = proc_dir / "okx" / "BTC-USDT" / "events"
    d1 = date(2024, 1, 1)
    _create_sample_day_parquet(events_root, d1)

    validate_symbol("BTC-USDT", exchange="okx", processed_dir=str(proc_dir))

    loaded = load_reports("okx", "BTC-USDT", processed_dir=str(proc_dir))
    assert len(loaded) == 1
    assert loaded[0]["day"] == d1.isoformat()
