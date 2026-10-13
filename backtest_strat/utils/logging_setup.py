"""
Централизованное логирование: все процессы (CLI, cron, API, Web UI) пишут в общий
каталог logs/ в одном формате, с меткой источника.

  2026-10-10 23:05:42,013 [INFO] [cron] backtest_strat.pipeline: ingest BTC-USDT/trades: downloaded=1, ...

Файлы суточные (pipeline-YYYY-MM-DD.log, по UTC). Ротация без переименования файлов:
каждый процесс сам переключается на файл нового дня, поэтому несколько процессов
могут писать одновременно без гонок (запись в режиме append атомарна для строк лога).
"""
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

LOG_DIR = Path(os.getenv("PIPELINE_LOG_DIR", "logs"))
RETENTION_DAYS = int(os.getenv("PIPELINE_LOG_RETENTION_DAYS", "30"))
LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(source)s] %(name)s: %(message)s"
FILE_RE = re.compile(r"^pipeline-(\d{4}-\d{2}-\d{2})\.log$")

# Разбор строки лога обратно в поля — для вкладки Logs
LINE_RE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) \[(?P<level>[A-Z]+)\] \[(?P<source>[^\]]+)\] "
    r"(?P<logger>[^:]+): (?P<message>.*)$"
)


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class DailyFileHandler(logging.Handler):
    """Пишет в LOG_DIR/pipeline-<UTC-день>.log, переключая файл при смене суток."""

    def __init__(self, log_dir: Path):
        super().__init__()
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._day: Optional[str] = None
        self._stream = None

    def _ensure_stream(self) -> None:
        day = _utc_day()
        if day != self._day:
            if self._stream:
                self._stream.close()
            self._stream = open(self.log_dir / f"pipeline-{day}.log", "a", encoding="utf-8")
            self._day = day

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.acquire()
            try:
                self._ensure_stream()
                self._stream.write(msg + "\n")
                self._stream.flush()
            finally:
                self.release()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._stream:
            self._stream.close()
            self._stream = None
        super().close()


class _SourceFilter(logging.Filter):
    def __init__(self, source: str):
        super().__init__()
        self.source = source

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "source"):
            record.source = self.source
        return True


def cleanup_old_logs(log_dir: Path = LOG_DIR, retention_days: int = RETENTION_DAYS) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    for p in log_dir.glob("pipeline-*.log"):
        m = FILE_RE.match(p.name)
        if m and m.group(1) < cutoff:
            p.unlink(missing_ok=True)


def setup_logging(source: str, level: str = "INFO", console: bool = True, log_dir: Optional[Path] = None) -> None:
    """
    Настраивает корневой логгер процесса. source — метка источника: cli / cron / api / ui.
    Повторный вызов безопасен (обработчики пересоздаются).
    """
    log_dir = log_dir or LOG_DIR
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    root.setLevel(level)

    formatter = logging.Formatter(LOG_FORMAT)
    formatter.converter = time.gmtime  # время в логах — UTC, как и даты данных
    source_filter = _SourceFilter(source)

    file_handler = DailyFileHandler(log_dir)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(source_filter)
    root.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        stream.addFilter(source_filter)
        root.addHandler(stream)

    # Шумные библиотеки — только предупреждения
    for noisy in ("aiohttp.access", "urllib3", "httpx", "watchdog"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    cleanup_old_logs(log_dir)


def read_logs(
    log_dir: Optional[Path] = None,
    days: int = 1,
    levels: Optional[set] = None,
    source: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 500,
) -> list:
    """Последние записи лога (новые первыми) с фильтрами — для вкладки Logs."""
    log_dir = log_dir or LOG_DIR
    files = sorted((p for p in log_dir.glob("pipeline-*.log") if FILE_RE.match(p.name)), reverse=True)[:days]
    q = query.lower() if query else None
    out = []
    for path in files:
        entries = []
        current = None
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                m = LINE_RE.match(line)
                if m:
                    current = m.groupdict()
                    entries.append(current)
                elif current is not None:  # продолжение многострочной записи (traceback)
                    current["message"] += "\n" + line
        for e in reversed(entries):
            if levels and e["level"] not in levels:
                continue
            if source and e["source"] != source:
                continue
            if q and q not in e["message"].lower() and q not in e["logger"].lower():
                continue
            out.append(e)
            if len(out) >= limit:
                return out
    return out
