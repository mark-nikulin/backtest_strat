"""
Погода как внешний фактор (Open-Meteo Archive API: реанализ ERA5, без ключа).

ВАЖНО про «точку во времени» (point-in-time):
  * Значение дневной переменной за сутки D (UTC) становится известно только после конца суток D.
    `load_daily(..., lag_days=1)` по умолчанию сдвигает ряд на день: в строке D лежит значение за D-1.
    Без сдвига сезонная/погодная модель подсматривает в будущее на 0–24 часа.
  * Реанализ — это не прогноз, который был доступен в тот момент. Для поиска закономерностей это допустимо,
    для оценки реальной торговой пригодности — нет (нужны архивные прогнозы).

Кэш: data/external/weather/<city>.parquet; докачиваются только недостающие даты.
"""
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
CACHE_DIR = Path("data/external/weather")

CITIES: Dict[str, tuple] = {  # (lat, lon): крупнейшие торговые часовые пояса
    "new_york": (40.71, -74.01),
    "london": (51.51, -0.13),
    "tokyo": (35.68, 139.69),
    "hong_kong": (22.32, 114.17),
}
VARIABLES = ("temperature_2m_mean",)


def _fetch(lat: float, lon: float, start: date, end: date, variables: Iterable[str], retries: int = 5) -> pd.DataFrame:
    params = {
        "latitude": lat, "longitude": lon, "start_date": start.isoformat(), "end_date": end.isoformat(),
        "daily": ",".join(variables), "timezone": "UTC",
    }
    for attempt in range(retries):
        r = httpx.get(ARCHIVE_URL, params=params, timeout=60)
        if r.status_code == 200:
            d = r.json()["daily"]
            return pd.DataFrame(d).rename(columns={"time": "date"}).assign(date=lambda x: pd.to_datetime(x["date"]))
        if r.status_code in (429, 500, 502, 503, 504):
            delay = min(60, 2 ** attempt * 2)
            logger.warning(f"Open-Meteo HTTP {r.status_code}, повтор через {delay}s")
            time.sleep(delay)
            continue
        r.raise_for_status()
    raise RuntimeError(f"Open-Meteo недоступен после {retries} попыток")


def load_city(city: str, start: date, end: date, variables: Iterable[str] = VARIABLES, cache_dir: Path = CACHE_DIR) -> pd.DataFrame:
    """Дневные значения города за [start, end] (без сдвига, как есть в источнике)."""
    lat, lon = CITIES[city]
    variables = list(variables)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{city}.parquet"
    cached = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=["date"] + variables)
    have = set(pd.to_datetime(cached["date"]).dt.date) if len(cached) else set()
    need = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    missing = [d for d in need if d not in have]
    if missing or any(v not in cached.columns for v in variables):
        fresh = _fetch(lat, lon, min(missing, default=start), max(missing, default=end), variables)
        cached = pd.concat([cached, fresh]).drop_duplicates("date", keep="last").sort_values("date")
        cached.to_parquet(path, index=False)
    df = cached[(cached["date"] >= pd.Timestamp(start)) & (cached["date"] <= pd.Timestamp(end))]
    return df.reset_index(drop=True)


def load_daily(
    start: date,
    end: date,
    cities: Optional[List[str]] = None,
    variables: Iterable[str] = VARIABLES,
    lag_days: int = 1,
) -> pd.DataFrame:
    """
    Широкая таблица: индекс — дата ИСПОЛЬЗОВАНИЯ, колонки `<city>__<variable>`.
    Значение в строке D — это наблюдение за сутки D - lag_days (известное к началу суток D).
    """
    cities = cities or list(CITIES)
    # запрашиваем с запасом назад, чтобы после сдвига первая строка не была пустой
    frames = []
    for c in cities:
        df = load_city(c, start - timedelta(days=lag_days), end, variables).set_index("date")
        df.columns = [f"{c}__{v}" for v in df.columns]
        frames.append(df)
    wide = pd.concat(frames, axis=1).sort_index()
    wide.index = wide.index + pd.Timedelta(days=lag_days)
    return wide.loc[pd.Timestamp(start): pd.Timestamp(end)]
