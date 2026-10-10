import abc
import asyncio
import enum
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional

import aiohttp

from src.utils.dates import date_range, yesterday_utc
from src.utils.http import ProgressCallback as BytesCallback, download_to_file
from src.utils.retry import NotFoundError, retry_async


class DownloadStatus(str, enum.Enum):
    DOWNLOADED = "downloaded"  # скачан сейчас
    SKIPPED = "skipped"        # уже был на диске (идемпотентность)
    MISSING = "missing"        # биржа не публиковала архив за эту дату (404 по всем URL)
    FAILED = "failed"          # ошибка после всех повторов


@dataclass
class DownloadResult:
    target_date: date
    status: DownloadStatus
    path: Optional[Path] = None
    error: Optional[str] = None


@dataclass
class BackfillReport:
    symbol: str
    data_type: str
    results: List[DownloadResult] = field(default_factory=list)

    def by_status(self, status: DownloadStatus) -> List[DownloadResult]:
        return [r for r in self.results if r.status == status]

    @property
    def ok(self) -> bool:
        return not self.by_status(DownloadStatus.FAILED)

    def summary(self) -> str:
        counts = {s.value: len(self.by_status(s)) for s in DownloadStatus}
        return f"{self.symbol}/{self.data_type}: " + ", ".join(f"{k}={v}" for k, v in counts.items())


# (выполнено_дней, всего_дней) — вызывается по завершении каждого дня
ProgressCallback = Callable[[int, int], None]
# доля 0..1 с учетом недокачанных байт — для плавного прогресс-бара (архив стакана качается минутами)
PartialCallback = Callable[[float], None]

# .part старше этого возраста — остатки убитого процесса (активная запись обновляет mtime постоянно)
STALE_PART_SECONDS = 30 * 60


