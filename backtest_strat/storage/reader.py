from datetime import date
from pathlib import Path
from typing import Iterator, List, Optional

import pyarrow as pa
import pyarrow.parquet as pq

from backtest_strat.utils.dates import date_range
from backtest_strat.paths import PROCESSED_DIR


def event_files(
    symbol: str,
    start: date,
    end: date,
    exchange: str = "okx",
    processed_dir: str = PROCESSED_DIR,
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
    processed_dir: str = PROCESSED_DIR,
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
    processed_dir: str = PROCESSED_DIR,
    columns: Optional[List[str]] = None,
) -> pa.Table:
    """Все события за период одной таблицей — для исследований на небольших диапазонах."""
    files = event_files(symbol, start, end, exchange, processed_dir)
    if not files:
        from backtest_strat.normalizers.schema import EVENT_SCHEMA
        return EVENT_SCHEMA.empty_table() if columns is None else EVENT_SCHEMA.empty_table().select(columns)
    return pa.concat_tables(pq.read_table(p, columns=columns) for p in files)


DERIVED_KINDS = ("l1", "bars_1s", "bars_1m")


def derived_files(
    kind: str, symbol: str, start: date, end: date, exchange: str = "okx", processed_dir: str = PROCESSED_DIR
) -> List[Path]:
    """Файлы производного датасета (l1 / bars_1s / bars_1m) за [start, end] по дням."""
    if kind not in DERIVED_KINDS:
        raise ValueError(f"kind должен быть одним из {DERIVED_KINDS}")
    root = Path(processed_dir) / exchange / symbol / kind
    paths = (root / f"year={d.year}" / f"month={d.month:02d}" / f"{d.isoformat()}.parquet" for d in date_range(start, end))
    return [p for p in paths if p.exists()]


def load_derived(
    kind: str,
    symbol: str,
    start: date,
    end: date,
    exchange: str = "okx",
    processed_dir: str = PROCESSED_DIR,
    columns: Optional[List[str]] = None,
) -> pa.Table:
    """Производный датасет за период одной таблицей (хронологически). Пусто -> пустая таблица со схемой."""
    from backtest_strat.derived import KINDS

    files = derived_files(kind, symbol, start, end, exchange, processed_dir)
    if not files:
        empty = KINDS[kind][1].empty_table()
        return empty if columns is None else empty.select(columns)
    return pa.concat_tables(pq.read_table(p, columns=columns) for p in files)
