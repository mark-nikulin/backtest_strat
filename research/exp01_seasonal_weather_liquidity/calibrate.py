#!/usr/bin/env python3
"""
Калибровка статистических тестов эксперимента на СИНТЕТИКЕ с известной истиной.
Отвечает на вопрос, который обязателен ДО интерпретации результатов: «умеет ли тест видеть эффект и не выдумывает ли его?»

    python research/exp01_seasonal_weather_liquidity/calibrate.py   ->  results/calibration.json

Нулевые сценарии: случайное блуждание с тяжелыми хвостами (t4) ± дрейф ± кластеры волатильности.
Шум 57 б.п./час соответствует реальной часовой волатильности BTC за период (56 б.п.).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

from study import features  # noqa: E402
from study.config import PARAMS as P  # noqa: E402
from backtest_strat.research import stats  # noqa: E402

IDX = pd.date_range("2024-01-01", periods=4344, freq="h")


def _series(rng, effect_bps=0.0, drift_bps=0.0, garch=False, hours=(3, 9, 15, 21)):
    hod = np.zeros(24)
    hod[list(hours)] = effect_bps / 1e4
    z = rng.standard_t(4, len(IDX))
    if garch:
        z = z * np.repeat(np.exp(rng.normal(0, 0.5, len(IDX) // 24)), 24)
    return pd.Series(z * 0.004 + hod[IDX.hour] + drift_bps / 1e4, index=IDX)


def edge_test(sims=1000, **kw) -> dict:
    rng = np.random.default_rng(1)
    ps = []
    for _ in range(sims):
        ret = _series(rng, **kw)
        prof = features.seasonal_profile(ret, P.seasonal_window_days, P.seasonal_t_threshold)
        valid = prof["seasonal_mu"].notna().to_numpy()
        sig = prof["seasonal_sig"].where(valid, 0.0).to_numpy()
        ps.append(stats.edge_ttest(sig, np.expm1(ret.to_numpy()), valid)["p"])
    ps = np.array(ps)
    return {"rejects_p05": float(np.mean(ps < 0.05)), "rejects_p10": float(np.mean(ps < 0.10)), "sims": sims}


def selectivity_test(sims=400, effect_bps=0.0, **kw) -> dict:
    rng = np.random.default_rng(2)
    ps = []
    for _ in range(sims):
        ret = _series(rng, **kw)
        prof = features.seasonal_profile(ret, P.seasonal_window_days, P.seasonal_t_threshold)
        valid = prof["seasonal_mu"].notna().to_numpy()
        sig = prof["seasonal_sig"].where(valid, 0.0).to_numpy()
        r = np.expm1(ret.where(valid).fillna(0.0)).to_numpy()
        mask = np.repeat(np.abs(rng.normal(size=len(IDX) // 24 + 1)) >= 1.0, 24)[: len(IDX)]
        contrib = sig * r
        if effect_bps:
            contrib = np.where(mask & (sig != 0), contrib + effect_bps / 1e4 * np.abs(sig), contrib)
        p = stats.placebo_selectivity(contrib, sig, mask, rng, 150, 7)["p"]
        ps.append(p)
    ps = np.array([x for x in ps if np.isfinite(x)])
    return {"rejects_p05": float(np.mean(ps < 0.05)), "rejects_p10": float(np.mean(ps < 0.10)), "sims": len(ps)}


def main() -> None:
    null = {"случайное блуждание": {}, "дрейф 3 б.п./ч": {"drift_bps": 3.0}, "кластеры волатильности": {"garch": True},
            "дрейф + кластеры": {"drift_bps": 3.0, "garch": True}}
    out = {"edge_ttest": {"ложные срабатывания": {}, "мощность": {}}, "selectivity": {"ложные срабатывания": {}, "мощность": {}}}
    for name, kw in null.items():
        out["edge_ttest"]["ложные срабатывания"][name] = edge_test(**kw)
        out["selectivity"]["ложные срабатывания"][name] = selectivity_test(**{("drift_bps" if k == "drift_bps" else k): v for k, v in kw.items()})
        print("null", name, out["edge_ttest"]["ложные срабатывания"][name], out["selectivity"]["ложные срабатывания"][name])
    for eff in (5, 15, 30):
        out["edge_ttest"]["мощность"][f"{eff} б.п. в 4 часах из 24"] = edge_test(sims=500, effect_bps=eff)
    for eff in (10, 25):
        out["selectivity"]["мощность"][f"фильтр добавляет {eff} б.п./час"] = selectivity_test(effect_bps=eff)
    (HERE / "results" / "calibration.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
