"""
Единая точка запуска пайплайна. CLI и Web UI вызывают только этот модуль,
поэтому поведение (идемпотентность, retries, отчеты) у них идентичное.
"""
import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Callable, List, Optional, Sequence

from src.fetchers import BackfillReport, get_fetcher
from src.normalizers import NormalizeReport, get_normalizer
from src.utils.dates import yesterday_utc

logger = logging.getLogger(__name__)

MODES = ("backfill", "daily")
STAGES = ("all", "ingest", "normalize")
DATA_TYPES = ("trades", "orderbook")

# (доля 0..1, текстовый статус)
ProgressCallback = Callable[[float, str], None]


@dataclass
class PipelineParams:
    exchange: str
    symbols: List[str]
    mode: str = "backfill"
    start: Optional[date] = None
    end: Optional[date] = None
    data_types: List[str] = field(default_factory=lambda: list(DATA_TYPES))
    stage: str = "all"
    raw_dir: str = "data/raw"
    processed_dir: str = "data/processed"
    max_concurrent: int = 5
    force: bool = False

    def validate(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode должен быть одним из {MODES}")
        if self.stage not in STAGES:
            raise ValueError(f"stage должен быть одним из {STAGES}")
        if not self.symbols:
            raise ValueError("Нужен хотя бы один symbol")
        bad = set(self.data_types) - set(DATA_TYPES)
        if bad or not self.data_types:
            raise ValueError(f"data_types: допустимо {DATA_TYPES}, получено {self.data_types}")
        if self.mode == "backfill":
            if not self.start or not self.end:
                raise ValueError("Для backfill нужны start и end")
            if self.end < self.start:
                raise ValueError("end раньше start")
        if self.max_concurrent < 1:
            raise ValueError("max_concurrent >= 1")

    def date_bounds(self) -> tuple:
        if self.mode == "daily":
            d = self.end or yesterday_utc()
            return d, d
        return self.start, self.end


@dataclass
class PipelineResult:
    ingest: List[BackfillReport] = field(default_factory=list)
    normalize: List[NormalizeReport] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.ingest) and all(r.ok for r in self.normalize)

    def summary_lines(self) -> List[str]:
        return [f"ingest    {r.summary()}" for r in self.ingest] + [f"normalize {r.summary()}" for r in self.normalize]

    def to_dict(self) -> dict:
        def failed(rs, attr_status, key):
            return [
                {key: str(getattr(x, key)), "error": x.error}
                for r in rs for x in r.results if x.status.value == attr_status
            ]
        return {
            "ok": self.ok,
            "summary": self.summary_lines(),
            "failed": failed(self.ingest, "failed", "target_date") + failed(self.normalize, "failed", "name"),
        }


async def run_pipeline_async(params: PipelineParams, on_progress: Optional[ProgressCallback] = None) -> PipelineResult:
    params.validate()
    start, end = params.date_bounds()
    result = PipelineResult()

    do_ingest = params.stage in ("all", "ingest")
    do_normalize = params.stage in ("all", "normalize")
    # Веса этапов для общего прогресс-бара
    units: List[str] = (["ingest"] * len(params.data_types) if do_ingest else []) + (["normalize"] if do_normalize else [])
    total_units = len(units) * len(params.symbols)
    done_units = 0

    def report(frac_of_unit: float, text: str) -> None:
        if on_progress:
            on_progress(min(1.0, (done_units + frac_of_unit) / total_units), text)

    for symbol in params.symbols:
        if do_ingest:
            fetcher = get_fetcher(params.exchange, base_dir=params.raw_dir)
            for dt in params.data_types:
                partial = lambda f, s=symbol, dt=dt: report(f, f"{s} {dt}: {f * 100:.0f}%")
                rep = await fetcher.download_historical_range(
                    symbol, start, end, dt, max_concurrent=params.max_concurrent, on_partial=partial
                )
                result.ingest.append(rep)
                done_units += 1

        if do_normalize:
            normalizer = get_normalizer(params.exchange, raw_dir=params.raw_dir, processed_dir=params.processed_dir)
            cb = lambda i, n, s=symbol: report(i / max(n, 1), f"{s} нормализация: {i}/{n} шагов")
            # daily: пересобираем и предыдущий день — для него только сейчас появились сделки за D+1 (UTC+8)
            norm_start = start if params.mode == "backfill" else None
            rep = await asyncio.to_thread(
                normalizer.run, symbol, norm_start, end, params.force, cb
            )
            result.normalize.append(rep)
            done_units += 1

    report(0, "Готово")
    for line in result.summary_lines():
        logger.info(line)
    return result


def run_pipeline(params: PipelineParams, on_progress: Optional[ProgressCallback] = None) -> PipelineResult:
    return asyncio.run(run_pipeline_async(params, on_progress))
