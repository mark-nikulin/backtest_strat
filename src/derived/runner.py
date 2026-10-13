"""
Запуск построения производных датасетов по символу. Идемпотентно: день пересчитывается,
только если изменился файл событий (или версия логики, или force, или пропал выход).
"""
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional

from src.derived.build import KINDS, build_day
from src.storage.parquet import Manifest, PartitionedParquetWriter
from src.utils.locks import file_lock
from src.validation.runner import events_files

logger = logging.getLogger(__name__)

DERIVE_VERSION = 2  # поднять при изменении логики — датасеты пересоберутся


@dataclass
class DeriveStep:
    day: str
    status: str                       # processed | skipped | failed
    outputs: List[str] = field(default_factory=list)
    rows: dict = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class DeriveReport:
    symbol: str
    results: List[DeriveStep] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.status != "failed" for r in self.results)

    def summary(self) -> str:
        c = {s: sum(1 for r in self.results if r.status == s) for s in ("processed", "skipped", "failed")}
        return f"{self.symbol}: " + ", ".join(f"{k}={v}" for k, v in c.items())


def derive_symbol(
    symbol: str,
    exchange: str = "okx",
    processed_dir: str = "data/processed",
    start: Optional[date] = None,
    end: Optional[date] = None,
    force: bool = False,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> DeriveReport:
    files = events_files(processed_dir, exchange, symbol)
    days = sorted(d for d in files if (start is None or d >= start) and (end is None or d <= end))
    root = Path(processed_dir) / exchange / symbol
    report = DeriveReport(symbol)

    with file_lock(root / ".derived.lock"):
        manifest = Manifest(root / "_derived_manifest.json")
        for i, day in enumerate(days, 1):
            path = files[day]
            key = f"derive:{day.isoformat()}"
            fp = {"name": path.name, "size": path.stat().st_size, "version": DERIVE_VERSION}
            if not force and manifest.is_current(key, fp):
                report.results.append(DeriveStep(day.isoformat(), "skipped", manifest.get(key)["outputs"]))
            else:
                try:
                    manifest.remove_outputs(key)
                    tables = build_day(path)
                    outputs: List[Path] = []
                    for kind, (dirname, schema, _) in KINDS.items():
                        with PartitionedParquetWriter(root / dirname, f"{day.isoformat()}.parquet", schema) as w:
                            w.write(tables[kind])
                            outputs += w.commit()
                    manifest.set(key, fp, outputs)
                    step = DeriveStep(day.isoformat(), "processed", [str(p) for p in outputs],
                                      rows={k: t.num_rows for k, t in tables.items()})
                    logger.info(f"Производные датасеты {symbol} {day}: " + ", ".join(f"{k}={n}" for k, n in step.rows.items()))
                    report.results.append(step)
                except Exception as e:
                    logger.error(f"Производные датасеты {symbol} {day} упали: {e!r}")
                    report.results.append(DeriveStep(day.isoformat(), "failed", error=repr(e)))
            if on_progress:
                on_progress(i, len(days))

    logger.info(f"Производные датасеты завершены. {report.summary()}")
    return report