class BaseExchangeFetcher(abc.ABC):
    """
    Базовый загрузчик daily-архивов биржи.

    Наследнику достаточно описать, откуда и под каким именем качать файл
    (supported_data_types, candidate_urls, archive_filename). Retries, атомарная
    запись, идемпотентность и параллельный backfill реализованы здесь.
    """

    supported_data_types: tuple = ()

    def __init__(
        self,
        exchange_name: str,
        base_dir: str = "data/raw",
        max_attempts: int = 5,
        base_delay: float = 1.0,
    ):
        self.exchange_name = exchange_name.lower()
        self.base_dir = Path(base_dir)
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.logger = logging.getLogger(self.__class__.__name__)

    # --- Специфика биржи ---

    @abc.abstractmethod
    def candidate_urls(self, symbol: str, target_date: date, data_type: str) -> List[str]:
        """URL архива в порядке приоритета. 404 по одному — пробуем следующий."""

    @abc.abstractmethod
    def archive_filename(self, symbol: str, target_date: date, data_type: str) -> str:
        """Имя файла архива на диске."""

    async def list_symbols(self, inst_type: str = "SPOT") -> List[str]:
        """Список тикеров биржи для выпадающих списков UI. По умолчанию не поддерживается."""
        return []

    # --- Общая логика ---

    def get_target_path(self, symbol: str, target_date: date, data_type: str) -> Path:
        return (
            self.base_dir / self.exchange_name / symbol / data_type
            / self.archive_filename(symbol, target_date, data_type)
        )

    def _is_downloaded(self, filepath: Path) -> bool:
        # Благодаря атомарной записи (.part -> rename) существующий файл всегда полный
        if filepath.exists() and filepath.stat().st_size > 0:
            self.logger.debug(f"Файл уже существует: {filepath}. Пропускаем.")
            return True
        return False

    def _validate_data_type(self, data_type: str) -> None:
        if data_type not in self.supported_data_types:
            raise ValueError(
                f"{self.exchange_name} не поддерживает data_type={data_type!r}. "
                f"Доступно: {', '.join(self.supported_data_types)}"
            )

    async def download_daily_archive(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        target_date: date,
        data_type: str,
        on_bytes: Optional[BytesCallback] = None,
    ) -> DownloadResult:
        """Скачивает архив за один день. Никогда не бросает исключений — статус в результате."""
        self._validate_data_type(data_type)
        dest = self.get_target_path(symbol, target_date, data_type)

        if self._is_downloaded(dest):
            return DownloadResult(target_date, DownloadStatus.SKIPPED, dest)

        for url in self.candidate_urls(symbol, target_date, data_type):
            try:
                await retry_async(
                    lambda: download_to_file(session, url, dest, on_progress=on_bytes),
                    max_attempts=self.max_attempts,
                    base_delay=self.base_delay,
                    description=f"{symbol} {data_type} {target_date}",
                )
                self.logger.info(f"Скачан {dest}")
                return DownloadResult(target_date, DownloadStatus.DOWNLOADED, dest)
            except NotFoundError:
                self.logger.debug(f"404: {url}")
                continue
            except Exception as e:
                self.logger.error(f"Не удалось скачать {symbol} {data_type} {target_date}: {e}")
                return DownloadResult(target_date, DownloadStatus.FAILED, error=str(e))

        self.logger.warning(f"Архив не найден на бирже: {symbol} {data_type} {target_date}")
        return DownloadResult(target_date, DownloadStatus.MISSING)

    async def download_historical_range(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        data_type: str,
        max_concurrent: int = 5,
        on_progress: Optional[ProgressCallback] = None,
        on_partial: Optional[PartialCallback] = None,
    ) -> BackfillReport:
        """Backfill: параллельно качает диапазон дат. Падение одного дня не останавливает остальные."""
        self._validate_data_type(data_type)
        dates = date_range(start_date, end_date)
        self.logger.info(f"Запуск Backfill для {symbol} ({data_type}) с {start_date} по {end_date}: {len(dates)} дн.")

        self._cleanup_stale_parts(symbol, data_type)
        semaphore = asyncio.Semaphore(max_concurrent)
        inflight: Dict[date, float] = {}

        def _partial() -> None:
            if on_partial:
                on_partial((len(report.results) + sum(inflight.values())) / len(dates))

        def _bytes_cb(d: date) -> BytesCallback:
            def cb(written: int, total: Optional[int]) -> None:
                inflight[d] = written / total if total else 0.0
                _partial()
            return cb
        report = BackfillReport(symbol, data_type)
        # Большие архивы стакана качаются минутами — ограничиваем только простой сокета
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)

        async with aiohttp.ClientSession(timeout=timeout) as session:

            async def _one(d: date) -> DownloadResult:
                async with semaphore:
                    result = await self.download_daily_archive(session, symbol, d, data_type, on_bytes=_bytes_cb(d))
                inflight.pop(d, None)
                report.results.append(result)
                _partial()
                if on_progress:
                    on_progress(len(report.results), len(dates))
                return result

            await asyncio.gather(*(_one(d) for d in dates))

        report.results.sort(key=lambda r: r.target_date)
        self.logger.info(f"Завершено скачивание. {report.summary()}")
        return report

    def _cleanup_stale_parts(self, symbol: str, data_type: str) -> None:
        target_dir = self.base_dir / self.exchange_name / symbol / data_type
        if not target_dir.exists():
            return
        cutoff = time.time() - STALE_PART_SECONDS
        for part in target_dir.glob("*.part"):
            try:
                if part.stat().st_mtime < cutoff:
                    part.unlink()
                    self.logger.info(f"Удален незавершенный файл прошлого запуска: {part.name}")
            except FileNotFoundError:
                pass

    async def download_daily(
        self,
        symbol: str,
        data_type: str,
        target_date: Optional[date] = None,
    ) -> BackfillReport:
        """Daily-режим: только последний полный день (UTC)."""
        d = target_date or yesterday_utc()
        return await self.download_historical_range(symbol, d, d, data_type, max_concurrent=1)
