from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.validation.day import FAIL, OK, WARN
from src.validation.runner import validate_symbol
from src.validation.trades import TradeThresholds, validate_trade_day

NS = 1_000_000_000


def _write_trade_source(processed_dir: Path, symbol: str, source_day: date, trades: List[dict]):
    dest = (
        processed_dir
        / "okx"
        / symbol
        / "trades"
        / f"year={source_day.year}"
        / f"month={source_day.month:02d}"
        / f"{symbol}-trades-{source_day.isoformat()}.parquet"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "ts": pa.array([t["ts"] for t in trades], pa.int64()),
            "side": pa.array([t.get("side", 1) for t in trades], pa.int8()),
            "price": pa.array([t["price"] for t in trades], pa.float64()),
            "size": pa.array([t["size"] for t in trades], pa.float64()),
            "msg_id": pa.array([t["msg_id"] for t in trades], pa.int64()),
        }
    )
    pq.write_table(table, dest)
    return dest


def test_validate_trade_day_no_trades(tmp_path: Path):
    d = date(2024, 1, 1)
    _write_trade_source(tmp_path, "BTC-USDT", d, [])
    _write_trade_source(tmp_path, "BTC-USDT", d + timedelta(days=1), [])

    rep = validate_trade_day(str(tmp_path), "okx", "BTC-USDT", d)
    assert rep.status == FAIL
    check = next(c for c in rep.checks if c["name"] == "no_trades")
    assert check["status"] == FAIL


def test_validate_trade_day_duplicate_ids(tmp_path: Path):
    d = date(2024, 1, 1)
    t0 = int(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * NS)
    # 2 trades with duplicate msg_id = 999
    trades = [
        {"ts": t0, "price": 100.0, "size": 1.0, "msg_id": 999},
        {"ts": t0 + NS, "price": 100.5, "size": 1.0, "msg_id": 999},
    ]
    _write_trade_source(tmp_path, "BTC-USDT", d, trades)
    _write_trade_source(tmp_path, "BTC-USDT", d + timedelta(days=1), [])

    rep = validate_trade_day(str(tmp_path), "okx", "BTC-USDT", d)
    assert rep.status == FAIL
    check = next(c for c in rep.checks if c["name"] == "duplicate_ids")
    assert check["status"] == FAIL
    assert check["value"] == 1


def test_validate_trade_day_non_positive(tmp_path: Path):
    d = date(2024, 1, 1)
    t0 = int(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * NS)
    trades = [
        {"ts": t0, "price": -100.0, "size": 1.0, "msg_id": 1},
        {"ts": t0 + NS, "price": 100.0, "size": 0.0, "msg_id": 2},
    ]
    _write_trade_source(tmp_path, "BTC-USDT", d, trades)
    _write_trade_source(tmp_path, "BTC-USDT", d + timedelta(days=1), [])

    rep = validate_trade_day(str(tmp_path), "okx", "BTC-USDT", d)
    assert rep.status == FAIL
    check = next(c for c in rep.checks if c["name"] == "non_positive")
    assert check["status"] == FAIL
    assert check["value"] == 2


def test_validate_trade_day_gaps_warn_and_fail(tmp_path: Path):
    d = date(2024, 1, 1)
    lo = int(datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp() * NS)

    # 1. Gap > 1h (e.g. 2h pause) -> WARN
    trades_warn = [
        {"ts": lo + 10 * NS, "price": 100.0, "size": 1.0, "msg_id": 1},
        {"ts": lo + 7210 * NS, "price": 100.0, "size": 1.0, "msg_id": 2},  # 7200s gap = 2h
    ]
    _write_trade_source(tmp_path / "w", "BTC-USDT", d, trades_warn)
    _write_trade_source(tmp_path / "w", "BTC-USDT", d + timedelta(days=1), [])

    rep_warn = validate_trade_day(str(tmp_path / "w"), "okx", "BTC-USDT", d)
    check_warn = next(c for c in rep_warn.checks if c["name"] == "trade_gap")
    assert check_warn["status"] in (WARN, FAIL)
    assert check_warn["value"] >= 7200

    # 2. Gap > 6h (e.g. gap from last trade to end of day is 8 hours) -> FAIL
    # Last trade is at 15:00 UTC -> gap to 24:00 is 9 hours
    t_15h = int(datetime(2024, 1, 1, 15, 0, 0, tzinfo=timezone.utc).timestamp() * NS)
    trades_fail = [
        {"ts": lo + 100 * NS, "price": 100.0, "size": 1.0, "msg_id": 1},
        {"ts": t_15h, "price": 100.0, "size": 1.0, "msg_id": 2},
    ]
    _write_trade_source(tmp_path / "f", "BTC-USDT", d, trades_fail)
    _write_trade_source(tmp_path / "f", "BTC-USDT", d + timedelta(days=1), [])

    rep_fail = validate_trade_day(str(tmp_path / "f"), "okx", "BTC-USDT", d)
    check_fail = next(c for c in rep_fail.checks if c["name"] == "trade_gap")
    assert check_fail["status"] == FAIL
    assert check_fail["value"] >= 6 * 3600


