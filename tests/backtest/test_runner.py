from datetime import date, datetime, timezone
from pathlib import Path
import sys
from unittest.mock import patch
import pyarrow as pa
import pyarrow.parquet as pq

from scripts.run_backtest import main
from src.normalizers.schema import BOOK_SNAPSHOT, BOOK_UPDATE, BUY, EVENT_SCHEMA, SELL, TRADE


def make_events_parquet(dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    ts_base = int(datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000)

    # Несколько событий: валидный снимок книги + сделки
    rows = [
        # 1. Снимок книги
        {"ts": ts_base, "event_type": BOOK_SNAPSHOT, "side": BUY, "price": 40000.0, "size": 1.0, "num_orders": 1, "msg_id": 1, "level": 0},
        {"ts": ts_base, "event_type": BOOK_SNAPSHOT, "side": SELL, "price": 40001.0, "size": 1.0, "num_orders": 1, "msg_id": 1, "level": 1},
        # 2. Сделка
        {"ts": ts_base + 10_000_000, "event_type": TRADE, "side": SELL, "price": 40000.0, "size": 0.5, "num_orders": None, "msg_id": 2, "level": 0},
        # 3. Апдейт книги
        {"ts": ts_base + 20_000_000, "event_type": BOOK_UPDATE, "side": BUY, "price": 40000.0, "size": 2.0, "num_orders": 2, "msg_id": 3, "level": 0},
    ]

    table = pa.table(
        {
            "ts": pa.array([r["ts"] for r in rows], pa.int64()).cast(EVENT_SCHEMA.field("ts").type),
            "event_type": pa.array([r["event_type"] for r in rows], pa.int8()),
            "side": pa.array([r["side"] for r in rows], pa.int8()),
            "price": pa.array([r["price"] for r in rows], pa.float64()),
            "size": pa.array([r["size"] for r in rows], pa.float64()),
            "num_orders": pa.array([r["num_orders"] for r in rows], pa.int32()),
            "msg_id": pa.array([r["msg_id"] for r in rows], pa.int64()),
            "level": pa.array([r["level"] for r in rows], pa.int16()),
        },
        schema=EVENT_SCHEMA,
    )
    pq.write_table(table, dest)


def test_run_backtest_cli_smoke(tmp_path: Path, capsys):
    processed_dir = tmp_path / "processed"
    events_file = processed_dir / "okx" / "BTC-USDT" / "events" / "year=2024" / "month=01" / "2024-01-01.parquet"
    make_events_parquet(events_file)

    csv_path = tmp_path / "fills.csv"

    argv = [
        "scripts/run_backtest.py",
        "--strategy", "src.backtest.examples:TouchMarketMaker",
        "--param", "size=0.01",
        "--param", "max_position=0.05",
        "--symbol", "BTC-USDT",
        "--start", "2024-01-01",
        "--end", "2024-01-01",
        "--tick", "0.1",
        "--lot", "0.001",
        "--processed-dir", str(processed_dir),
        "--fills-csv", str(csv_path),
    ]

    with patch.object(sys, "argv", argv):
        code = main()

    assert code == 0
    out = capsys.readouterr().out
    assert "total_pnl" in out
    assert "events" in out
    assert csv_path.exists()
    assert "ts,order_id,side,price,amount,is_maker,fee,tag" in csv_path.read_text()
