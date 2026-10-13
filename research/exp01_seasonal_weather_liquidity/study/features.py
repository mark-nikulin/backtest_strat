"""
Признаки на часовой сетке. Строка t — час, НАЧИНАЮЩИЙСЯ в t; решение принимается в момент t
по информации, известной до t. `ret` — доходность, которую позиция заработает ВНУТРИ часа t.

Правило против заглядывания в будущее: каждый признак — функция только прошлых значений (shift/rolling по прошлому),
а внешние ряды (погода) уже сдвинуты в источнике на lag_days.
"""
import numpy as np
import pandas as pd

from .config import PARAMS, Params


def seasonal_profile(ret: pd.Series, window_days: int, t_threshold: float) -> pd.DataFrame:
    """Среднее той же доходности в тот же час суток за предыдущие N дней и его t-статистика."""
    hod = ret.index.hour
    grouped = ret.groupby(hod)
    mu = grouped.transform(lambda s: s.shift(1).rolling(window_days, min_periods=window_days).mean())
    sd = grouped.transform(lambda s: s.shift(1).rolling(window_days, min_periods=window_days).std())
    t = mu / (sd / np.sqrt(window_days))
    sig = np.where(t.abs() >= t_threshold, np.sign(mu), 0.0)
    return pd.DataFrame({"seasonal_mu": mu, "seasonal_t": t, "seasonal_sig": np.nan_to_num(sig)}, index=ret.index)


def weather_composite(w: pd.DataFrame, window_days: int) -> pd.Series:
    """Композитная z-аномалия температуры: (T - среднее за N дней) / std за N дней, затем среднее по городам."""
    z = (w - w.rolling(window_days, min_periods=window_days).mean().shift(1)) / \
        w.rolling(window_days, min_periods=window_days).std().shift(1)
    return z.mean(axis=1).rename("weather_z")


def relative_liquidity(notional: pd.Series, window_days: int) -> pd.Series:
    """Объем за предыдущие 24ч / средний суточный объем за предыдущие N дней (< 1 — рынок тоньше обычного)."""
    past = notional.shift(1)
    last24 = past.rolling(24, min_periods=24).sum()
    base = past.rolling(window_days * 24, min_periods=window_days * 24).sum() / window_days
    return (last24 / base.replace(0, np.nan)).rename("rel_liq")


def build_features(hourly: pd.DataFrame, weather: pd.DataFrame, p: Params = PARAMS) -> pd.DataFrame:
    f = pd.DataFrame(index=hourly.index)
    f["close"] = hourly["close"]
    f["ret"] = np.log(hourly["close"] / hourly["close"].shift(1))     # доходность внутри часа t
    f["notional"] = hourly["notional"]
    f["amihud"] = f["ret"].abs() / hourly["notional"].replace(0, np.nan)

    f = f.join(seasonal_profile(f["ret"], p.seasonal_window_days, p.seasonal_t_threshold))
    f["rel_liq"] = relative_liquidity(f["notional"], p.liquidity_window_days)

    wz = weather_composite(weather, p.weather_window_days)
    f["weather_z"] = wz.reindex(f.index.normalize()).to_numpy()       # значение дня на все часы дня
    return f


def variant_positions(f: pd.DataFrame, p: Params = PARAMS) -> pd.DataFrame:
    """Целевая позиция (-1/0/+1) для каждого варианта. NaN-признаки => вне рынка (0)."""
    s = f["seasonal_sig"].fillna(0.0)
    m_w = (f["weather_z"].abs() >= p.weather_z_threshold).fillna(False)
    m_l = (f["rel_liq"] < p.liquidity_threshold).fillna(False)
    return pd.DataFrame({
        "M0_seasonal": s,
        "M1_seasonal_weather": s.where(m_w, 0.0),
        "M2_seasonal_liquidity": s.where(m_l, 0.0),
        "M3_all": s.where(m_w & m_l, 0.0),
    }, index=f.index)
