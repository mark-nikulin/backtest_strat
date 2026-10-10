import json
import re
import tarfile
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator, List

import numpy as np
import pandas as pd
import pyarrow as pa

from src.normalizers.base import BaseNormalizer
from src.normalizers.schema import BOOK_SNAPSHOT, BOOK_UPDATE, BUY, EVENT_SCHEMA, SELL, TRADE

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_MS_TO_NS = 1_000_000


class OkxNormalizer(BaseNormalizer):
    """
    Форматы сырых архивов OKX — см. docs/okx_historical_data.md.

    trades:    zip -> CSV (instrument_name, trade_id, side, price, size, created_time[ms]).
               Сутки файла — по UTC+8: архив за X покрывает [X-1 16:00, X 16:00) UTC.
    orderbook: tar.gz -> NDJSON {instId, action, ts[ms], asks, bids}, уровни [price, size, num_orders].
               Сутки файла — по UTC. Снапшот каждую минуту, между ними диффы.
    """

    def __init__(self, raw_dir: str = "data/raw", processed_dir: str = "data/processed"):
        super().__init__("okx", raw_dir=raw_dir, processed_dir=processed_dir)

    def source_date(self, path: Path) -> date:
        m = _DATE_RE.search(path.name)
        if not m:
            raise ValueError(f"Не удалось извлечь дату из имени файла: {path.name}")
        return date.fromisoformat(m.group(1))

    def trade_dates_for_utc_day(self, day: date) -> List[date]:
        return [day, day + timedelta(days=1)]

    def iter_trades(self, path: Path) -> Iterator[pa.Table]:
        chunks = pd.read_csv(
            path,
            compression="zip",
            chunksize=self.batch_rows,
            dtype={"trade_id": "int64", "side": "string", "price": "float64", "size": "float64", "created_time": "int64"},
            usecols=["trade_id", "side", "price", "size", "created_time"],
        )
        for df in chunks:
            n = len(df)
            yield pa.table(
                {
                    "ts": pa.array(df["created_time"].to_numpy() * _MS_TO_NS, pa.int64()).cast(EVENT_SCHEMA.field("ts").type),
                    "event_type": pa.array(np.full(n, TRADE, np.int8)),
                    "side": pa.array(np.where(df["side"].to_numpy() == "buy", BUY, SELL).astype(np.int8)),
                    "price": pa.array(df["price"].to_numpy()),
                    "size": pa.array(df["size"].to_numpy()),
                    "num_orders": pa.nulls(n, pa.int32()),
                    "msg_id": pa.array(df["trade_id"].to_numpy()),
                    "level": pa.array(np.zeros(n, np.int16)),
                },
                schema=EVENT_SCHEMA,
            )

    def iter_orderbook(self, path: Path) -> Iterator[pa.Table]:
        cols = _BookColumns()
        msg_id = 0
        # r|gz — потоковое чтение: архив не распаковывается на диск и не читается в память целиком
        with tarfile.open(path, "r|gz") as tar:
            for member in tar:
                fh = tar.extractfile(member)
                if fh is None:
                    continue
                for line in fh:
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    cols.add_message(msg, msg_id)
                    msg_id += 1
                    if len(cols) >= self.batch_rows:
                        yield cols.flush()
        if len(cols):
            yield cols.flush()


class _BookColumns:
    """Буфер колонок: разворачивает сообщения стакана в строки по уровням."""

    def __init__(self):
        self._reset()

    def _reset(self) -> None:
        self.ts, self.event_type, self.side, self.price, self.size = [], [], [], [], []
        self.num_orders, self.msg_id, self.level = [], [], []

    def __len__(self) -> int:
        return len(self.ts)

    def add_message(self, msg: dict, msg_id: int) -> None:
        ts = int(msg["ts"]) * _MS_TO_NS
        etype = BOOK_SNAPSHOT if msg["action"] == "snapshot" else BOOK_UPDATE
        level = 0
        for side, key in ((BUY, "bids"), (SELL, "asks")):
            for price, size, orders in msg.get(key, ()):
                self.ts.append(ts)
                self.event_type.append(etype)
                self.side.append(side)
                self.price.append(float(price))
                self.size.append(float(size))
                self.num_orders.append(int(orders))
                self.msg_id.append(msg_id)
                self.level.append(level)
                level += 1

    def flush(self) -> pa.Table:
        table = pa.table(
            {
                "ts": pa.array(self.ts, pa.int64()).cast(EVENT_SCHEMA.field("ts").type),
                "event_type": pa.array(self.event_type, pa.int8()),
                "side": pa.array(self.side, pa.int8()),
                "price": pa.array(self.price, pa.float64()),
                "size": pa.array(self.size, pa.float64()),
                "num_orders": pa.array(self.num_orders, pa.int32()),
                "msg_id": pa.array(self.msg_id, pa.int64()),
                "level": pa.array(self.level, pa.int16()),
            },
            schema=EVENT_SCHEMA,
        )
        self._reset()
        return table
