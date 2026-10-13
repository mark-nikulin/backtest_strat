from datetime import date, datetime, timezone
from pathlib import Path
from typing import List
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.derived.build import BAR_SCHEMA
from src.derived.runner import derive_symbol
from src.derived.trade_bars import (
    build_trade_bars_day,
    covered_utc_days,
    read_utc_day_trades,
    trade_source_days,
    trade_source_files,
)
from src.normalizers.schema import BOOK_SNAPSHOT, BUY, EVENT_SCHEMA, SELL, TRADE

NS = 1_000_000_000


def _write_trade_source_file(processed_dir: Path, symbol: str, source_day: date, trades: List[dict]):
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
            "side": pa.array([t["side"] for t in trades], pa.int8()),
            "price": pa.array([t["price"] for t in trades], pa.float64()),
            "size": pa.array([t["size"] for t in trades], pa.float64()),
            "msg_id": pa.array([t["msg_id"] for t in trades], pa.int64()),
        }
    )
    pq.write_table(table, dest)
    return dest


def test_covered_utc_days():
    # Source days: D and D+1 are needed for UTC day D
    days = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 4), date(2024, 1, 5)]
    cov = covered_utc_days(days)
    # 2024-01-01 is covered (has 01 and 02)
    # 2024-01-02 is NOT covered (missing 03)
    # 2024-01-04 is covered (has 04 and 05)
    # 2024-01-05 is NOT covered (missing 06)
    assert cov == [date(2024, 1, 1), date(2024, 1, 4)]


def test_read_utc_day_trades_and_boundaries(tmp_path: Path):
    d_target = date(2024, 1, 2)
    d_next = date(2024, 1, 3)

    t_before = int(datetime(2024, 1, 1, 23, 59, 59, tzinfo=timezone.utc).timestamp() * NS)
    t_start = int(datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc).timestamp() * NS)
    t_mid1 = int(datetime(2024, 1, 2, 8, 0, 0, tzinfo=timezone.utc).timestamp() * NS)
    t_mid2 = int(datetime(2024, 1, 2, 18, 0, 0, tzinfo=timezone.utc).timestamp() * NS)
    t_end = int(datetime(2024, 1, 3, 0, 0, 0, tzinfo=timezone.utc).timestamp() * NS)
    t_after = int(datetime(2024, 1, 3, 1, 0, 0, tzinfo=timezone.utc).timestamp() * NS)

    # Source file D: has trade before UTC day, at start, and mid1
    _write_trade_source_file(
        tmp_path,
        "BTC-USDT",
        d_target,
        [
            {"ts": t_before, "side": BUY, "price": 99.0, "size": 1.0, "msg_id": 10},
            {"ts": t_start, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 20},
            {"ts": t_mid1, "side": SELL, "price": 101.0, "size": 1.5, "msg_id": 30},
        ],
    )

    # Source file D+1: has mid2 (at 18:00 UTC of D), and trades at/after end of D
    # Add two trades with same timestamp to verify lexsort by (ts, msg_id)
    _write_trade_source_file(
        tmp_path,
        "BTC-USDT",
        d_next,
        [
            {"ts": t_mid2, "side": BUY, "price": 102.0, "size": 2.0, "msg_id": 50},
            {"ts": t_mid2, "side": SELL, "price": 102.5, "size": 0.5, "msg_id": 40},
            {"ts": t_end, "side": BUY, "price": 103.0, "size": 1.0, "msg_id": 60},
            {"ts": t_after, "side": SELL, "price": 104.0, "size": 1.0, "msg_id": 70},
        ],
    )

    res = read_utc_day_trades(str(tmp_path), "okx", "BTC-USDT", d_target)
    # Expected: t_start, t_mid1, and two trades at t_mid2 (total 4 trades)
    assert len(res["ts"]) == 4
    assert res["ts"][0] == t_start
    assert res["price"][0] == 100.0

    assert res["ts"][1] == t_mid1
    assert res["price"][1] == 101.0

    # At t_mid2: sorted by msg_id: msg_id 40 before msg_id 50
    assert res["ts"][2] == t_mid2
    assert res["price"][2] == 102.5  # msg_id 40

    assert res["ts"][3] == t_mid2
    assert res["price"][3] == 102.0  # msg_id 50


def test_read_utc_day_trades_empty(tmp_path: Path):
    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 2)
    # Write files with trades far outside 2024-01-01 UTC
    t_far = int(datetime(2024, 1, 5, 12, 0, 0, tzinfo=timezone.utc).timestamp() * NS)
    _write_trade_source_file(
        tmp_path, "BTC-USDT", d1, [{"ts": t_far, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1}]
    )
    _write_trade_source_file(
        tmp_path, "BTC-USDT", d2, [{"ts": t_far, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 2}]
    )

    res = read_utc_day_trades(str(tmp_path), "okx", "BTC-USDT", d1)
    assert len(res["ts"]) == 0
    assert len(res["price"]) == 0


