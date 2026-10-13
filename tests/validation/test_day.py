from datetime import datetime, timezone
from pathlib import Path
from typing import List
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backtest_strat.normalizers.schema import BOOK_SNAPSHOT, BOOK_UPDATE, BUY, EVENT_SCHEMA, SELL, TRADE
from backtest_strat.validation.day import FAIL, OK, WARN, Thresholds, validate_events_file

NS = 1_000_000_000


def _day_start_ns(day_str: str) -> int:
    return int(datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * NS)


def _write_parquet(dest: Path, rows: List[dict], batch_size: int = 500_000):
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


def test_clean_day(tmp_path: Path):
    day_str = "2024-01-01"
    start_ns = _day_start_ns(day_str)
    file_path = tmp_path / f"{day_str}.parquet"

    rows = [
        # Snapshot in first seconds
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1, "level": 1},
        # Normal trade inside spread
        {"ts": start_ns + 5 * NS, "event_type": TRADE, "side": BUY, "price": 100.5, "size": 0.5, "msg_id": 2},
        # Update 10s later
        {"ts": start_ns + 15 * NS, "event_type": BOOK_UPDATE, "side": BUY, "price": 100.2, "size": 1.5, "msg_id": 3},
    ]
    _write_parquet(file_path, rows)

    rep = validate_events_file(file_path)
    assert rep.status == OK
    assert rep.events == 4
    assert rep.book_messages == 2
    assert rep.trades == 1
    assert rep.snapshots == 1
    assert all(c["status"] == OK for c in rep.checks)


def test_crossed_book_fails(tmp_path: Path):
    day_str = "2024-01-01"
    start_ns = _day_start_ns(day_str)
    file_path = tmp_path / f"{day_str}.parquet"

    # Bid 102.0 >= Ask 100.0
    rows = [
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 102.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 100.0, "size": 1.0, "msg_id": 1, "level": 1},
    ]
    _write_parquet(file_path, rows)

    rep = validate_events_file(file_path)
    assert rep.status == FAIL
    check_cb = next(c for c in rep.checks if c["name"] == "crossed_book")
    assert check_cb["status"] == FAIL
    assert check_cb["value"] == 1


def test_book_gap_warn_and_fail(tmp_path: Path):
    day_str = "2024-01-01"
    start_ns = _day_start_ns(day_str)

    # 1. Gap > 30s but < 300s -> WARN
    rows_warn = [
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1},
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1},
        {"ts": start_ns + 50 * NS, "event_type": BOOK_UPDATE, "side": BUY, "price": 100.1, "size": 1.0, "msg_id": 2},
    ]
    p_warn_dir = tmp_path / "warn"
    p_warn_dir.mkdir()
    p_warn = p_warn_dir / f"{day_str}.parquet"
    _write_parquet(p_warn, rows_warn)
    rep_warn = validate_events_file(p_warn, Thresholds(gap_warn_s=30.0, gap_fail_s=300.0))
    check_warn = next(c for c in rep_warn.checks if c["name"] == "book_gap")
    assert check_warn["status"] == WARN
    assert rep_warn.status == WARN

    # 2. Gap > 300s -> FAIL
    rows_fail = [
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1},
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1},
        {"ts": start_ns + 400 * NS, "event_type": BOOK_UPDATE, "side": BUY, "price": 100.1, "size": 1.0, "msg_id": 2},
    ]
    p_fail_dir = tmp_path / "fail"
    p_fail_dir.mkdir()
    p_fail = p_fail_dir / f"{day_str}.parquet"
    _write_parquet(p_fail, rows_fail)
    rep_fail = validate_events_file(p_fail, Thresholds(gap_warn_s=30.0, gap_fail_s=300.0))
    check_fail = next(c for c in rep_fail.checks if c["name"] == "book_gap")
    assert check_fail["status"] == FAIL
    assert rep_fail.status == FAIL


def test_trade_outside_book_warn(tmp_path: Path):
    day_str = "2024-01-01"
    start_ns = _day_start_ns(day_str)
    file_path = tmp_path / f"{day_str}.parquet"

    # Best bid = 100.0, best ask = 101.0
    # Trade BUY at 95.0 (below best bid)
    rows = [
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1, "level": 1},
        {"ts": start_ns + 2 * NS, "event_type": TRADE, "side": BUY, "price": 95.0, "size": 0.5, "msg_id": 2},
    ]
    _write_parquet(file_path, rows)

    rep = validate_events_file(file_path)
    check_outside = next(c for c in rep.checks if c["name"] == "trade_vs_book")
    assert check_outside["status"] == WARN
    assert check_outside["value"] == 1.0  # 100% outside


def test_non_positive_values_fail(tmp_path: Path):
    day_str = "2024-01-01"
    start_ns = _day_start_ns(day_str)

    # 1. Trade with price <= 0
    p1_dir = tmp_path / "bad_px"
    p1_dir.mkdir()
    p1 = p1_dir / f"{day_str}.parquet"
    _write_parquet(p1, [
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1},
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1},
        {"ts": start_ns + 2 * NS, "event_type": TRADE, "side": BUY, "price": 0.0, "size": 1.0, "msg_id": 2},
    ])
    rep1 = validate_events_file(p1)
    assert next(c for c in rep1.checks if c["name"] == "non_positive")["status"] == FAIL

    # 2. Book with negative size
    p2_dir = tmp_path / "neg_sz"
    p2_dir.mkdir()
    p2 = p2_dir / f"{day_str}.parquet"
    _write_parquet(p2, [
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": -5.0, "msg_id": 1},
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1},
    ])
    rep2 = validate_events_file(p2)
    assert next(c for c in rep2.checks if c["name"] == "non_positive")["status"] == FAIL


