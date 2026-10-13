# Документация проекта Backtest Data Pipeline & Research

Добро пожаловать в документацию проекта. В данном каталоге собраны технические описания всех подсистем, спецификации данных, руководства по эксплуатации и исследовательские воркфлоу.

---

## Оглавление документации

| Документ | Описание |
| :--- | :--- |
| [**`backtest.md`**](backtest.md) | Архитектура event-driven бэктестера: симуляция очереди заявок (`ReplayBook`), учет задержек, комиссии, управление позицией и расчет метрик. |
| [**`cli.md`**](cli.md) | Консольный интерфейс `backtest_strat.cli`: параметры запуска, стадии (`ingest`, `normalize`, `validate`, `derive`), коды возврата и cron. |
| [**`data_quality.md`**](data_quality.md) | Контроль качества рыночных данных: 9 проверок L2-стакана, 6 проверок trades-only, кэширование отчетов и пороги аномалий. |
| [**`derived_datasets.md`**](derived_datasets.md) | Производные датасеты: тиковый `l1`, бары `bars_1s` и `bars_1m`, as-of mid/спред и построение баров из одних сделок (`trade_bars`). |
| [**`library.md`**](library.md) | Методичка по подключению библиотеки к своему проекту и полный справочник API: установка, сценарии, каждый класс и метод, консольные команды. |
| [**`ingestion.md`**](ingestion.md) | Модуль загрузки сырых архивов: шаблонный метод `BaseExchangeFetcher`, отказоустойчивость, exponential backoff, метаданные инструментов. |
| [**`normalization.md`**](normalization.md) | Нормализация сырых архивов в схему `EVENT_SCHEMA`, детерминированная сортировка `SORT_KEYS`, согласование таймзон UTC+8 и UTC. |
| [**`okx_historical_data.md`**](okx_historical_data.md) | Техническая спецификация публичных архивов биржи OKX: эндпоинты CDN, форматы сделок (ZIP/CSV) и стакана L2 400lv (TAR.GZ/NDJSON). |
| [**`operations.md`**](operations.md) | Эксплуатация и мониторинг: ночной планировщик `backtest_strat.scheduler`, схема двух выключателей, централизованные суточные логи в `logs/`. |
| [**`research_workflow.md`**](research_workflow.md) | Исследовательский воркфлоу «идея → векторный тест → бэктест», правила Point-in-Time, ограничения `bar_feed`, векторная оценка (`fast_pnl`). |
| [**`testing.md`**](testing.md) | Стратегия тестирования, запуск быстрых/медленных тестов и сводка покрытия кодовой базы (96% coverage). |
| [**`web_ui.md`**](web_ui.md) | Веб-интерфейс и HTTP API: бэкенд на FastAPI с пулом задач в SQLite, 5 вкладок Streamlit-дашборда, спецификация REST API. |

---

## Сверка документации с кодом (Reconciliation)

В рамках аудита документации проведена построчная сверка параметров CLI, эндпоинтов API, констант, классов и путей к файлам с актуальной кодовой базой (`backtest_strat/`):

| Компонент / Документ | Что было указано ранее | Фактическое состояние в коде | Статус / Исправление |
| :--- | :--- | :--- | :--- |
| **`docs/cli.md`**<br>Стадии `--stage` | Указаны только 3 стадии: `all, ingest, normalize`. Описание `all`: «скачать и нормализовать». | `backtest_strat/pipeline.py: STAGES` определяет 5 стадий: `("all", "ingest", "normalize", "validate", "derive")`. Стадия `all` выполняет весь цикл. | **Исправлено в документации:** дополнен список допустимых значений и актуализировано описание флага `--stage`. |
| **`docs/data_quality.md`**<br>Запуск из CLI | Использовалась несуществующая команда:<br>`python scripts/run_pipeline.py --stage validate --symbols BTC-USDT ...` | Скрипт `scripts/run_pipeline.py` отсутствует в репозитории. Единая точка запуска — модуль `python -m backtest_strat.cli` с флагом `--symbol`. | **Исправлено в документации:** примеры заменены на валидный запуск через `python -m backtest_strat.cli --exchange okx --symbol BTC-USDT ...`. |
| **`docs/data_quality.md`**<br>Проверки по сделкам | Описаны только проверки L2-стакана (`crossed_book`, `book_gap` и др.). | Модуль `backtest_strat/validation/trades.py` реализует 6 специализированных проверок для trades-only дней. | **Исправлено в документации:** добавлена таблица проверок `no_trades`, `duplicate_trade_ids`, `non_positive`, `trade_gaps`, `trade_price_spike`, `low_coverage`. |
| **`docs/derived_datasets.md`**<br>Запуск из CLI | Использовалась несуществующая команда `python scripts/run_pipeline.py --stage derive ...`. | Единая точка запуска — модуль `python -m backtest_strat.cli`. | **Исправлено в документации:** вызовы заменены на `python -m backtest_strat.cli --exchange okx --symbol BTC-USDT --stage derive ...`. |
| **`docs/derived_datasets.md`**<br>Сборка без стакана | Описана сборка только из потока `events/`. | В `backtest_strat/derived/trade_bars.py` реализована сборка минутных и секундных баров напрямую из сделок (`derive_trades:`). | **Исправлено в документации:** добавлено описание алгоритма `derive_trades` и его приоритетов относительно L2. |
| **`docs/web_ui.md`**<br>Спецификация API | Отсутствовал эндпоинт `/validation/{exchange}/{symbol}`. | В `backtest_strat/api/app.py` реализован `GET /validation/{exchange}/{symbol}`. | **Исправлено в документации:** эндпоинт внесен в таблицу спецификации HTTP API. |
| **`docs/web_ui.md`**<br>Вкладки Streamlit UI | Перечислены 4 вкладки: Задачи, Покрытие данных, Расписание, Логи. | В `backtest_strat/ui/dashboard.py` реализовано 5 вкладок (добавлена вкладка «Качество данных»). | **Исправлено в документации:** вкладка «Качество данных» добавлена в список. |
| **`docs/web_ui.md`**<br>Стадии задач API | В `POST /jobs` описаны только стадии сбора и нормализации. | Схема `JobRequest` принимает стадии `all, ingest, normalize, validate, derive`. | **Исправлено в документации:** отражена поддержка всех пяти стадий. |
| **`docs/ingestion.md`**<br>Публичные методы | Описаны только `download_historical_range` и `download_daily`. | В `BaseExchangeFetcher` / `OkxFetcher` добавлены `list_symbols()`, `instrument_specs()`, `spread_snapshot()`. | **Исправлено в документации:** добавлены примеры работы с рыночными метаданными и спецификациями. |
| **`backtest_strat/ui/dashboard.py`**<br>Выпадающий список стадий | Документация описывает возможность выбора стадий «Только проверить» и «Только производные». | В строке 71 `backtest_strat/ui/dashboard.py` в `st.selectbox` передан жесткий список `["all", "ingest", "normalize"]`. Опции `validate` и `derive` отсутствуют, несмотря на наличие в `format_func`. | **Зафиксировано в `.agents/ISSUES.md` (ISSUE-3):** дефект в коде UI (согласно протоколу QA не редактирует `backtest_strat/`). |
