"""
Консольный запуск бэктеста на нормализованных событиях (data/processed/<exchange>/<symbol>/events).

  bt-backtest --strategy mypkg.strategies:MyAlpha --symbol BTC-USDT --start 2024-01-01 --end 2024-01-01 \\
              --tick 0.1 --lot 0.00000001 --param size=0.01

Стратегия — любой класс из вашего проекта (`модуль:Класс`), наследник backtest_strat.backtest.Strategy.
Параметры стратегии: --param key=value (повторяемый), значения парсятся как JSON.
"""
import argparse
import csv
import importlib
import json
import sys
from typing import List, Optional

from backtest_strat.backtest import BacktestEngine, InstrumentSpec, format_summary, summarize
from backtest_strat.paths import PROCESSED_DIR
from backtest_strat.utils.dates import parse_date
from backtest_strat.utils.logging_setup import setup_logging


def load_strategy(path: str, params: dict):
    module, _, cls = path.partition(":")
    if not cls:
        raise SystemExit(f"--strategy ожидает 'модуль:Класс', получено {path!r}")
    # папка запуска первой в пути: стратегии лежат в проекте пользователя, а не в пакете
    if "" not in sys.path and "." not in sys.path:
        sys.path.insert(0, "")
    return getattr(importlib.import_module(module), cls)(**params)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="bt-backtest", description="Event-driven бэктест")
    p.add_argument("--strategy", required=True, help="модуль:Класс")
    p.add_argument("--param", action="append", default=[], help="key=value (JSON)")
    p.add_argument("--exchange", default="okx")
    p.add_argument("--symbol", required=True)
    p.add_argument("--start", type=parse_date, required=True)
    p.add_argument("--end", type=parse_date, required=True)
    p.add_argument("--tick", type=float, required=True, help="Шаг цены")
    p.add_argument("--lot", type=float, required=True, help="Минимальный лот")
    p.add_argument("--multiplier", type=float, default=1.0)
    p.add_argument("--maker-fee-bps", type=float, default=8.0)
    p.add_argument("--taker-fee-bps", type=float, default=10.0)
    p.add_argument("--feed-latency-ms", type=float, default=1.0)
    p.add_argument("--order-latency-ms", type=float, default=2.0)
    p.add_argument("--processed-dir", default=PROCESSED_DIR)
    p.add_argument("--fills-csv", help="Сохранить сделки стратегии в CSV")
    args = p.parse_args(argv)

    setup_logging("cli")
    params = {}
    for kv in args.param:
        k, _, v = kv.partition("=")
        params[k] = json.loads(v)

    spec = InstrumentSpec(
        symbol=args.symbol, tick_size=args.tick, lot_size=args.lot, contract_multiplier=args.multiplier,
        maker_fee=args.maker_fee_bps / 1e4, taker_fee=args.taker_fee_bps / 1e4,
        feed_latency_ns=int(args.feed_latency_ms * 1e6), order_latency_ns=int(args.order_latency_ms * 1e6),
    )
    engine = BacktestEngine(load_strategy(args.strategy, params), spec)
    result = engine.run(args.start, args.end, exchange=args.exchange, processed_dir=args.processed_dir)
    if result.events == 0:
        print("Нет событий за период — сначала соберите данные: bt-data --exchange okx --symbol ... ", file=sys.stderr)
        return 1

    print(format_summary(summarize(result)))
    if args.fills_csv:
        with open(args.fills_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts", "order_id", "side", "price", "amount", "is_maker", "fee", "tag"])
            for x in result.portfolio.fills:
                w.writerow([x.ts, x.order_id, x.side, x.price, x.amount, x.is_maker, x.fee, x.tag])
    return 0


if __name__ == "__main__":
    sys.exit(main())