def test_build_trade_bars_day(tmp_path: Path):
    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 2)
    t0 = int(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * NS)

    _write_trade_source_file(
        tmp_path,
        "BTC-USDT",
        d1,
        [
            {"ts": t0, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1},
            {"ts": t0 + 500_000_000, "side": SELL, "price": 101.0, "size": 2.0, "msg_id": 2},
        ],
    )
    _write_trade_source_file(tmp_path, "BTC-USDT", d2, [])

    bars_dict = build_trade_bars_day(str(tmp_path), "okx", "BTC-USDT", d1)
    assert set(bars_dict.keys()) == {"bars_1s", "bars_1m"}

    b1s = bars_dict["bars_1s"]
    assert b1s.schema == BAR_SCHEMA
    assert b1s.num_rows == 1
    assert b1s["open"][0].as_py() == 100.0
    assert b1s["close"][0].as_py() == 101.0
    assert b1s["volume"][0].as_py() == 3.0
    # L1 was not available, so mid_close and spread_close must be null
    assert b1s["mid_close"][0].as_py() is None
    assert b1s["spread_close"][0].as_py() is None


def test_derive_symbol_trades_only_and_priority(tmp_path: Path):
    # Case 1: Day 1 has events. Day 2 has trades only (covered by D2 and D3).
    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 2)
    d3 = date(2024, 1, 3)

    # 1. Create events for Day 1
    ev_path = (
        tmp_path / "okx" / "BTC-USDT" / "events" / "year=2024" / "month=01" / f"{d1.isoformat()}.parquet"
    )
    ev_path.parent.mkdir(parents=True, exist_ok=True)
    t1 = int(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * NS)
    pq.write_table(
        pa.table(
            {
                "ts": pa.array([t1, t1], pa.int64()).cast(EVENT_SCHEMA.field("ts").type),
                "event_type": pa.array([BOOK_SNAPSHOT, BOOK_SNAPSHOT], pa.int8()),
                "side": pa.array([BUY, SELL], pa.int8()),
                "price": pa.array([100.0, 101.0], pa.float64()),
                "size": pa.array([1.0, 1.0], pa.float64()),
                "num_orders": pa.array([None, None], pa.int32()),
                "msg_id": pa.array([1, 1], pa.int64()),
                "level": pa.array([0, 0], pa.int16()),
            },
            schema=EVENT_SCHEMA,
        ),
        ev_path,
    )

    # Also create trades for Day 1 and Day 2 (Day 1 has events, so events must have priority)
    _write_trade_source_file(
        tmp_path, "BTC-USDT", d1, [{"ts": t1, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 1}]
    )
    t2 = int(datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc).timestamp() * NS)
    _write_trade_source_file(
        tmp_path, "BTC-USDT", d2, [{"ts": t2, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 2}]
    )
    t3 = int(datetime(2024, 1, 3, 12, 0, 0, tzinfo=timezone.utc).timestamp() * NS)
    f3 = _write_trade_source_file(
        tmp_path, "BTC-USDT", d3, [{"ts": t3, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 3}]
    )

    # 1st run of derive_symbol:
    rep = derive_symbol("BTC-USDT", exchange="okx", processed_dir=str(tmp_path))
    assert rep.ok is True
    assert len(rep.results) == 2
    # Day 1 built from events (has l1, bars_1s, bars_1m)
    assert rep.results[0].day == "2024-01-01"
    assert rep.results[0].status == "processed"
    assert "l1" in rep.results[0].rows

    # Day 2 built from trades (has bars_1s, bars_1m, NO l1)
    assert rep.results[1].day == "2024-01-02"
    assert rep.results[1].status == "processed"
    assert "bars_1s" in rep.results[1].rows
    assert "l1" not in rep.results[1].rows

    # 2nd run: both days are skipped (idempotent)
    rep2 = derive_symbol("BTC-USDT", exchange="okx", processed_dir=str(tmp_path))
    assert rep2.ok is True
    assert all(r.status == "skipped" for r in rep2.results)

    # Modifying one of Day 2's source files triggers recomputation of Day 2
    _write_trade_source_file(
        tmp_path,
        "BTC-USDT",
        d3,
        [
            {"ts": t3, "side": BUY, "price": 100.0, "size": 1.0, "msg_id": 3},
            {"ts": t3 + 1, "side": SELL, "price": 101.0, "size": 2.0, "msg_id": 4},
        ],
    )
    rep3 = derive_symbol("BTC-USDT", exchange="okx", processed_dir=str(tmp_path))
    assert rep3.results[0].status == "skipped"   # Day 1 remains skipped
    assert rep3.results[1].status == "processed" # Day 2 recomputed
