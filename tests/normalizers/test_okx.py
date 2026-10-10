import io
import json
from datetime import date, timedelta
from pathlib import Path
import tarfile
import zipfile

import pyarrow as pa
import pytest

from src.normalizers import get_normalizer
from src.normalizers.okx import OkxNormalizer
from src.normalizers.schema import BOOK_SNAPSHOT, BOOK_UPDATE, BUY, EVENT_SCHEMA, SELL, TRADE


def make_trades_zip(dest: Path, rows: list[dict]):
    """Создает компактный .zip с CSV для тестов trades OKX."""
    csv_header = "instrument_name,trade_id,side,price,size,created_time\n"
    csv_lines = [
        f"{r.get('instrument_name', 'BTC-USDT')},{r['trade_id']},{r['side']},{r['price']},{r['size']},{r['created_time']}\n"
        for r in rows
    ]
    csv_bytes = (csv_header + "".join(csv_lines)).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(dest.stem + ".csv", csv_bytes)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(buf.getvalue())


def make_orderbook_targz(dest: Path, messages: list[dict], extra_blank_lines: bool = False):
    """Создает компактный .tar.gz с NDJSON для тестов orderbook OKX."""
    ndjson_parts = []
    for m in messages:
        ndjson_parts.append(json.dumps(m) + "\n")
        if extra_blank_lines:
            ndjson_parts.append("   \n\n")

    data = "".join(ndjson_parts).encode("utf-8")
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tar:
        ti = tarfile.TarInfo(name=dest.stem.split(".")[0] + ".json")
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(tar_buf.getvalue())


def test_registry_get_normalizer():
    norm = get_normalizer("okx")
    assert isinstance(norm, OkxNormalizer)
    assert norm.exchange_name == "okx"

    norm_upper = get_normalizer("OKX")
    assert isinstance(norm_upper, OkxNormalizer)

    with pytest.raises(ValueError, match="Неизвестная биржа"):
        get_normalizer("binance")


def test_source_date_and_trade_dates():
    norm = OkxNormalizer()
    p_trades = Path("data/raw/okx/BTC-USDT/trades/BTC-USDT-trades-2024-01-05.zip")
    assert norm.source_date(p_trades) == date(2024, 1, 5)

    p_book = Path("data/raw/okx/BTC-USDT/orderbook/BTC-USDT-L2orderbook-400lv-2024-01-05.tar.gz")
    assert norm.source_date(p_book) == date(2024, 1, 5)

    with pytest.raises(ValueError, match="Не удалось извлечь дату"):
        norm.source_date(Path("invalid_filename.zip"))

    # Day D requires trades D and D+1
    d = date(2024, 1, 5)
    assert norm.trade_dates_for_utc_day(d) == [d, date(2024, 1, 6)]


def test_iter_trades_normalization(tmp_path: Path):
    trades_path = tmp_path / "BTC-USDT-trades-2024-01-01.zip"
    raw_rows = [
        {"trade_id": 101, "side": "buy", "price": 42000.5, "size": 0.25, "created_time": 1704067200000},
        {"trade_id": 102, "side": "sell", "price": 42001.0, "size": 1.50, "created_time": 1704067200100},
    ]
    make_trades_zip(trades_path, raw_rows)

    norm = OkxNormalizer()
    tables = list(norm.iter_trades(trades_path))
    assert len(tables) == 1
    t = tables[0]

    assert t.schema == EVENT_SCHEMA
    assert t.num_rows == 2

    # Verify columns
    assert t.column("event_type").to_pylist() == [TRADE, TRADE]
    assert t.column("side").to_pylist() == [BUY, SELL]
    assert t.column("price").to_pylist() == [42000.5, 42001.0]
    assert t.column("size").to_pylist() == [0.25, 1.50]
    assert t.column("num_orders").to_pylist() == [None, None]
    assert t.column("msg_id").to_pylist() == [101, 102]
    assert t.column("level").to_pylist() == [0, 0]

    # Verify timestamp converted from ms to ns
    expected_ts_ns = [1704067200000 * 1_000_000, 1704067200100 * 1_000_000]
    assert t.column("ts").cast(pa.int64()).to_pylist() == expected_ts_ns


