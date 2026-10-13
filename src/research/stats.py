"""
Статистика исследования. Принцип: почасовые доходности зависимы (волатильность кластеризуется, сезонность
привязана к дням), поэтому единица независимости — СУТКИ, а значимость оценивается плацебо-перестановками
с циклическим сдвигом сигнала на целое число суток (сохраняет автокорреляцию и внутрисуточную структуру).
"""
from typing import Dict

import numpy as np
import pandas as pd


def daily_tstat(hourly_pnl: pd.Series) -> float:
    """t-статистика среднего суточного PnL (суммы по UTC-суткам)."""
    d = hourly_pnl.groupby(hourly_pnl.index.normalize()).sum()
    d = d[np.isfinite(d)]
    if len(d) < 5 or d.std() == 0:
        return float("nan")
    return float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d))))


def sharpe_hourly(hourly_pnl: pd.Series, notional: float) -> float:
    r = hourly_pnl / notional
    if r.std() == 0 or len(r) < 24:
        return float("nan")
    return float(r.mean() / r.std(ddof=1) * np.sqrt(24 * 365))


def _roll_days(x: np.ndarray, days: int) -> np.ndarray:
    return np.roll(x, days * 24)


def placebo_edge(contrib: np.ndarray, signal: np.ndarray, ret: np.ndarray, rng, draws: int, min_shift: int,
                 lookback_days: int = 0) -> Dict[str, float]:
    """
    Статистика: средняя валовая доходность сигнала S*ret на часах, где S != 0 (в б.п.).
    Нулевая гипотеза: сигнал не связан с будущей доходностью -> сдвигаем сигнал на случайное число суток.

    lookback_days — окно, по которому построен сигнал. ОБЯЗАТЕЛЬНО указывать для сигналов из скользящего окна:
    при циклическом сдвиге «за конец» ряда сигнал оказывается построенным по данным ПОСЛЕ доходности, которую
    он умножает (его окно захватывает ее саму), и плацебо получает ложную положительную связь. Это раздувало разброс
    нулевого распределения втрое и делало тест слепым (мощность 0 при эффекте 15 б.п.). Допустимые сдвиги:
    [min_shift, n_days - lookback_days - 1].
    """
    n_days = len(ret) // 24
    max_shift = max(min_shift + 1, n_days - lookback_days - 1)
    active = signal != 0
    if active.sum() < 50:
        return {"edge_bps": float("nan"), "edge_excess_bps": float("nan"), "p": float("nan"), "n_active": int(active.sum())}
    edge = float(contrib[active].mean() * 1e4)
    null = np.empty(draws)
    for i in range(draws):
        k = int(rng.integers(min_shift, max_shift))
        s = _roll_days(signal, k)
        a = s != 0
        null[i] = (s[a] * ret[a]).mean() * 1e4 if a.sum() else 0.0
    # Плацебо центрировано не на нуле: у несимметричного сигнала (больше длинных, чем коротких) на активе с дрейфом
    # среднее связи сигнала и доходности ненулевое и без всякой сезонности. Сравниваем отклонение от центра плацебо.
    center = float(null.mean())
    p = (1 + np.sum(np.abs(null - center) >= abs(edge - center))) / (1 + draws)
    return {"edge_bps": edge, "edge_excess_bps": edge - center, "p": float(p), "n_active": int(active.sum())}


def edge_ttest(signal: np.ndarray, ret: np.ndarray, valid: np.ndarray, day_ids: np.ndarray = None) -> Dict[str, float]:
    """
    t-тест валовой связи сигнала с доходностью по суткам.

    Вклад часа: S_t * (r_t - r_среднее). Сигнал S_t построен только по прошлому, поэтому при отсутствии предсказуемости
    вклад — разность мартингала с нулевым средним; вычитание среднего убирает «ложный edge» от общего дрейфа актива
    при несимметричном сигнале. Единица независимости — сутки (внутрисуточная зависимость и кластеры волатильности
    гасятся суммированием). Проверено на случайных блужданиях: доля ложных срабатываний близка к номиналу.
    """
    n = len(ret)
    day_ids = np.arange(n) // 24 if day_ids is None else day_ids
    m = valid.astype(bool)
    if m.sum() < 48:
        return {"edge_bps": np.nan, "edge_excess_bps": np.nan, "t": np.nan, "p": np.nan, "n_active": int((signal != 0).sum()), "n_days": 0}
    rbar = ret[m].mean()
    c = np.where(m, signal * (ret - rbar), 0.0)
    days = np.unique(day_ids[m])
    d = np.array([c[(day_ids == j) & m].sum() for j in days]) if len(days) < 400 else \
        np.bincount(np.searchsorted(days, day_ids[m]), weights=c[m], minlength=len(days))
    J = len(d)
    sd = d.std(ddof=1)
    t = float(d.mean() / (sd / np.sqrt(J))) if sd > 0 and J > 2 else float("nan")
    from math import erf, sqrt
    # двусторонний p по t-распределению (через scipy при наличии, иначе нормальное приближение)
    try:
        from scipy import stats as _st
        p = float(2 * _st.t.sf(abs(t), J - 1))
    except Exception:
        p = float(2 * (1 - 0.5 * (1 + erf(abs(t) / sqrt(2))))) if np.isfinite(t) else float("nan")
    active = (signal != 0) & m
    return {
        "edge_bps": float((signal * ret)[active].mean() * 1e4) if active.any() else float("nan"),
        "edge_excess_bps": float((signal * (ret - rbar))[active].mean() * 1e4) if active.any() else float("nan"),
        "t": t, "p": p, "n_active": int(active.sum()), "n_days": J,
    }


