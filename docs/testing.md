# Стратегия тестирования и отчет о покрытии кодовой базы

В данном документе описаны принципы организации тестового набора проекта, команды запуска тестов и актуальная сводка покрытия кода (`pytest-cov`).

---

## 1. Команды запуска тестов

### Запуск основного набора (быстрые тесты без сети)
По умолчанию все внешние HTTP-запросы мокаются локальным сервером или фикстурами.
```bash
.venv/bin/python -m pytest tests/ -m "not network" -q
```

### Запуск с расчетом покрытия (`pytest-cov`)
```bash
.venv/bin/python -m pytest tests/ --cov=backtest_strat --cov-report=term-missing
```

### Запуск интеграционных тестов с реальной сетью
Для тестов, обращающихся к публичным CDN OKX или Open-Meteo, используется маркер `network`:
```bash
.venv/bin/python -m pytest tests/ -m "network" -v
```

---

## 2. Архитектура и изоляция тестов

1. **Изоляция логирования (`isolate_logging` в `tests/conftest.py`):**
   Все тесты автоматически перенаправляют запись логов в изолированный временный каталог `tmp_path / "test_logs"`. Боевые файлы журнала в `logs/pipeline-*.log` остаются чистыми от тестовых артефактов.
2. **Мокирование HTTP-серверов (`MockHttpServer`):**
   Сетевые вызовы для скачивания архивов биржи и погодных рядов перехватываются локальным асинхронным сервером на базе `aiohttp.test_utils.TestServer`.
3. **Изоляция хранилища Parquet и SQLite:**
   Каждый тест создает независимые временные каталоги `tmp_path` для `data/raw`, `data/processed` и `data/jobs.sqlite`.
4. **Детерминизм и псевдослучайные генераторы:**
   Все тесты статистических методов (`placebo_edge`, `placebo_selectivity`, `cluster_ols`) используют фиксированные сиды `np.random.default_rng(seed)`.

---

## 3. Сводка покрытия кода (`backtest_strat/`)

Результаты прогона `pytest --cov=backtest_strat --cov-report=term-missing`:
- **Всего строк кода (Stmts):** 2891
- **Пропущенных строк (Miss):** 110
- **Общее покрытие (Total Coverage):** **96%**
- **Успешных тестов:** 195 (0 failures)

### Детализация по модулям

| Модуль | Назначение | Строк | Пропущено | Покрытие |
| :--- | :--- | :--- | :--- | :--- |
| `backtest_strat/pipeline.py` | Оркестратор всех этапов пайплайна | 110 | 0 | **100%** |
| `backtest_strat/api/jobs.py` | SQLite реестр и воркер фоновых задач | 87 | 0 | **100%** |
| `backtest_strat/backtest/engine.py` | Основной цикл симуляции событий | 119 | 0 | **100%** |
| `backtest_strat/backtest/bar_feed.py` | Адаптер баров в синтетический стакан | 21 | 0 | **100%** |
| `backtest_strat/backtest/portfolio.py`| Учет открытой позиции, средней цены и PnL | 43 | 0 | **100%** |
| `backtest_strat/backtest/report.py` | Расчет метрик (Sharpe, max drawdown, fills) | 32 | 0 | **100%** |
| `backtest_strat/derived/build.py` | Потоковая агрегация L1 и баров | 96 | 0 | **100%** |
| `backtest_strat/derived/trade_bars.py`| Сборка баров напрямую из сделок | 41 | 0 | **100%** |
| `backtest_strat/normalizers/base.py` | Базовый класс нормализации и слияния событий | 152 | 0 | **100%** |
| `backtest_strat/normalizers/schema.py`| Схема `EVENT_SCHEMA` и сортировка | 8 | 0 | **100%** |
| `backtest_strat/research/evaluate.py` | Оценка стратегий (`fast_pnl`, `run_schedule`) | 20 | 0 | **100%** |
| `backtest_strat/research/market.py` | Синхронные обертки над рыночными спецификациями | 7 | 0 | **100%** |
| `backtest_strat/storage/parquet.py` | Партиционированный Parquet writer и манифесты | 78 | 0 | **100%** |
| `backtest_strat/storage/reader.py` | Чтение событий и производных витрин | 33 | 0 | **100%** |
| `backtest_strat/utils/http.py` | Отказоустойчивое скачивание с чанками | 43 | 0 | **100%** |
| `backtest_strat/utils/sync.py` | Запуск корутин из синхронного кода/ноутбуков | 21 | 0 | **100%** |
| `backtest_strat/utils/dates.py` | Вспомогательные функции дат | 10 | 0 | **100%** |
| `backtest_strat/utils/locks.py` | Межпроцессные файловые блокировки | 14 | 0 | **100%** |
| `backtest_strat/utils/simple_book.py`| Легковесный стакан | 30 | 0 | **100%** |
| `backtest_strat/validation/trades.py` | Валидация потока сделок (trades-only) | 76 | 0 | **100%** |
| `backtest_strat/normalizers/okx.py` | Парсинг OKX архивов сделок и стакана | 73 | 1 | **99%** |
| `backtest_strat/scheduler.py` | Планировщик ночных запусков | 138 | 2 | **99%** |
| `backtest_strat/external/weather.py` | Загрузка и кэширование погоды Open-Meteo | 51 | 1 | **98%** |
| `backtest_strat/fetchers/base.py` | Базовый загрузчик архивов с ретраями | 128 | 4 | **97%** |
| `backtest_strat/fetchers/okx.py` | Специфика эндпоинтов и инструментов OKX | 65 | 2 | **97%** |
| `backtest_strat/utils/logging_setup.py`| Централизованное логирование и ротация | 109 | 3 | **97%** |
| `backtest_strat/utils/retry.py` | Декораторы экспоненциального бэкоффа | 26 | 1 | **96%** |
| `backtest_strat/backtest/types.py` | Структуры ордеров и исполнений | 39 | 1 | **97%** |
| `backtest_strat/validation/day.py` | Потоковые проверки качества книги и сделок | 198 | 6 | **97%** |
| `backtest_strat/derived/runner.py` | Оркестратор сборки производных витрин | 82 | 4 | **95%** |
| `backtest_strat/research/stats.py` | Статистика исследований (t-test, FDR, OLS) | 104 | 5 | **95%** |
| `backtest_strat/api/app.py` | FastAPI эндпоинты | 127 | 7 | **94%** |
| `backtest_strat/backtest/strategy.py` | Базовые интерфейсы `Strategy` и `Context` | 53 | 3 | **94%** |
| `backtest_strat/validation/runner.py` | Оркестратор валидации символа | 117 | 7 | **94%** |
| `backtest_strat/backtest/book.py` | Восстановление L2 книги заявок и очереди | 203 | 15 | **93%** |
| `backtest_strat/backtest/examples.py` | Примеры стратегий (TouchMarketMaker, MidMomentum)| 55 | 4 | **93%** |
| `backtest_strat/backtest/schedule.py` | Исполнение расписания целевых позиций | 23 | 2 | **91%** |
| `backtest_strat/research/bars.py` | Агрегация минутных баров в часовые | 18 | 2 | **89%** |
| `backtest_strat/cli.py` | Консольный интерфейс `backtest_strat.cli` | 52 | 9 | **83%** |
| `backtest_strat/ui/dashboard.py` | Веб-дашборд на Streamlit | 150 | 31 | **79%** |
