from datetime import date
from pathlib import Path
from typing import Iterator, List, Optional

import pyarrow as pa
import pyarrow.parquet as pq

from src.utils.dates import date_range


def event_files(
    symbol: str,
    start: date,
    end: date,
    exchange: str = "okx",
    processed_dir: str = "data/processed",
) -> List[Path]:
    """Файлы событий за [start, end] в хронологическом порядке. Читаются только нужные партиции."""
    root = Path(processed_dir) / exchange / symbol / "events"
    paths = (root / f"year={d.year}" / f"month={d.month:02d}" / f"{d.isoformat()}.parquet" for d in date_range(start, end))
    return [p for p in paths if p.exists()]


def iter_events(
    symbol: str,
    start: date,
    end: date,
    exchange: str = "okx",
    processed_dir: str = "data/processed",
    columns: Optional[List[str]] = None,
    batch_size: int = 100_000,
) -> Iterator[pa.RecordBatch]:
    """Потоковое чтение событий для бэктестера: батчи строго в порядке SORT_KEYS, память ~ batch_size."""
    for path in event_files(symbol, start, end, exchange, processed_dir):
        yield from pq.ParquetFile(path).iter_batches(batch_size=batch_size, columns=columns)


def load_events(
    symbol: str,
    start: date,
    end: date,
    exchange: str = "okx",
    processed_dir: str = "data/processed",
    columns: Optional[List[str]] = None,
) -> pa.Table:
    """Все события за период одной таблицей — для исследований на небольших диапазонах."""
    files = event_files(symbol, start, end, exchange, processed_dir)
    if not files:
        from src.normalizers.schema import EVENT_SCHEMA
        return EVENT_SCHEMA.empty_table() if columns is None else EVENT_SCHEMA.empty_table().select(columns)
    return pa.concat_tables(pq.read_table(p, columns=columns) for p in files)