def placebo_selectivity(contrib: np.ndarray, signal: np.ndarray, mask: np.ndarray, rng, draws: int, min_shift: int) -> Dict[str, float]:
    """
    Статистика: E[S*ret | фильтр включен] - E[S*ret | фильтр выключен] на часах, где S != 0 (б.п.).
    Отвечает на вопрос «фильтр выбирает лучшие часы?», а не «фильтр прибылен?».
    Нулевая гипотеза: фильтр не связан с качеством сигнала -> сдвигаем маску на случайное число суток.

    ОГРАНИЧЕНИЕ: сдвиг идет на ЦЕЛЫЕ сутки, поэтому строго суточно-периодическая маска (напр. «первые 12 часов каждых
    суток») при сдвиге не меняется, плацебо совпадает с наблюдением, и p = 1 независимо от эффекта. Метод подходит для
    масок, привязанных к событиям/режимам (погода, относительная ликвидность), но НЕ для фильтров «час суток».
    Для фильтров по часу суток используйте t-тест вклада в двух подвыборках.
    """
    n_days = len(contrib) // 24
    act = signal != 0

    def stat(m: np.ndarray) -> float:
        on, off = act & m, act & ~m
        if on.sum() < 30 or off.sum() < 30:
            return np.nan
        return float((contrib[on].mean() - contrib[off].mean()) * 1e4)

    obs = stat(mask)
    if np.isnan(obs):
        return {"selectivity_bps": float("nan"), "p": float("nan"), "n_on": int((act & mask).sum())}
    null = np.array([stat(_roll_days(mask, int(rng.integers(min_shift, max(min_shift + 1, n_days - min_shift)))))
                     for _ in range(draws)])
    null = null[np.isfinite(null)]
    p = (1 + np.sum(np.abs(null) >= abs(obs))) / (1 + len(null))
    return {"selectivity_bps": obs, "p": float(p), "n_on": int((act & mask).sum())}


def cluster_ols(y: np.ndarray, X: np.ndarray, groups: np.ndarray) -> Dict[str, np.ndarray]:
    """OLS с кластерно-робастными ошибками (CR1) по группам (сутки)."""
    XtX_inv = np.linalg.pinv(X.T @ X)
    beta = XtX_inv @ X.T @ y
    resid = y - X @ beta
    meat = np.zeros((X.shape[1], X.shape[1]))
    uniq = np.unique(groups)
    for g in uniq:
        idx = groups == g
        sg = X[idx].T @ resid[idx]
        meat += np.outer(sg, sg)
    G, n, k = len(uniq), len(y), X.shape[1]
    scale = G / (G - 1) * (n - 1) / (n - k) if G > 1 and n > k else 1.0
    cov = scale * XtX_inv @ meat @ XtX_inv
    se = np.sqrt(np.diag(cov))
    return {"beta": beta, "se": se, "t": beta / np.where(se > 0, se, np.nan)}


def interaction_regression(f: pd.DataFrame) -> Dict[str, float]:
    """
    ret(б.п.) ~ a + b0*S + b1*S*W + b2*S*(L-1),  S — сезонный сигнал, W — z погоды, L — относительная ликвидность.
    b1/b2 — «меняет ли погода/ликвидность отдачу сезонного сигнала». Кластеры — сутки.
    """
    d = f[["ret", "seasonal_sig", "weather_z", "rel_liq"]].dropna()
    d = d[d["seasonal_sig"] != 0]
    if len(d) < 200:
        return {k: np.nan for k in ("b_seasonal", "t_seasonal", "b_weather", "t_weather", "b_liq", "t_liq", "n")} | {"n": len(d)}
    S = d["seasonal_sig"].to_numpy()
    W = (d["weather_z"] - d["weather_z"].mean()).to_numpy()
    L = (np.clip(d["rel_liq"], 0, 5) - 1).to_numpy()
    X = np.column_stack([np.ones(len(d)), S, S * W, S * L])
    out = cluster_ols(d["ret"].to_numpy() * 1e4, X, d.index.normalize().to_numpy().astype("int64"))
    return {"b_seasonal": out["beta"][1], "t_seasonal": out["t"][1], "b_weather": out["beta"][2], "t_weather": out["t"][2],
            "b_liq": out["beta"][3], "t_liq": out["t"][3], "n": len(d)}


def benjamini_hochberg(p: pd.Series) -> pd.Series:
    """FDR-поправка по семейству тестов; NaN игнорируются."""
    q = pd.Series(np.nan, index=p.index)
    valid = p.dropna().sort_values()
    m = len(valid)
    if m == 0:
        return q
    adj = (valid.values * m / np.arange(1, m + 1))
    adj = np.minimum.accumulate(adj[::-1])[::-1]
    q.loc[valid.index] = np.clip(adj, 0, 1)
    return q
