import math
from datetime import datetime, timezone
from typing import Dict

from backtest_strat.backtest.engine import BacktestResult

MINUTES_PER_YEAR = 365 * 24 * 60  # крипта торгуется круглосуточно


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat(timespec="seconds")


def summarize(result: BacktestResult) -> Dict[str, float]:
    p = result.portfolio
    mark = result.final_mid if result.final_mid is not None else p.avg_price
    curve = [eq for _, eq in p.equity_curve]

    max_dd, peak = 0.0, -math.inf
    for eq in curve:
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)

    # Sharpe по приращениям PnL (не доходностям): капитал в бэктесте не задан
    diffs = [b - a for a, b in zip(curve, curve[1:])]
    sharpe = float("nan")
    if len(diffs) > 1:
        mean = sum(diffs) / len(diffs)
        var = sum((d - mean) ** 2 for d in diffs) / (len(diffs) - 1)
        if var > 0:
            step_min = (p.equity_curve[1][0] - p.equity_curve[0][0]) / 60e9
            sharpe = mean / math.sqrt(var) * math.sqrt(MINUTES_PER_YEAR / step_min)

    n_fills = len(p.fills)
    return {
        "period": f"{_iso(result.start_ts)} → {_iso(result.end_ts)}",
        "total_pnl": p.equity(mark),
        "realized_pnl": p.realized_pnl,
        "unrealized_pnl": p.unrealized_pnl(mark),
        "fees": p.fees,
        "final_position": p.position,
        "fills": n_fills,
        "maker_share": p.maker_fills / n_fills if n_fills else float("nan"),
        "orders_sent": result.orders_sent,
        # доля ордеров, получивших хотя бы одно исполнение (market-ордер по нескольким уровням = несколько fills)
        "order_fill_ratio": len({f.order_id for f in p.fills}) / result.orders_sent if result.orders_sent else float("nan"),
        "volume": p.volume,
        "notional": p.notional,
        "max_drawdown": max_dd,
        "sharpe_annualized": sharpe,
        "events": result.events,
        "events_per_sec": result.events / result.wall_seconds if result.wall_seconds else float("nan"),
    }


def format_summary(stats: Dict[str, float]) -> str:
    lines = []
    for k, v in stats.items():
        if isinstance(v, float):
            v = f"{v:,.4f}" if abs(v) < 1e6 else f"{v:,.0f}"
        lines.append(f"  {k:<20} {v}")
    return "\n".join(lines)
