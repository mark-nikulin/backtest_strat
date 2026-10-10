"""
CLI пайплайна данных.

Примеры:
  python -m src.cli --exchange okx --symbol BTC-USDT --mode backfill --start 2024-01-01 --end 2024-01-31
  python -m src.cli --exchange okx --symbol BTC-USDT ETH-USDT --mode daily
  python -m src.cli --exchange okx --symbol BTC-USDT --mode backfill --start 2024-01-01 --end 2024-01-02 \\
                    --data-type trades --stage ingest

Код возврата: 0 — без ошибок, 1 — были FAILED-шаги, 2 — неверные аргументы. Удобно для cron.
"""
import argparse
import logging
import sys
from typing import List, Optional

from src.fetchers import FETCHERS
from src.pipeline import DATA_TYPES, MODES, STAGES, PipelineParams, run_pipeline
from src.utils.dates import parse_date


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="Сбор и нормализация исторических рыночных данных",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Примеры:")[1].split("Код возврата")[0].rstrip(),
    )
    p.add_argument("--exchange", required=True, choices=sorted(FETCHERS))
    p.add_argument("--symbol", required=True, nargs="+", help="Один или несколько тикеров, напр. BTC-USDT BTC-USDT-SWAP")
    p.add_argument("--mode", choices=MODES, default="backfill")
    p.add_argument("--start", type=parse_date, help="YYYY-MM-DD, обязателен для backfill")
    p.add_argument("--end", type=parse_date, help="YYYY-MM-DD, обязателен для backfill; для daily — дата вместо вчерашней")
    p.add_argument("--data-type", nargs="+", choices=DATA_TYPES, default=list(DATA_TYPES), dest="data_types")
    p.add_argument("--stage", choices=STAGES, default="all", help="all = скачать и нормализовать")
    p.add_argument("--raw-dir", default="data/raw")
    p.add_argument("--processed-dir", default="data/processed")
    p.add_argument("--concurrency", type=int, default=5, help="Параллельных загрузок в backfill")
    p.add_argument("--force", action="store_true", help="Пересобрать нормализованные данные, даже если они актуальны")
    p.add_argument("--quiet", action="store_true", help="Без прогресс-бара")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def _progress_printer(enabled: bool):
    if not enabled or not sys.stderr.isatty():
        return None

    def show(frac: float, text: str) -> None:
        width = 30
        filled = int(frac * width)
        sys.stderr.write(f"\r[{'#' * filled}{'.' * (width - filled)}] {frac * 100:5.1f}%  {text[:60]:<60}")
        sys.stderr.flush()
        if frac >= 1.0:
            sys.stderr.write("\n")

    return show


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    params = PipelineParams(
        exchange=args.exchange,
        symbols=args.symbol,
        mode=args.mode,
        start=args.start,
        end=args.end,
        data_types=args.data_types,
        stage=args.stage,
        raw_dir=args.raw_dir,
        processed_dir=args.processed_dir,
        max_concurrent=args.concurrency,
        force=args.force,
    )
    try:
        params.validate()
    except ValueError as e:
        parser.error(str(e))  # exit 2

    result = run_pipeline(params, on_progress=_progress_printer(not args.quiet))

    print("\n".join(result.summary_lines()))
    for f in result.to_dict()["failed"]:
        print(f"FAILED: {f}", file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
