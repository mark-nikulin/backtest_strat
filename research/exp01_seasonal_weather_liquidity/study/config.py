"""
Параметры эксперимента. ЗАФИКСИРОВАНЫ ДО просмотра результатов (pre-registration):
менять их после первого прогона можно только с записью в PROBLEMS.md и пометкой «пост-фактум».
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Tuple

UNIVERSE: Tuple[str, ...] = (
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT", "LTC-USDT", "ADA-USDT", "AVAX-USDT",
    "LINK-USDT", "DOT-USDT", "ATOM-USDT", "ETC-USDT", "BCH-USDT", "UNI-USDT", "FIL-USDT", "TRX-USDT",
)


@dataclass(frozen=True)
class Params:
    # --- данные ---
    exchange: str = "okx"
    processed_dir: str = "data/processed"
    start: date = date(2024, 1, 1)
    end: date = date(2024, 6, 29)            # последний UTC-день, покрытый сделками D и D+1 (скачано до 06-30)
    split: date = date(2024, 4, 1)           # граница двух подпериодов для проверки устойчивости

    # --- сезонность: час суток (UTC), скользящее окно по предыдущим дням ---
    seasonal_window_days: int = 28
    seasonal_t_threshold: float = 1.0

    # --- погода: аномалия температуры vs скользящее среднее, композит по городам ---
    weather_window_days: int = 30
    weather_z_threshold: float = 1.0         # фильтр M1: торговать только в «экстремальную» погоду |z| >= порога
    weather_lag_days: int = 1                # значение за сутки D-1 доступно в сутки D

    # --- ликвидность: объем за предыдущие 24ч / средний суточный объем за 30 дней ---
    liquidity_window_days: int = 30
    liquidity_threshold: float = 1.0         # фильтр M2: торговать только при rel_liq < порога (тонкий рынок)

    # --- исполнение / издержки ---
    notional: float = 1_000.0                # USDT на позицию
    taker_fee_bps: float = 10.0              # OKX spot, базовый уровень
    taker_fee_low_bps: float = 2.0           # сценарий высокого тира / маркет-мейкерских программ
    spread_half_min_bps: float = 0.005       # пол: у BTC спред = 1 тик ~ 0.02 б.п.
    spread_half_max_bps: float = 15.0

    # --- статистика ---
    placebo_draws: int = 500
    placebo_min_shift_days: int = 7
    seed: int = 20240101


PARAMS = Params()
VARIANTS = ("M0_seasonal", "M1_seasonal_weather", "M2_seasonal_liquidity", "M3_all")
