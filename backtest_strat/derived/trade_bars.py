"""
Бары только из нормализованных сделок (без стакана).

Нужны, когда стакан не скачивали: исследования сезонности/ликвидности на десятках активов
требуют сотен ГБ стакана, а сделки занимают единицы МБ в день.

Сутки UTC D покрываются двумя файлами сделок источника (OKX режет по UTC+8):
файлами D и D+1 — берем из них строки с ts в [D 00:00, D+1 00:00) UTC.
mid_close / spread_close в таких барах пустые (L1 неизвестен).
"""
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from backtest_strat.derived.build import BAR_SCHEMA, KINDS, build_bars
from backtest_strat.normalizers.schema import EVENT_SCHEMA

NS = 1_000_000_000
_EMPTY_L1 = pa.table({"ts": pa.array([], pa.int64()).cast(pa.timestamp("ns", tz="UTC")),
                      "bid_px": pa.array([], pa.float64()), "ask_px": pa.array([], pa.float64())})


def trade_source_files(processed_dir: str, exchange: str, symbol: str, source_day: date) -> List[Path]:
    """Нормализованные сделки одного файла-источника (могут лежать в двух месячных партициях)."""
    root = Path(processed_dir) / exchange / symbol / "trades"
    return sorted(root.glob(f"year=*/month=*/{symbol}-trades-{source_day.isoformat()}.parquet"))


def trade_source_days(processed_dir: str, exchange: str, symbol: str) -> List[date]:
    root = Path(processed_dir) / exchange / symbol / "trades"
    days = {date.fromisoformat(p.stem[-10:]) for p in root.glob("year=*/month=*/*-trades-*.parquet")}
    return sorted(days)


def covered_utc_days(source_days: List[date], offset_days: int = 1) -> List[date]:
    """UTC-сутки, для которых есть оба нужных файла (D и D+1)."""
    have = set(source_days)
    return [d for d in sorted(have) if d + timedelta(days=offset_days) in have]


def read_utc_day_trades(processed_dir: str, exchange: str, symbol: str, day: date) -> Dict[str, np.ndarray]:
    files = []
    for src in (day, day + timedelta(days=1)):
        files += trade_source_files(processed_dir, exchange, symbol, src)
    lo = datetime.combine(day, time(), tzinfo=timezone.utc)
    hi = lo + timedelta(days=1)
    parts = []
    for f in files:
        t = pq.read_table(f, columns=["ts", "side", "price", "size", "msg_id"])
        ts = t["ts"].cast(pa.int64()).to_numpy()
        m = (ts >= int(lo.timestamp() * NS)) & (ts < int(hi.timestamp() * NS))
        if m.any():
            parts.append({"ts": ts[m], "side": t["side"].to_numpy()[m], "price": t["price"].to_numpy()[m],
                          "size": t["size"].to_numpy()[m], "msg_id": t["msg_id"].to_numpy()[m]})
    if not parts:
        return {k: np.array([]) for k in ("ts", "side", "price", "size")}
    cat = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    order = np.lexsort((cat["msg_id"], cat["ts"]))  # тот же порядок, что у потока событий: ts, затем trade_id
    return {k: cat[k][order] for k in ("ts", "side", "price", "size")}


def build_trade_bars_day(processed_dir: str, exchange: str, symbol: str, day: date) -> Dict[str, pa.Table]:
    trades = read_utc_day_trades(processed_dir, exchange, symbol, day)
    return {
        "bars_1s": build_bars(trades, KINDS["bars_1s"][2], _EMPTY_L1),
        "bars_1m": build_bars(trades, KINDS["bars_1m"][2], _EMPTY_L1),
    }