def test_price_spike_warn(tmp_path: Path):
    day_str = "2024-01-01"
    start_ns = _day_start_ns(day_str)
    file_path = tmp_path / f"{day_str}.parquet"

    # t=1s: msg_id=1, mid = 100.0
    # t=2s: msg_id=2 (samples mid 100.0), sets mid to 101.0 (+100 bps > 50 bps)
    # t=3s: msg_id=3 (samples mid 101.0, flags spike), sets mid to 100.05 (reversal = 0.95 / 1.0 = 95% >= 80%)
    # t=5s: msg_id=4 (samples mid 100.05, confirms reversal and increments spike count)
    rows = [
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 99.9, "size": 1.0, "msg_id": 1},
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 100.1, "size": 1.0, "msg_id": 1},

        {"ts": start_ns + 2 * NS, "event_type": BOOK_UPDATE, "side": BUY, "price": 99.9, "size": 0.0, "msg_id": 2},
        {"ts": start_ns + 2 * NS, "event_type": BOOK_UPDATE, "side": BUY, "price": 100.9, "size": 1.0, "msg_id": 2},
        {"ts": start_ns + 2 * NS, "event_type": BOOK_UPDATE, "side": SELL, "price": 100.1, "size": 0.0, "msg_id": 2},
        {"ts": start_ns + 2 * NS, "event_type": BOOK_UPDATE, "side": SELL, "price": 101.1, "size": 1.0, "msg_id": 2},

        {"ts": start_ns + 3 * NS, "event_type": BOOK_UPDATE, "side": BUY, "price": 100.9, "size": 0.0, "msg_id": 3},
        {"ts": start_ns + 3 * NS, "event_type": BOOK_UPDATE, "side": BUY, "price": 99.95, "size": 1.0, "msg_id": 3},
        {"ts": start_ns + 3 * NS, "event_type": BOOK_UPDATE, "side": SELL, "price": 101.1, "size": 0.0, "msg_id": 3},
        {"ts": start_ns + 3 * NS, "event_type": BOOK_UPDATE, "side": SELL, "price": 100.15, "size": 1.0, "msg_id": 3},

        {"ts": start_ns + 5 * NS, "event_type": BOOK_UPDATE, "side": BUY, "price": 99.95, "size": 1.0, "msg_id": 4},
        {"ts": start_ns + 5 * NS, "event_type": BOOK_UPDATE, "side": SELL, "price": 100.15, "size": 1.0, "msg_id": 4},
    ]
    _write_parquet(file_path, rows)

    rep = validate_events_file(file_path)
    check_spike = next(c for c in rep.checks if c["name"] == "price_spike")
    assert check_spike["status"] == WARN
    assert check_spike["value"] == 1


def test_no_initial_book_scenarios(tmp_path: Path):
    day_str = "2024-01-01"
    start_ns = _day_start_ns(day_str)

    # 1. Day starts with snapshot within 60s -> OK
    p_ok_dir = tmp_path / "ok"
    p_ok_dir.mkdir()
    p_ok = p_ok_dir / f"{day_str}.parquet"
    _write_parquet(p_ok, [
        {"ts": start_ns + 5 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1},
        {"ts": start_ns + 5 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1},
    ])
    rep_ok = validate_events_file(p_ok)
    assert next(c for c in rep_ok.checks if c["name"] == "no_initial_book")["status"] == OK

    # 2. Day starts with updates, but snapshot arrives within 120s -> WARN with time
    p_warn_dir = tmp_path / "warn"
    p_warn_dir.mkdir()
    p_warn = p_warn_dir / f"{day_str}.parquet"
    _write_parquet(p_warn, [
        {"ts": start_ns + 5 * NS, "event_type": BOOK_UPDATE, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1},
        {"ts": start_ns + 5 * NS, "event_type": BOOK_UPDATE, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1},
        {"ts": start_ns + 70 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 2},
        {"ts": start_ns + 70 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 2},
    ])
    rep_warn = validate_events_file(p_warn)
    check_warn = next(c for c in rep_warn.checks if c["name"] == "no_initial_book")
    assert check_warn["status"] == WARN
    assert "первый снапшот в" in check_warn["detail"]

    # 3. Day has no snapshot at all -> FAIL
    p_fail_dir = tmp_path / "fail"
    p_fail_dir.mkdir()
    p_fail = p_fail_dir / f"{day_str}.parquet"
    _write_parquet(p_fail, [
        {"ts": start_ns + 5 * NS, "event_type": BOOK_UPDATE, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1},
        {"ts": start_ns + 5 * NS, "event_type": BOOK_UPDATE, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1},
    ])
    rep_fail = validate_events_file(p_fail)
    check_fail = next(c for c in rep_fail.checks if c["name"] == "no_initial_book")
    assert check_fail["status"] == FAIL
    assert "нет ни одного снапшота" in check_fail["detail"]


def test_split_message_counted_as_one(tmp_path: Path):
    day_str = "2024-01-01"
    start_ns = _day_start_ns(day_str)
    file_path = tmp_path / f"{day_str}.parquet"

    # Один снапшот msg_id=1, но 2 уровня
    rows = [
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1, "level": 0},
        {"ts": start_ns + 1 * NS, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 101.0, "size": 1.0, "msg_id": 1, "level": 1},
    ]
    # Пишем с batch_size=1 (каждая строка в отдельном row group)
    _write_parquet(file_path, rows, batch_size=1)

    rep = validate_events_file(file_path)
    assert rep.events == 2
    assert rep.book_messages == 1
    assert rep.snapshots == 1
