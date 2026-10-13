# Использование как библиотеки

Проект устанавливается как обычный Python-пакет `backtest-strat` (импорт — `backtest_strat`) и подключается к вашему проекту:
свой код стратегий и исследований остается у вас, данные, проверки качества, движок и статистика берутся из библиотеки.

## Установка

```bash
# из локальной папки (правки в библиотеке сразу видны в вашем проекте)
pip install -e /путь/к/backtest_strat

# или как wheel
cd /путь/к/backtest_strat && python -m build     # dist/backtest_strat-0.1.0-py3-none-any.whl
pip install dist/backtest_strat-0.1.0-py3-none-any.whl

# из git
pip install "git+ssh://git@github.com/mark-nikulin/backtest_strat.git"
```

Ядро ставится с минимумом зависимостей (`numpy`, `pandas`, `pyarrow`, `aiohttp`, `httpx`). Остальное — по необходимости:

| Extra | Что добавляет | Когда нужен |
|---|---|---|
| `[api]` | fastapi, uvicorn | HTTP API задач (`bt-api`) |
| `[ui]` | streamlit + api | дашборд и монитор (`bt-dashboard`, `bt-monitor`) |
| `[research]` | matplotlib, scipy, nbformat, nbclient, ipykernel | ноутбуки, графики, точные p-значения |
| `[dev]` | pytest, pytest-cov, build | разработка библиотеки |

```bash
pip install -e "/путь/к/backtest_strat[research,ui]"
```

> Пакет верхнего уровня намеренно легкий: `import backtest_strat.backtest` не тянет fastapi/streamlit/scipy.

## Быстрый старт: своя стратегия на своих барах

```python
import numpy as np, pandas as pd
from backtest_strat.backtest import (BUY, SELL, BacktestEngine, InstrumentSpec, Strategy,
                                     bars_to_events, format_summary, summarize)

class MyAlpha(Strategy):                       # ваш класс, лежит в вашем проекте
    def on_book(self, ctx):
        if ctx.position == 0 and ctx.book.spread() < 0.5:
            ctx.market(BUY, 0.01)

spec = InstrumentSpec("BTC-USDT", tick_size=0.1, lot_size=0.00001, taker_fee=0.001)
events = bars_to_events(bar_ts_ns, close, interval_ns=60 * 10**9, tick_size=0.1, half_spread_bps=0.5)
result = BacktestEngine(MyAlpha(), spec).run_batches([events])
print(format_summary(summarize(result)))
```

Для L2-данных (стакан + сделки) вместо `bars_to_events` — `BacktestEngine(...).run(start, end)`,
который читает нормализованные события из `PIPELINE_PROCESSED_DIR`.

## Данные: скачать, проверить, получить

```bash
bt-data --exchange okx --symbol BTC-USDT ETH-USDT --mode backfill --start 2024-01-01 --end 2024-06-30 \
        --data-type trades --stage all                       # сырье → Parquet → проверки → бары
```

```python
from datetime import date
from backtest_strat.storage.reader import load_derived, load_events
bars = load_derived("bars_1m", "BTC-USDT", date(2024, 1, 1), date(2024, 1, 31)).to_pandas()
```

Тот же конвейер из кода: `backtest_strat.pipeline.run_pipeline(PipelineParams(...))`.

## Публичный интерфейс по подпакетам

| Подпакет | Назначение | Главные имена |
|---|---|---|
| `backtest_strat.backtest` | движок и исполнение | `BacktestEngine`, `Strategy`, `Context`, `InstrumentSpec`, `ScheduledPosition`, `bars_to_events`, `summarize`, `format_summary`, `Portfolio` |
| `backtest_strat.pipeline` | оркестрация этапов | `PipelineParams`, `run_pipeline`, `run_pipeline_async` |
| `backtest_strat.storage.reader` | чтение данных | `load_derived`, `load_events`, `iter_events`, `derived_files` |
| `backtest_strat.fetchers` | загрузчики бирж | `get_fetcher`, `OkxFetcher`, `FETCHERS` (`instrument_specs`, `spread_snapshot`, `list_symbols`) |
| `backtest_strat.normalizers` | нормализация | `get_normalizer`, `OkxNormalizer` |
| `backtest_strat.validation` | проверки качества | `validate_symbol`, `load_reports`, `Thresholds` |
| `backtest_strat.derived` | L1 и бары | `derive_symbol`, `build_bars`, `build_l1` |
| `backtest_strat.research` | утилиты исследований | `stats.edge_ttest`, `stats.placebo_selectivity`, `stats.benjamini_hochberg`, `bars.minute_bars/to_hourly`, `evaluate.fast_pnl/run_schedule`, `market.instrument_specs/spread_snapshot` |
| `backtest_strat.external.weather` | внешние ряды с PIT-лагом | `load_daily`, `load_city` |
| `backtest_strat.utils.sync` | корутины в Jupyter | `run_sync` |

Добавление новой биржи — наследник `BaseExchangeFetcher` + `BaseNormalizer` и запись в реестры `FETCHERS` / `NORMALIZERS`
(см. [ingestion.md](ingestion.md)).

## Консольные команды

| Команда | Что делает |
|---|---|
| `bt-data` | скачать / нормализовать / проверить / построить датасеты (`--stage all`) |
| `bt-backtest` | прогон стратегии `модуль:Класс` из вашего проекта на событиях |
| `bt-schedule` | ночной планировщик (cron), включение/выключение |
| `bt-api` | HTTP API фоновых задач (localhost) |
| `bt-dashboard`, `bt-monitor` | веб-интерфейсы (нужен `[ui]`) |

## Где лежат данные

Относительные пути — от **текущей папки процесса** (папки вашего проекта). Один раз настраиваются окружением:

| Переменная | По умолчанию | Что |
|---|---|---|
| `PIPELINE_DATA_DIR` | `data` | корень |
| `PIPELINE_RAW_DIR` | `$DATA/raw` | сырые архивы |
| `PIPELINE_PROCESSED_DIR` | `$DATA/processed` | нормализованные данные, бары, отчеты |
| `PIPELINE_EXTERNAL_DIR` | `$DATA/external` | внешние ряды (погода) |
| `PIPELINE_LOG_DIR` | `logs` | журналы |
| `PIPELINE_SCHEDULE_CONFIG` | `config/schedule.json` | расписание |

Это позволяет держать данные отдельно от кода (`export PIPELINE_DATA_DIR=/mnt/market`) и делить их между проектами.

## Заметки по версии 0.1

- Пакет называется `backtest_strat`. Прежнее имя `src` оставлено **только в репозитории** как алиас для старых тестов; в wheel его нет.
  Новый код импортирует `backtest_strat`.
- Поддерживается Python ≥ 3.11 (разрабатывается на 3.14).
- Публичный интерфейс — перечисленные выше имена; остальное считайте внутренним и меняемым.
