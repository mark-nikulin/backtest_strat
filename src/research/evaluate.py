"""Оценка позиций: быстрая векторная (для плацебо/перебора) и через движок (для итоговых цифр)."""
from typing import Optional

import numpy as np
import pandas as pd

from src.backtest import BacktestEngine, InstrumentSpec, ScheduledPosition, bars_to_events

NS = 10 ** 9


def fast_pnl(ret: pd.Series, pos: pd.Series, cost_bps_per_side: float, notional: float) -> pd.Series:
    """
    Почасовой PnL в USDT: pos_t * простая доходность − издержки за каждое изменение позиции.
    ret — лог-доходность внутри периода t; pos_t принята ДО периода t (решение по прошлой информации).
    """
    pos = pos.fillna(0.0)
    simple = np.expm1(ret.fillna(0.0))
    turnover = (pos - pos.shift(1).fillna(0.0)).abs()
    return notional * (pos * simple - turnover * cost_bps_per_side / 1e4)


def run_schedule(
    minute: pd.DataFrame,
    positions: pd.Series,
    symbol: str,
    tick_size: float,
    lot_size: float,
    half_spread_bps: float,
    taker_fee_bps: float,
    notional: float,
    order_latency_ms: float = 3.0,
    equity_sample_ns: int = 3600 * NS,
) -> dict:
    """
    Прогон расписания позиций через движок проекта на синтетическом стакане из минутных баров.
    positions — Series(-1/0/+1) с индексом = момент принятия решения (начало периода).
    """
    spec = InstrumentSpec(
        symbol=symbol, tick_size=tick_size, lot_size=lot_size,
        maker_fee=taker_fee_bps / 1e4, taker_fee=taker_fee_bps / 1e4,
        feed_latency_ns=0, order_latency_ns=int(order_latency_ms * 1e6),
    )
    ts = minute.index.as_unit("ns").asi8  # pandas 3: разрешение индекса не обязательно ns
    events = bars_to_events(ts, minute["close"].to_numpy(), 60 * NS, tick_size, half_spread_bps)
    sched = {int(t.value): int(v) for t, v in positions.items()}
    engine = BacktestEngine(ScheduledPosition(sched, notional), spec, equity_sample_ns=equity_sample_ns)
    res = engine.run_batches([events])
    port = res.portfolio
    curve = pd.Series({pd.Timestamp(t, unit="ns"): eq for t, eq in port.equity_curve}).sort_index()
    return {"net_pnl": port.equity(res.final_mid), "fees": port.fees, "fills": len(port.fills),
            "notional_traded": port.notional, "equity_hourly": curve, "result": res}
