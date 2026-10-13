"""Загрузка баров для исследований (pandas) и агрегация в часовые."""
from datetime import date
from typing import Optional

import pandas as pd

from src.storage.reader import load_derived


def minute_bars(symbol: str, start: date, end: date, exchange: str = "okx", processed_dir: str = "data/processed") -> pd.DataFrame:
    """1-минутные бары, индекс — начало бара (UTC, tz-naive). Только интервалы со сделками."""
    df = load_derived("bars_1m", symbol, start, end, exchange=exchange, processed_dir=processed_dir).to_pandas()
    if df.empty:
        return df
    df["ts"] = df["ts"].dt.tz_convert("UTC").dt.tz_localize(None)
    return df.set_index("ts").sort_index()


def to_hourly(m: pd.DataFrame, rule: str = "1h") -> pd.DataFrame:
    """Агрегация минутных баров. close — последняя цена периода (если сделок не было — цена «стоит»)."""
    if m.empty:
        return m
    g = m.assign(notional=m["volume"] * m["vwap"]).resample(rule)
    h = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
        "volume": g["volume"].sum(), "notional": g["notional"].sum(), "trades": g["trades"].sum(),
        "minutes_active": g["close"].count(),
    })
    h["close"] = h["close"].ffill()
    h["open"] = h["open"].fillna(h["close"].shift(1)).fillna(h["close"])
    return h
