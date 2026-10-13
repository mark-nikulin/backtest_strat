#!/usr/bin/env python3
"""
Эксперимент exp01: сезонность + погода + ликвидность. Полный прогон:

    python research/exp01_seasonal_weather_liquidity/run_study.py [--symbols BTC-USDT ETH-USDT] [--no-engine]

Шаги: (1) довести данные до производных (derive/validate идемпотентны) -> (2) анализ каждого актива
-> (3) поправка на множественные сравнения -> (4) results/*.csv, results/summary.md, results/params.json.
"""
import argparse
import json
import logging
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from study import analyze, data, stats  # noqa: E402
from study.config import PARAMS, UNIVERSE, VARIANTS  # noqa: E402
from backtest_strat.derived import derive_symbol  # noqa: E402
from backtest_strat.utils.logging_setup import setup_logging  # noqa: E402
from backtest_strat.validation import validate_symbol  # noqa: E402

RESULTS = HERE / "results"


def write_progress(stage: str, fraction: float, message: str) -> None:
    """Файл для backtest_strat/ui/monitor.py: прогресс исследования виден в вебе."""
    RESULTS.mkdir(exist_ok=True)
    tmp = RESULTS / "progress.json.tmp"
    tmp.write_text(json.dumps({"stage": stage, "fraction": round(fraction, 4), "message": message, "ts": time.time()}))
    tmp.replace(RESULTS / "progress.json")


def prepare(symbols):
    """Дойти до производных данных и проверок качества. Идемпотентно."""
    rows = []
    for s in symbols:
        v = validate_symbol(s, PARAMS.exchange, str(ROOT / PARAMS.processed_dir), PARAMS.start, PARAMS.end)
        derive_symbol(s, PARAMS.exchange, str(ROOT / PARAMS.processed_dir), PARAMS.start, PARAMS.end)
        c = {}
        for d in v.days:
            for ch in d["checks"]:
                if ch["status"] != "OK":
                    c[ch["name"]] = c.get(ch["name"], 0) + 1
        rows.append({"symbol": s, "days_validated": len(v.days), "days_ok": sum(d["status"] == "OK" for d in v.days),
                     "days_warn": sum(d["status"] == "WARN" for d in v.days), "days_fail": sum(d["status"] == "FAIL" for d in v.days),
                     "missing_days": len(v.missing_days), **{f"warn_{k}": n for k, n in c.items()}})
    return pd.DataFrame(rows).set_index("symbol")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=list(UNIVERSE))
    ap.add_argument("--no-engine", action="store_true", help="Только быстрая векторная оценка (без движка)")
    args = ap.parse_args()
    setup_logging("cli", console=False)
    RESULTS.mkdir(exist_ok=True)

    t0 = time.time()
    write_progress("проверка и подготовка данных", 0.0, f"{len(args.symbols)} активов")
    quality = prepare(args.symbols)
    quality.to_csv(RESULTS / "data_quality.csv")
    print(quality[["days_validated", "days_ok", "days_warn", "days_fail", "missing_days"]].to_string())

    weather = data.weather()
    specs = data.instrument_specs(args.symbols)
    spreads = data.spread_snapshot(args.symbols)
    rows, feats = [], {}
    for i, s in enumerate(args.symbols, 1):
        if s not in specs or s not in spreads:
            print(f"[{i}/{len(args.symbols)}] {s}: нет параметров инструмента — пропуск")
            continue
        write_progress("анализ активов", 0.1 + 0.85 * (i - 1) / len(args.symbols), f"{s} ({i}/{len(args.symbols)})")
        t = time.time()
        r = analyze.analyze_asset(s, weather, specs[s], spreads[s], run_engine=not args.no_engine,
                                  rng=np.random.default_rng(PARAMS.seed + i))
        if "row" not in r:
            print(f"[{i}/{len(args.symbols)}] {s}: {r.get('error')}")
            continue
        rows.append(r["row"])
        feats[s] = r["features"]
        print(f"[{i}/{len(args.symbols)}] {s}: {time.time() - t:.0f}s  edge={r['row']['edge_bps']:+.2f} bps (p={r['row']['edge_p']:.3f})")

    sc = pd.DataFrame(rows).set_index("symbol")
    # Поправка на множественные сравнения: семейство = один тест по всем активам
    for col in ("edge_p", "weather_selectivity_p", "liq_selectivity_p"):
        sc[col.replace("_p", "_q")] = stats.benjamini_hochberg(sc[col])
    sc.to_csv(RESULTS / "asset_scorecard.csv")
    (RESULTS / "params.json").write_text(json.dumps(asdict(PARAMS), default=str, indent=2, ensure_ascii=False))

    pd.concat({s: f for s, f in feats.items()}, names=["symbol", "ts"]).to_parquet(RESULTS / "features.parquet")
    write_progress("готово", 1.0, f"{len(rows)} активов за {time.time() - t0:.0f}s")
    print(f"\nГотово за {time.time() - t0:.0f}s -> {RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
