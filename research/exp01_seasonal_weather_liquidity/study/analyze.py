"""Полный анализ одного актива: признаки -> варианты -> движок -> статистика."""
from typing import Dict, Optional

import numpy as np
import pandas as pd

from . import data, execution, features, stats
from .config import PARAMS, VARIANTS, Params

BPS = 1e4


def analyze_asset(symbol: str, weather: pd.DataFrame, spec_raw: dict, snapshot_spread_bps: float, p: Params = PARAMS,
                  run_engine: bool = True, rng: Optional[np.random.Generator] = None) -> Dict:
    rng = rng or np.random.default_rng(p.seed)
    minute = data.load_minute_bars(symbol, p)
    if minute.empty:
        return {"symbol": symbol, "error": "нет данных"}
    hourly = data.to_hourly(minute)
    f = features.build_features(hourly, weather, p)
    pos = features.variant_positions(f, p)
    half_bps = execution.half_spread_bps(snapshot_spread_bps, spec_raw["tick_size"], float(hourly["close"].median()), p)
    # Котировки движка округляются к сетке цены (bid вниз, ask вверх): в среднем +0.5 тика на сторону.
    # Без этой добавки быстрая оценка занижает издержки у активов с крупным тиком (PROBLEMS.md, P11).
    tick_bps = spec_raw["tick_size"] / float(hourly["close"].median()) * BPS
    cost_main = p.taker_fee_bps + half_bps + 0.5 * tick_bps
    cost_low = p.taker_fee_low_bps + half_bps + 0.5 * tick_bps

    valid = f["seasonal_mu"].notna() & f["rel_liq"].notna() & f["weather_z"].notna()
    ret = f["ret"].where(valid)
    row: Dict = {
        "symbol": symbol, "hours": int(valid.sum()), "active_hours_share": float((hourly["minutes_active"] > 0).mean()),
        "median_hourly_notional_usd": float(hourly["notional"].median()),
        "amihud_median": float(f["amihud"].median()), "half_spread_bps": half_bps, "snapshot_spread_bps": snapshot_spread_bps,
        "tick_bps": spec_raw["tick_size"] / float(hourly["close"].median()) * BPS,
    }

    contrib = (pos["M0_seasonal"] * np.expm1(ret)).fillna(0.0).to_numpy()
    sig = pos["M0_seasonal"].where(valid, 0.0).to_numpy()
    r = np.expm1(ret.fillna(0.0)).to_numpy()
    nd, ms = p.placebo_draws, p.placebo_min_shift_days

    day_ids = pd.factorize(f.index.normalize())[0]
    e = stats.edge_ttest(sig, r, valid.to_numpy(), day_ids)
    row.update({"edge_bps": e["edge_bps"], "edge_excess_bps": e["edge_excess_bps"], "edge_t": e["t"], "edge_p": e["p"],
                "active_hours": e["n_active"], "edge_days": e["n_days"]})
    w_mask = (f["weather_z"].abs() >= p.weather_z_threshold).fillna(False).to_numpy()
    l_mask = (f["rel_liq"] < p.liquidity_threshold).fillna(False).to_numpy()
    sw = stats.placebo_selectivity(contrib, sig, w_mask, rng, nd, ms)
    sl = stats.placebo_selectivity(contrib, sig, l_mask, rng, nd, ms)
    row.update({"weather_selectivity_bps": sw["selectivity_bps"], "weather_selectivity_p": sw["p"], "weather_on_hours": sw["n_on"],
                "liq_selectivity_bps": sl["selectivity_bps"], "liq_selectivity_p": sl["p"], "liq_on_hours": sl["n_on"]})
    row.update({f"reg_{k}": v for k, v in stats.interaction_regression(f[valid]).items()})

    half = p.split
    for v in VARIANTS:
        pv = pos[v].where(valid, 0.0)
        gross = execution.fast_pnl(ret, pv, 0.0, p.notional)
        net = execution.fast_pnl(ret, pv, cost_main, p.notional)
        net_low = execution.fast_pnl(ret, pv, cost_low, p.notional)
        tag = v.split("_")[0]
        row[f"{tag}_gross_bps_per_hour"] = float(gross[valid].mean() / p.notional * BPS)
        row[f"{tag}_net_bps_per_hour"] = float(net[valid].mean() / p.notional * BPS)
        row[f"{tag}_net_pnl_usd"] = float(net[valid].sum())
        row[f"{tag}_net_low_fee_pnl_usd"] = float(net_low[valid].sum())
        row[f"{tag}_net_t_daily"] = stats.daily_tstat(net[valid])
        row[f"{tag}_net_sharpe"] = stats.sharpe_hourly(net[valid], p.notional)
        row[f"{tag}_trades"] = int((pv.fillna(0).diff().abs() > 0).sum())
        row[f"{tag}_exposure"] = float((pv != 0).mean())
        h1 = gross[valid & (gross.index < pd.Timestamp(half))]
        h2 = gross[valid & (gross.index >= pd.Timestamp(half))]
        row[f"{tag}_gross_bps_h1"] = float(h1.mean() / p.notional * BPS) if len(h1) else np.nan
        row[f"{tag}_gross_bps_h2"] = float(h2.mean() / p.notional * BPS) if len(h2) else np.nan

        if run_engine:
            res = execution.run_engine(minute, pv, spec_raw, symbol, half_bps, p.taker_fee_bps, p)
            row[f"{tag}_engine_net_pnl_usd"] = float(res["net_pnl"])
            row[f"{tag}_engine_fills"] = int(res["fills"])
            row[f"{tag}_engine_fees_usd"] = float(res["fees"])
    return {"row": row, "features": f, "positions": pos}
