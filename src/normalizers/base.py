import abc
import enum
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, List, Optional

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds

from src.normalizers.schema import EVENT_SCHEMA, SORT_KEYS, TRADE
from src.storage.parquet import Manifest, PartitionedParquetWriter

DATA_TYPES = ("trades", "orderbook")
EVENTS = "events"


class StepStatus(str, enum.Enum):
    PROCESSED = "processed"    # обработан сейчас
    SKIPPED = "skipped"        # уже актуален (идемпотентность)
    INCOMPLETE = "incomplete"  # не хватает входных данных (например, сделок за следующий день)
    FAILED = "failed"


@dataclass
class StepResult:
    name: str
    status: StepStatus
    outputs: List[Path] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class NormalizeReport:
    symbol: str
    results: List[StepResult] = field(default_factory=list)

    def by_status(self, status: StepStatus) -> List[StepResult]:
        return [r for r in self.results if r.status == status]

    @property
    def ok(self) -> bool:
        return not self.by_status(StepStatus.FAILED)

    def summary(self) -> str:
        counts = {s.value: len(self.by_status(s)) for s in StepStatus}
        return f"{self.symbol}: " + ", ".join(f"{k}={v}" for k, v in counts.items())


# (выполнено_шагов, всего_шагов)
ProgressCallback = Callable[[int, int], None]


def _fingerprint(paths: List[Path]) -> dict:
    """Отпечаток входов: имя + размер. Сырые архивы пишутся атомарно и не меняются после записи."""
    return {p.name: p.stat().st_size for p in sorted(paths)}


