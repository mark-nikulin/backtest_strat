"""
Ночной планировщик: cron -> `python -m src.scheduler run` -> пайплайн за последние дни.

Два независимых выключателя:
  1. Строка в crontab (install / uninstall) — запускается ли процесс вообще.
  2. Флаг "enabled" в config/schedule.json (enable / disable) — делает ли запуск что-то.
     Выключенный запуск только пишет в лог "skip" и выходит с кодом 0.
Так расписание можно поставить на паузу из Web UI, не трогая crontab на сервере.

Команды:
  python -m src.scheduler status
  python -m src.scheduler enable | disable
  python -m src.scheduler install [--time 00:30] | uninstall
  python -m src.scheduler run [--force-run]      # то, что вызывает cron
  python -m src.scheduler show-cron              # строка crontab, без установки

Время в crontab — локальное время сервера (на Linux-серверах обычно UTC).
Архивы OKX за прошедшие UTC-сутки появляются в первые минуты нового дня.
"""
import argparse
import json
import logging
import os
import shlex
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import List, Optional

from src.pipeline import PipelineParams, run_pipeline
from src.utils.dates import yesterday_utc
from src.utils.logging_setup import setup_logging

logger = logging.getLogger("src.scheduler")

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.getenv("PIPELINE_SCHEDULE_CONFIG", PROJECT_DIR / "config" / "schedule.json"))
CRON_MARKER = "# backtest-data-pipeline"
CRONTAB_BIN = os.getenv("PIPELINE_CRONTAB_BIN", "crontab")

DEFAULTS = {
    "enabled": False,
    "time": "00:30",
    "exchange": "okx",
    "symbols": ["BTC-USDT"],
    "data_types": ["trades", "orderbook"],
    # Повторно проверяем несколько последних дней: если сервер был выключен или биржа
    # выложила архив с опозданием, пропуск закроется сам. Скачанное пропускается (идемпотентность).
    "lookback_days": 3,
    "raw_dir": "data/raw",
    "processed_dir": "data/processed",
}


# --- Конфиг ---

def load_config(path: Path = CONFIG_PATH) -> dict:
    cfg = dict(DEFAULTS)
    if path.exists():
        cfg.update(json.loads(path.read_text()))
    return cfg


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    _validate_time(cfg["time"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def set_enabled(enabled: bool, path: Path = CONFIG_PATH) -> dict:
    cfg = load_config(path)
    cfg["enabled"] = enabled
    save_config(cfg, path)
    logger.info(f"Расписание {'включено' if enabled else 'выключено'}")
    return cfg


def _validate_time(value: str) -> tuple:
    try:
        hh, mm = (int(x) for x in value.split(":"))
        assert 0 <= hh < 24 and 0 <= mm < 60
    except Exception:
        raise ValueError(f"time должен быть в формате HH:MM, получено {value!r}")
    return hh, mm


# --- Crontab ---

def cron_line(time_hhmm: str) -> str:
    hh, mm = _validate_time(time_hhmm)
    python = shlex.quote(sys.executable)
    cmd = f"cd {shlex.quote(str(PROJECT_DIR))} && {python} -m src.scheduler run >/dev/null 2>&1"
    # Вывод в /dev/null: все события и так пишутся в logs/ через централизованное логирование
    return f"{mm} {hh} * * * {cmd} {CRON_MARKER}"


def _read_crontab() -> List[str]:
    res = subprocess.run([CRONTAB_BIN, "-l"], capture_output=True, text=True)
    if res.returncode != 0:  # "no crontab for user"
        return []
    return res.stdout.splitlines()


def _write_crontab(lines: List[str]) -> None:
    content = "\n".join(lines) + "\n" if lines else ""
    subprocess.run([CRONTAB_BIN, "-"], input=content, text=True, check=True)


def installed_cron_line() -> Optional[str]:
    return next((l for l in _read_crontab() if CRON_MARKER in l), None)


def install(time_hhmm: Optional[str] = None) -> str:
    """Ставит (или заменяет) нашу строку в crontab. Чужие строки не трогает."""
    cfg = load_config()
    if time_hhmm:
        cfg["time"] = time_hhmm
        save_config(cfg)
    line = cron_line(cfg["time"])
    lines = [l for l in _read_crontab() if CRON_MARKER not in l] + [line]
    _write_crontab(lines)
    logger.info(f"Cron установлен: {cfg['time']} ежедневно")
    return line


def uninstall() -> bool:
    lines = _read_crontab()
    kept = [l for l in lines if CRON_MARKER not in l]
    if len(kept) == len(lines):
        return False
    _write_crontab(kept)
    logger.info("Cron удален")
    return True


def status() -> dict:
    cfg = load_config()
    try:
        line = installed_cron_line()
        cron_error = None
    except FileNotFoundError:
        line, cron_error = None, "crontab не найден в системе"
    return {
        "enabled": cfg["enabled"],
        "cron_installed": line is not None,
        "cron_line": line,
        "cron_error": cron_error,
        "will_run": cfg["enabled"] and line is not None,
        "config": cfg,
    }


# --- Запуск ---

def run(force_run: bool = False) -> int:
    cfg = load_config()
    if not cfg["enabled"] and not force_run:
        logger.info("Расписание выключено (config/schedule.json: enabled=false) — пропуск")
        return 0

    end = yesterday_utc()
    start = end - timedelta(days=max(1, int(cfg["lookback_days"])) - 1)
    params = PipelineParams(
        exchange=cfg["exchange"],
        symbols=cfg["symbols"],
        mode="backfill",
        start=start,
        end=end,
        data_types=cfg["data_types"],
        raw_dir=cfg["raw_dir"],
        processed_dir=cfg["processed_dir"],
    )
    logger.info(f"Ночной запуск: {params.exchange} {params.symbols} {start}..{end}")
    try:
        result = run_pipeline(params)
    except Exception:
        logger.exception("Ночной запуск упал")
        return 1
    if not result.ok:
        for f in result.to_dict()["failed"]:
            logger.error(f"Ночной запуск: FAILED {f}")
    logger.info(f"Ночной запуск завершен: ok={result.ok}")
    return 0 if result.ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.scheduler", description="Ночной планировщик пайплайна")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("enable")
    sub.add_parser("disable")
    inst = sub.add_parser("install")
    inst.add_argument("--time", help="HH:MM, локальное время сервера")
    sub.add_parser("uninstall")
    r = sub.add_parser("run")
    r.add_argument("--force-run", action="store_true", help="Выполнить, даже если расписание выключено")
    sub.add_parser("show-cron")
    args = p.parse_args(argv)

    setup_logging("cron" if args.cmd == "run" else "cli", console=args.cmd != "run" or sys.stderr.isatty())

    if args.cmd == "run":
        return run(force_run=args.force_run)
    if args.cmd == "status":
        print(json.dumps(status(), indent=2, ensure_ascii=False))
    elif args.cmd == "enable":
        set_enabled(True)
        if not status()["cron_installed"]:
            print("Внимание: флаг включен, но строка cron не установлена — выполните `install`")
    elif args.cmd == "disable":
        set_enabled(False)
    elif args.cmd == "install":
        print(install(args.time))
        if not load_config()["enabled"]:
            print("Cron установлен, но расписание выключено (enabled=false): запуски будут пропускаться")
    elif args.cmd == "uninstall":
        print("Удалено" if uninstall() else "Строка cron не найдена")
    elif args.cmd == "show-cron":
        print(cron_line(load_config()["time"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