def test_iter_orderbook_normalization(tmp_path: Path):
    book_path = tmp_path / "BTC-USDT-L2orderbook-400lv-2024-01-01.tar.gz"
    messages = [
        {
            "instId": "BTC-USDT",
            "action": "snapshot",
            "ts": 1704067200000,
            "bids": [["42000.0", "1.0", "5"], ["41999.0", "2.0", "8"]],
            "asks": [["42001.0", "1.5", "3"]],
        },
        {
            "instId": "BTC-USDT",
            "action": "update",
            "ts": 1704067200500,
            "bids": [["42000.0", "0.0", "0"]],  # size 0.0 -> deletion of level
            "asks": [["42002.0", "0.5", "1"]],
        },
    ]
    make_orderbook_targz(book_path, messages, extra_blank_lines=True)

    norm = OkxNormalizer()
    tables = list(norm.iter_orderbook(book_path))
    assert len(tables) == 1
    t = tables[0]

    assert t.schema == EVENT_SCHEMA
    # Message 1 has 2 bids + 1 ask = 3 rows
    # Message 2 has 1 bid + 1 ask = 2 rows
    # Total = 5 rows
    assert t.num_rows == 5

    # Message 1 checks
    # Row 0: snapshot bid
    assert t.column("event_type")[0].as_py() == BOOK_SNAPSHOT
    assert t.column("side")[0].as_py() == BUY
    assert t.column("price")[0].as_py() == 42000.0
    assert t.column("size")[0].as_py() == 1.0
    assert t.column("num_orders")[0].as_py() == 5
    assert t.column("msg_id")[0].as_py() == 0
    assert t.column("level")[0].as_py() == 0

    # Row 1: snapshot bid
    assert t.column("side")[1].as_py() == BUY
    assert t.column("price")[1].as_py() == 41999.0
    assert t.column("msg_id")[1].as_py() == 0
    assert t.column("level")[1].as_py() == 1

    # Row 2: snapshot ask
    assert t.column("side")[2].as_py() == SELL
    assert t.column("price")[2].as_py() == 42001.0
    assert t.column("msg_id")[2].as_py() == 0
    assert t.column("level")[2].as_py() == 2

    # Message 2 checks: update
    # Row 3: update bid with size 0.0
    assert t.column("event_type")[3].as_py() == BOOK_UPDATE
    assert t.column("side")[3].as_py() == BUY
    assert t.column("price")[3].as_py() == 42000.0
    assert t.column("size")[3].as_py() == 0.0
    assert t.column("num_orders")[3].as_py() == 0
    assert t.column("msg_id")[3].as_py() == 1
    assert t.column("level")[3].as_py() == 0

    # Row 4: update ask
    assert t.column("event_type")[4].as_py() == BOOK_UPDATE
    assert t.column("side")[4].as_py() == SELL
    assert t.column("price")[4].as_py() == 42002.0
    assert t.column("msg_id")[4].as_py() == 1
    assert t.column("level")[4].as_py() == 1


def test_batching_produces_identical_result(tmp_path: Path):
    book_path = tmp_path / "orderbook.tar.gz"
    messages = [
        {"instId": "BTC-USDT", "action": "snapshot", "ts": 1704067200000, "bids": [["100.0", "1.0", "1"]], "asks": []},
        {"instId": "BTC-USDT", "action": "update", "ts": 1704067200100, "bids": [["101.0", "2.0", "2"]], "asks": []},
        {"instId": "BTC-USDT", "action": "update", "ts": 1704067200200, "bids": [["102.0", "3.0", "3"]], "asks": []},
        {"instId": "BTC-USDT", "action": "update", "ts": 1704067200300, "bids": [["103.0", "4.0", "4"]], "asks": []},
    ]
    make_orderbook_targz(book_path, messages)

    norm_small = OkxNormalizer()
    norm_small.batch_rows = 2
    chunks_small = list(norm_small.iter_orderbook(book_path))
    assert len(chunks_small) == 2
    concatenated_small = pa.concat_tables(chunks_small)

    norm_large = OkxNormalizer()
    norm_large.batch_rows = 1000
    chunks_large = list(norm_large.iter_orderbook(book_path))
    assert len(chunks_large) == 1
    concatenated_large = chunks_large[0]

    assert concatenated_small.equals(concatenated_large)