class BaseNormalizer(abc.ABC):
    """
    Нормализация сырых архивов биржи в единую схему EVENT_SCHEMA.

    Этапы (каждый идемпотентен — повторный запуск пропускает актуальные результаты):
      1. normalize_file: сырой архив -> processed/<ex>/<sym>/<data_type>/year=/month=/<source>.parquet
      2. build_events:   стакан + сделки за UTC-день -> processed/<ex>/<sym>/events/year=/month=/<YYYY-MM-DD>.parquet,
                         строго отсортированные по SORT_KEYS.

    Наследник реализует чтение своих форматов (iter_trades / iter_orderbook) и знание
    о том, как файлы биржи нарезаны по времени (source_date, trade_dates_for_utc_day).
    """

    batch_rows: int = 250_000

    def __init__(self, exchange_name: str, raw_dir: str = "data/raw", processed_dir: str = "data/processed"):
        self.exchange_name = exchange_name.lower()
        self.raw_dir = Path(raw_dir)
        self.processed_dir = Path(processed_dir)
        self.logger = logging.getLogger(self.__class__.__name__)

    # --- Специфика биржи ---

    @abc.abstractmethod
    def iter_trades(self, path: Path) -> Iterator[pa.Table]:
        """Потоково читает сырой архив сделок, отдает таблицы в EVENT_SCHEMA, отсортированные по ts."""

    @abc.abstractmethod
    def iter_orderbook(self, path: Path) -> Iterator[pa.Table]:
        """Потоково читает сырой архив стакана, отдает таблицы в EVENT_SCHEMA, отсортированные по ts."""

    @abc.abstractmethod
    def source_date(self, path: Path) -> date:
        """Дата сырого архива по имени файла."""

    @abc.abstractmethod
    def trade_dates_for_utc_day(self, day: date) -> List[date]:
        """Даты архивов сделок, которые вместе покрывают UTC-сутки day."""

    # --- Пути ---

    def symbol_dir(self, symbol: str, layer: str = "processed") -> Path:
        base = self.processed_dir if layer == "processed" else self.raw_dir
        return base / self.exchange_name / symbol

    def manifest(self, symbol: str) -> Manifest:
        return Manifest(self.symbol_dir(symbol) / "_manifest.json")

    def raw_files(self, symbol: str, data_type: str) -> List[Path]:
        d = self.symbol_dir(symbol, "raw") / data_type
        return sorted(p for p in d.iterdir() if p.is_file() and not p.name.endswith(".part")) if d.exists() else []

    def _raw_by_date(self, symbol: str, data_type: str) -> dict:
        return {self.source_date(p): p for p in self.raw_files(symbol, data_type)}

    # --- Этап 1: сырой архив -> нормализованный Parquet ---

    def normalize_file(self, symbol: str, data_type: str, raw_path: Path, force: bool = False) -> StepResult:
        manifest = self.manifest(symbol)
        key = f"{data_type}:{raw_path.name}"
        fp = _fingerprint([raw_path])

        if not force and manifest.is_current(key, fp):
            return StepResult(key, StepStatus.SKIPPED, [Path(p) for p in manifest.get(key)["outputs"]])

        reader = self.iter_trades if data_type == "trades" else self.iter_orderbook
        out_name = raw_path.name.split(".")[0] + ".parquet"
        try:
            manifest.remove_outputs(key)
            with PartitionedParquetWriter(self.symbol_dir(symbol) / data_type, out_name, EVENT_SCHEMA) as w:
                rows = 0
                for table in reader(raw_path):
                    w.write(table)
                    rows += table.num_rows
                outputs = w.commit()
            manifest.set(key, fp, outputs)
            self.logger.info(f"Нормализован {raw_path.name}: {rows} строк -> {len(outputs)} файл(ов)")
            return StepResult(key, StepStatus.PROCESSED, outputs)
        except Exception as e:
            self.logger.error(f"Ошибка нормализации {raw_path}: {e!r}")
            return StepResult(key, StepStatus.FAILED, error=repr(e))

    # --- Этап 2: слияние в поток событий за UTC-сутки ---

    def _normalized_outputs(self, manifest: Manifest, data_type: str, raw_path: Path) -> Optional[List[Path]]:
        entry = manifest.get(f"{data_type}:{raw_path.name}")
        if not entry or not manifest.is_current(f"{data_type}:{raw_path.name}", _fingerprint([raw_path])):
            return None
        return [Path(p) for p in entry["outputs"]]

    def build_events(self, symbol: str, day: date, force: bool = False) -> StepResult:
        manifest = self.manifest(symbol)
        key = f"{EVENTS}:{day.isoformat()}"

        book_raw = self._raw_by_date(symbol, "orderbook").get(day)
        trades_by_date = self._raw_by_date(symbol, "trades")
        trade_raws = [trades_by_date.get(d) for d in self.trade_dates_for_utc_day(day)]

        if book_raw is None or any(r is None for r in trade_raws):
            missing = ["orderbook"] * (book_raw is None) + [
                f"trades {d}" for d, r in zip(self.trade_dates_for_utc_day(day), trade_raws) if r is None
            ]
            return StepResult(key, StepStatus.INCOMPLETE, error="нет сырых данных: " + ", ".join(missing))

        inputs = [book_raw, *trade_raws]
        fp = _fingerprint(inputs)
        if not force and manifest.is_current(key, fp):
            return StepResult(key, StepStatus.SKIPPED, [Path(p) for p in manifest.get(key)["outputs"]])

        book_files = self._normalized_outputs(manifest, "orderbook", book_raw)
        trade_files = [self._normalized_outputs(manifest, "trades", r) for r in trade_raws]
        if book_files is None or any(t is None for t in trade_files):
            return StepResult(key, StepStatus.INCOMPLETE, error="входы еще не нормализованы")

        try:
            manifest.remove_outputs(key)
            book_ds = ds.dataset([str(p) for p in book_files], schema=EVENT_SCHEMA, format="parquet")
            trade_ds = ds.dataset([str(p) for t in trade_files for p in t], schema=EVENT_SCHEMA, format="parquet")
            day_start = datetime.combine(day, time(), tzinfo=timezone.utc)

            rows = 0
            with PartitionedParquetWriter(self.symbol_dir(symbol) / EVENTS, f"{day.isoformat()}.parquet", EVENT_SCHEMA) as w:
                # Часовые окна: в памяти никогда не больше часа данных
                for h in range(24):
                    lo = pa.scalar(day_start + timedelta(hours=h), EVENT_SCHEMA.field("ts").type)
                    hi = pa.scalar(day_start + timedelta(hours=h + 1), EVENT_SCHEMA.field("ts").type)
                    flt = (ds.field("ts") >= lo) & (ds.field("ts") < hi)
                    window = pa.concat_tables([trade_ds.to_table(filter=flt), book_ds.to_table(filter=flt)])
                    w.write(sort_events(window))
                    rows += window.num_rows
                outputs = w.commit()

            manifest.set(key, fp, outputs)
            self.logger.info(f"Собраны события {symbol} {day}: {rows} строк")
            return StepResult(key, StepStatus.PROCESSED, outputs)
        except Exception as e:
            self.logger.error(f"Ошибка сборки событий {symbol} {day}: {e!r}")
            return StepResult(key, StepStatus.FAILED, error=repr(e))

    # --- Оркестрация ---

    def run(
        self,
        symbol: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
        force: bool = False,
        on_progress: Optional[ProgressCallback] = None,
    ) -> NormalizeReport:
        """
        Нормализует все сырые архивы символа (в диапазоне дат, если задан) и собирает
        события за все дни, для которых хватает данных. Ошибка одного шага не останавливает остальные.
        """
        in_range = lambda d: (start is None or d >= start) and (end is None or d <= end)

        files = [
            (dt, p) for dt in DATA_TYPES for p in self.raw_files(symbol, dt)
            # сделки за end+1 нужны для сборки событий за end
            if in_range(self.source_date(p)) or (dt == "trades" and end and self.source_date(p) == end + timedelta(days=1))
        ]
        days = sorted(d for d in self._raw_by_date(symbol, "orderbook") if in_range(d))
        total = len(files) + len(days)
        report = NormalizeReport(symbol)

        def _step(result: StepResult) -> None:
            report.results.append(result)
            if on_progress:
                on_progress(len(report.results), total)

        for dt, p in files:
            _step(self.normalize_file(symbol, dt, p, force=force))
        for d in days:
            _step(self.build_events(symbol, d, force=force))

        self.logger.info(f"Нормализация завершена. {report.summary()}")
        return report


def sort_events(table: pa.Table) -> pa.Table:
    """Детерминированная сортировка по SORT_KEYS (см. schema.py)."""
    is_book = pc.not_equal(table.column("event_type"), pa.scalar(TRADE, pa.int8()))
    tmp = table.append_column("is_book", is_book)
    idx = pc.sort_indices(tmp, sort_keys=[(k, "ascending") for k in SORT_KEYS])
    return table.take(idx)
