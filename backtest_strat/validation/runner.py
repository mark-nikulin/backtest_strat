"""
Запуск валидации по символу: отчеты по дням + проверка пропусков между днями.
Отчет дня идемпотентен: хранится рядом с данными с отпечатком файла событий,
пересчитывается только если файл событий изменился (или force).
"""
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, List, Optional

from backtest_strat.utils.dates import date_range
from backtest_strat.utils.locks import file_lock
from backtest_strat.derived.trade_bars import covered_utc_days, trade_source_days, trade_source_files
from backtest_strat.storage.layout import events_files  # noqa: F401  (реэкспорт для api)
from backtest_strat.validation.day import FAIL, OK, WARN, DayReport, Thresholds, validate_events_file
from backtest_strat.validation.trades import validate_trade_day
from backtest_strat.paths import PROCESSED_DIR

logger = logging.getLogger(__name__)

REPORT_VERSION = 1  # поднять при изменении логики проверок — старые отчеты пересчитаются


def _fingerprint(path: Path) -> dict:
    st = path.stat()
    return {"name": path.name, "size": st.st_size, "version": REPORT_VERSION}


def validation_dir(processed_dir: str, exchange: str, symbol: str) -> Path:
    return Path(processed_dir) / exchange / symbol / "validation"


@dataclass
class ValidationSummary:
    symbol: str
    days: List[dict] = field(default_factory=list)       # DayReport.to_dict() + "from_cache"
    missing_days: List[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        statuses = [d["status"] for d in self.days]
        if self.missing_days or FAIL in statuses:
            return FAIL
        return WARN if WARN in statuses else OK

    @property
    def ok(self) -> bool:
        return self.status != FAIL

    def summary(self) -> str:
        c = {s: sum(1 for d in self.days if d["status"] == s) for s in (OK, WARN, FAIL)}
        miss = f", пропущено дней={len(self.missing_days)}" if self.missing_days else ""
        return f"{self.symbol}: OK={c[OK]}, WARN={c[WARN]}, FAIL={c[FAIL]}{miss}"


def validate_symbol(
    symbol: str,
    exchange: str = "okx",
    processed_dir: str = PROCESSED_DIR,
    start: Optional[date] = None,
    end: Optional[date] = None,
    force: bool = False,
    thresholds: Thresholds = Thresholds(),
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> ValidationSummary:
    files = events_files(processed_dir, exchange, symbol)
    days = sorted(d for d in files if (start is None or d >= start) and (end is None or d <= end))
    out_dir = validation_dir(processed_dir, exchange, symbol)
    summary = ValidationSummary(symbol)

    with file_lock(out_dir / ".lock"):
        for i, day in enumerate(days, 1):
            path = files[day]
            report_path = out_dir / f"{day.isoformat()}.json"
            fp = _fingerprint(path)
            cached = None
            if not force and report_path.exists():
                try:
                    data = json.loads(report_path.read_text())
                    if data.get("fingerprint") == fp:
                        cached = data["report"]
                except (json.JSONDecodeError, KeyError):
                    pass
            if cached is not None:
                summary.days.append({**cached, "from_cache": True})
            else:
                try:
                    report = validate_events_file(path, thresholds).to_dict()
                except Exception as e:
                    logger.error(f"Валидация {symbol} {day} упала: {e!r}")
                    report = DayReport(
                        day=day.isoformat(), status=FAIL, events=0, book_messages=0, trades=0, snapshots=0,
                        first_ts=None, last_ts=None, stats={},
                        checks=[{"name": "validator_error", "status": FAIL, "detail": repr(e), "value": 0, "examples": []}],
                    ).to_dict()
                else:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    tmp = report_path.with_name(report_path.name + ".tmp")
                    tmp.write_text(json.dumps({"fingerprint": fp, "report": report}, ensure_ascii=False, indent=1))
                    os.replace(tmp, report_path)
                summary.days.append({**report, "from_cache": False})
                logger.info(f"Валидация {symbol} {day}: {report['status']}")
            if on_progress:
                on_progress(i, len(days))

    # Дни без стакана, но с покрытием сделок: проверки только по сделкам (backtest_strat/validation/trades.py)
    trade_only = [
        d for d in covered_utc_days(trade_source_days(processed_dir, exchange, symbol))
        if d not in files and (start is None or d >= start) and (end is None or d <= end)
    ]
    if trade_only:
        with file_lock(out_dir / ".lock"):
            for day in trade_only:
                report_path = out_dir / f"{day.isoformat()}.json"
                srcs = [p for d in (day, day + timedelta(days=1)) for p in trade_source_files(processed_dir, exchange, symbol, d)]
                fp = {"trade_files": {p.name: p.stat().st_size for p in srcs}, "version": REPORT_VERSION}
                cached = None
                if not force and report_path.exists():
                    try:
                        data = json.loads(report_path.read_text())
                        if data.get("fingerprint") == fp:
                            cached = data["report"]
                    except (json.JSONDecodeError, KeyError):
                        pass
                if cached is not None:
                    summary.days.append({**cached, "from_cache": True})
                    continue
                try:
                    report = validate_trade_day(processed_dir, exchange, symbol, day).to_dict()
                except Exception as e:
                    logger.error(f"Валидация сделок {symbol} {day} упала: {e!r}")
                    report = DayReport(
                        day=day.isoformat(), status=FAIL, events=0, book_messages=0, trades=0, snapshots=0,
                        first_ts=None, last_ts=None, stats={},
                        checks=[{"name": "validator_error", "status": FAIL, "detail": repr(e), "value": 0, "examples": []}],
                    ).to_dict()
                else:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    tmp = report_path.with_name(report_path.name + ".tmp")
                    tmp.write_text(json.dumps({"fingerprint": fp, "report": report}, ensure_ascii=False, indent=1))
                    os.replace(tmp, report_path)
                summary.days.append({**report, "from_cache": False})
        summary.days.sort(key=lambda d: d["day"])

    # Пропуски: любой день внутри диапазона собранных дней без файла событий.
    # Последний собранный день не считаем пропуском-хвостом — его края определяются самими данными.
    have = set(files) | set(covered_utc_days(trade_source_days(processed_dir, exchange, symbol)))
    if len(have) >= 2:
        lo, hi = min(have), max(have)
        scope_lo, scope_hi = max(lo, start or lo), min(hi, end or hi)
        if scope_lo <= scope_hi:
            summary.missing_days = [d.isoformat() for d in date_range(scope_lo, scope_hi) if d not in have]
    for m in summary.missing_days:
        logger.warning(f"Нет событий за {symbol} {m} внутри собранного диапазона")
    logger.info(f"Валидация завершена. {summary.summary()}")
    return summary


def load_reports(exchange: str, symbol: str, processed_dir: str = PROCESSED_DIR) -> List[dict]:
    """Сохраненные отчеты по дням (без пересчета) — для API и дашборда."""
    out = []
    for p in sorted(validation_dir(processed_dir, exchange, symbol).glob("*.json")):
        try:
            out.append(json.loads(p.read_text())["report"])
        except (json.JSONDecodeError, KeyError):
            continue
    return out