def test_validate_trade_day_price_spike_warn(tmp_path: Path):
    d = date(2024, 1, 1)
    lo = int(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * NS)

    # Minute 0: price = 100.0
    # Minute 1: price jumps to 110.0 (+10% >= 5%)
    # Minute 3: price returns to 101.0 (reversal: (110 - 101)/(110 - 100) = 9/10 = 90% >= 80%)
    trades = [
        {"ts": lo + 10 * NS, "price": 100.0, "size": 1.0, "msg_id": 1},
        {"ts": lo + 70 * NS, "price": 110.0, "size": 1.0, "msg_id": 2},
        {"ts": lo + 190 * NS, "price": 101.0, "size": 1.0, "msg_id": 3},
    ]
    _write_trade_source(tmp_path, "BTC-USDT", d, trades)
    _write_trade_source(tmp_path, "BTC-USDT", d + timedelta(days=1), [])

    rep = validate_trade_day(str(tmp_path), "okx", "BTC-USDT", d)
    check = next(c for c in rep.checks if c["name"] == "price_spike")
    assert check["status"] == WARN
    assert check["value"] == 1


def test_validate_trade_day_low_coverage_warn(tmp_path: Path):
    d = date(2024, 1, 1)
    lo = int(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * NS)
    # Only 2 distinct minutes with trades (< 25% of 1440 minutes)
    trades = [
        {"ts": lo, "price": 100.0, "size": 1.0, "msg_id": 1},
        {"ts": lo + 60 * NS, "price": 100.0, "size": 1.0, "msg_id": 2},
    ]
    _write_trade_source(tmp_path, "BTC-USDT", d, trades)
    _write_trade_source(tmp_path, "BTC-USDT", d + timedelta(days=1), [])

    rep = validate_trade_day(str(tmp_path), "okx", "BTC-USDT", d)
    check = next(c for c in rep.checks if c["name"] == "day_coverage")
    assert check["status"] == WARN
    assert check["value"] < 0.25


def test_validate_trade_day_clean_ok(tmp_path: Path):
    d = date(2024, 1, 1)
    lo = int(datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp() * NS)

    # 400 active minutes spread across 24h (400/1440 = 27.7% >= 25%)
    # max gap between trades is 200 seconds (< 3600s)
    trades = []
    # 1 trade every 200 seconds for all 86400s
    for i, t_offset in enumerate(range(10, 86400 - 10, 200)):
        trades.append({"ts": lo + t_offset * NS, "price": 100.0, "size": 1.0, "msg_id": i + 1})

    _write_trade_source(tmp_path, "BTC-USDT", d, trades)
    _write_trade_source(tmp_path, "BTC-USDT", d + timedelta(days=1), [])

    rep = validate_trade_day(str(tmp_path), "okx", "BTC-USDT", d)
    assert rep.status == OK
    assert all(c["status"] == OK for c in rep.checks)


def test_validate_symbol_trade_only(tmp_path: Path):
    # Setup 3 source days: D1, D2, D4 (D3 missing)
    # D1 is covered (has D1 and D2)
    # D2 is not covered (missing D3)
    # D4 is covered by D5
    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 2)
    d4 = date(2024, 1, 4)
    d5 = date(2024, 1, 5)

    lo1 = int(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * NS)
    lo4 = int(datetime(2024, 1, 4, 12, 0, 0, tzinfo=timezone.utc).timestamp() * NS)

    _write_trade_source(tmp_path, "BTC-USDT", d1, [{"ts": lo1, "price": 100.0, "size": 1.0, "msg_id": 1}])
    _write_trade_source(tmp_path, "BTC-USDT", d2, [])
    _write_trade_source(tmp_path, "BTC-USDT", d4, [{"ts": lo4, "price": 100.0, "size": 1.0, "msg_id": 2}])
    _write_trade_source(tmp_path, "BTC-USDT", d5, [])

    # 1st run of validate_symbol:
    summary1 = validate_symbol("BTC-USDT", exchange="okx", processed_dir=str(tmp_path))
    assert len(summary1.days) == 2
    assert summary1.days[0]["day"] == "2024-01-01"
    assert summary1.days[0]["from_cache"] is False
    assert summary1.days[1]["day"] == "2024-01-04"
    assert summary1.days[1]["from_cache"] is False

    # missing_days considers range [2024-01-01, 2024-01-04] -> missing 02 and 03
    assert "2024-01-02" in summary1.missing_days
    assert "2024-01-03" in summary1.missing_days

    # 2nd run: reports are loaded from cache
    summary2 = validate_symbol("BTC-USDT", exchange="okx", processed_dir=str(tmp_path))
    assert all(d["from_cache"] is True for d in summary2.days)
