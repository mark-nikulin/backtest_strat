# backtest-strat: методичка по подключению и справочник API

Версия документа: 0.1 · пакет `backtest-strat` · импорт `backtest_strat` · Python ≥ 3.11 (проверено на 3.14) · Linux / macOS

Документ состоит из двух частей:

* **Часть I — методичка**: как поставить библиотеку, подключить к своему проекту и выполнить типовые сценарии.
* **Часть II — справочник API**: каждый публичный класс, функция и метод с параметрами, возвращаемыми значениями,
  исключениями и примерами.

Примеры кода из части I выполнялись: на реальных данных репозитория и в чистом окружении с установленным wheel.

## Оглавление

**Часть I. Методичка**
1. [Что это и когда использовать](#1-что-это-и-когда-использовать)
2. [Установка](#2-установка)
3. [Подключение к своему проекту](#3-подключение-к-своему-проекту)
4. [Ключевые понятия](#4-ключевые-понятия)
5. [Сценарии](#5-сценарии)
6. [Как добавить свою биржу](#6-как-добавить-свою-биржу)
7. [Частые проблемы](#7-частые-проблемы)

**Часть II. Справочник API**
8. [`backtest_strat.backtest`](#8-backtest_stratbacktest--движок-бэктеста)
9. [`backtest_strat.pipeline`](#9-backtest_stratpipeline--оркестрация)
10. [`backtest_strat.fetchers`](#10-backtest_stratfetchers--загрузка)
11. [`backtest_strat.normalizers`](#11-backtest_stratnormalizers--нормализация)
12. [`backtest_strat.storage`](#12-backtest_stratstorage--чтение-и-запись)
13. [`backtest_strat.derived`](#13-backtest_stratderived--l1-и-бары)
14. [`backtest_strat.validation`](#14-backtest_stratvalidation--проверки-качества)
15. [`backtest_strat.research`](#15-backtest_stratresearch--утилиты-исследований)
16. [`backtest_strat.external.weather`](#16-backtest_stratexternalweather--погода)
17. [`backtest_strat.scheduler`](#17-backtest_stratscheduler--ночной-запуск)
18. [`backtest_strat.utils`](#18-backtest_stratutils--вспомогательные-модули)
19. [`backtest_strat.paths`](#19-backtest_stratpaths--пути-данных)
20. [Консольные команды](#20-консольные-команды)
21. [Версионирование и стабильность](#21-версионирование-и-стабильность)

---

# Часть I. Методичка

## 1. Что это и когда использовать

Библиотека закрывает путь от «хочу проверить идею» до «есть честный ответ»:

```
биржа ──► fetchers ──► normalizers ──► validation ──► derived ──► backtest / research
 архивы     скачать      единая схема    проверки      L1, бары    движок, статистика
```

| Нужно | Берите |
|---|---|
| Скачать историю сделок/стакана биржи, не перекачивая то, что уже есть | `pipeline`, `fetchers`, команда `bt-data` |
| Убедиться, что данные пригодны (дыры, перевернутая книга, дубли) | `validation` |
| Получить бары и лучшие цены (L1) | `derived`, `storage.reader.load_derived` |
| Прогнать свою стратегию с задержками, комиссиями, очередью заявок | `backtest` |
| Проверить статистическую значимость идеи без самообмана | `research.stats` |
| Подмешать внешний ряд (погода) без заглядывания в будущее | `external.weather` |

**Не подходит для:** живой торговли (нет подключения к биржевому API для ордеров), высокочастотных стратегий на данных,
которых нет в публичных архивах (нужен собственный сбор), Windows без WSL (используется `fcntl` и `crontab`).

---

## 2. Установка

### 2.1. Варианты

| Способ | Команда | Когда |
|---|---|---|
| Из локальной папки, editable | `pip install -e /путь/к/backtest_strat` | **Рекомендуется при разработке**: правки библиотеки сразу видны во всех проектах |
| Из wheel | `python -m build` в репозитории → `pip install dist/backtest_strat-0.1.0-py3-none-any.whl` | Зафиксированная версия без ссылки на папку |
| Из git | `pip install "git+ssh://git@github.com/mark-nikulin/backtest_strat.git"` | Другая машина/сервер (нужен `git push`) |

### 2.2. Extras — необязательные зависимости

Ядро ставится с минимумом: `numpy`, `pandas`, `pyarrow`, `aiohttp`, `httpx`. Остальное — по необходимости:

| Extra | Состав | Для чего |
|---|---|---|
| `api` | fastapi, uvicorn | HTTP API задач (`bt-api`) |
| `ui` | streamlit, fastapi, uvicorn | дашборд и монитор (`bt-dashboard`, `bt-monitor`) |
| `research` | matplotlib, scipy, nbformat, nbclient, ipykernel | ноутбуки, графики, точные p-значения |
| `dev` | pytest, pytest-asyncio, pytest-cov, build | разработка самой библиотеки |
| `all` | api + ui + research | всё сразу |

```bash
pip install -e "/путь/к/backtest_strat[research]"
pip install -e "/путь/к/backtest_strat[research,ui]"
```

Без нужного extra команда подскажет, что поставить:
```
$ bt-dashboard
Нужен extra [ui]:  pip install 'backtest-strat[ui]'
```

> `import backtest_strat.backtest` **не** подгружает fastapi, streamlit, scipy и matplotlib: тяжелые зависимости
> импортируются только там, где реально нужны.

### 2.3. Проверка установки

```bash
python -c "import backtest_strat; print(backtest_strat.__version__)"     # 0.1.0
bt-data --help
```

### 2.4. Обновление и удаление

```bash
pip install -U -e /путь/к/backtest_strat      # после git pull в репозитории (при editable-установке обычно не нужно)
pip uninstall backtest-strat
```

### 2.5. Jupyter

В окружение вашего проекта: `pip install ipykernel`, затем `python -m ipykernel install --user --name myproject`
и выбрать это ядро. Утилиты библиотеки асинхронные; в ноутбуках используйте синхронные обертки
(`research.market.*`, `utils.sync.run_sync`), иначе `asyncio.run` упадет — цикл событий уже запущен.

---

## 3. Подключение к своему проекту

### 3.1. Рекомендуемая раскладка

```
my_project/                  # ваш проект — отсюда запускаются команды и ноутбуки
├── .venv/                   # окружение, куда установлена библиотека
├── requirements.txt         # -e /путь/к/backtest_strat
├── strategies/
│   └── my_alpha.py          # ваши стратегии: class MyAlpha(Strategy)
├── notebooks/
├── config/
│   └── schedule.json        # создается bt-schedule, если нужен ночной запуск
└── data/                    # создается библиотекой (путь настраивается)
    ├── raw/                 # сырые архивы биржи
    ├── processed/           # нормализованные данные, бары, отчеты качества
    └── external/            # внешние ряды (погода)
```

### 3.2. Зависимость в вашем проекте

`requirements.txt`:
```
-e /Users/kyle/main/py_files/backtest_strat
```
`pyproject.toml`:
```toml
dependencies = ["backtest-strat @ file:///Users/kyle/main/py_files/backtest_strat"]
```

### 3.3. Где лежат данные

Относительные пути считаются **от текущей папки процесса**. Чтобы один раз настроить и забыть, задайте окружение:

```bash
export PIPELINE_DATA_DIR=/Users/kyle/market_data      # raw, processed, external внутри
```

Полный список переменных — в разделе [19](#19-backtest_stratpaths--пути-данных). Те же данные могут использовать
несколько проектов: достаточно одинакового `PIPELINE_DATA_DIR`.

### 3.4. Первое подключение за три шага

```bash
# 1. данные: сделки BTC-USDT за январь 2024 (≈100 МБ), нормализация, проверка, бары
bt-data --exchange okx --symbol BTC-USDT --mode backfill --start 2024-01-01 --end 2024-01-31 \
        --data-type trades --stage all
```
```python
# 2. данные в коде
from datetime import date
from backtest_strat.storage.reader import load_derived
bars = load_derived("bars_1m", "BTC-USDT", date(2024, 1, 1), date(2024, 1, 31)).to_pandas()
```
```python
# 3. своя стратегия (см. сценарий 5.3)
from backtest_strat.backtest import Strategy, BacktestEngine, InstrumentSpec
```

---

## 4. Ключевые понятия

Эти соглашения действуют во всей библиотеке; без них легко ошибиться.

| Понятие | Правило |
|---|---|
| **Время** | Наносекунды от эпохи Unix, UTC. В таблицах колонка `ts` имеет тип `timestamp[ns, tz=UTC]`; в движке, `Order`, `Fill`, `InstrumentSpec` — целые `int` наносекунды. |
| **Стороны** | `BUY = 1`, `SELL = -1`. Для сделки — сторона агрессора; для стакана `BUY` = bid, `SELL` = ask. Константы импортируются из `backtest_strat.backtest` (или `backtest_strat.normalizers.schema`). |
| **Типы событий** | `TRADE = 0`, `BOOK_SNAPSHOT = 1` (полный срез — книгу очистить), `BOOK_UPDATE = 2` (дифф; `size == 0` удаляет уровень). |
| **Сообщение стакана** | Несколько строк (уровней) с одним `msg_id` и одним `ts`; применяется атомарно. |
| **Порядок событий** | Строго по `(ts, is_book, msg_id, level)`: при равном `ts` сделка идет **раньше** стакана. Детерминированно. |
| **Цены в движке** | Целые тики (`price_ticks`); перевод через `InstrumentSpec.to_ticks/to_price`. |
| **Комиссии** | Доли, не проценты: `0.001` = 10 б.п. Списываются в валюте котировки. |
| **Идемпотентность** | Любой этап можно запускать повторно: уже готовое пропускается (`skipped`); пересчет — `force=True` или изменение входных файлов. |
| **Сутки UTC и UTC+8** | Архивы сделок OKX нарезаны по UTC+8: для UTC-суток D нужны файлы D и D+1. Последние сутки диапазона без «следующего» файла получают статус `incomplete`. |
| **Точка во времени (PIT)** | Признак в момент `t` использует только данные, известные к `t`. Внешние ряды сдвигаются (`lag_days`). |
| **Статусы качества** | `OK` / `WARN` / `FAIL`. WARN — объяснить и принять решение; FAIL — не использовать день вслепую. |

### Схема событий `EVENT_SCHEMA`

| Колонка | Тип | Описание |
|---|---|---|
| `ts` | timestamp[ns, UTC] | момент события по бирже |
| `event_type` | int8 | 0 сделка, 1 снапшот, 2 апдейт |
| `side` | int8 | 1 / −1 |
| `price` | float64 | цена |
| `size` | float64 | объем (для стакана — новый объем уровня; 0 = удалить) |
| `num_orders` | int32, nullable | число заявок на уровне (только стакан) |
| `msg_id` | int64 | `trade_id` для сделок; номер сообщения в файле для стакана |
| `level` | int16 | позиция уровня внутри сообщения; 0 для сделок |

### Раскладка каталогов

```
<RAW>/<exchange>/<symbol>/<data_type>/<файл архива>
<PROCESSED>/<exchange>/<symbol>/
   trades/year=YYYY/month=MM/<SYMBOL>-trades-<дата источника>.parquet     # нормализованные сделки
   orderbook/year=YYYY/month=MM/...                                         # нормализованный стакан
   events/year=YYYY/month=MM/<YYYY-MM-DD>.parquet                           # сделки + стакан за UTC-сутки
   l1/ | bars_1s/ | bars_1m/  year=YYYY/month=MM/<YYYY-MM-DD>.parquet      # производные датасеты
   validation/<YYYY-MM-DD>.json                                             # отчеты качества
   _manifest.json  _derived_manifest.json                                   # реестр обработанного
```

---

## 5. Сценарии

### 5.1. Скачать и подготовить данные из кода

```python
from datetime import date
from backtest_strat.pipeline import PipelineParams, run_pipeline

params = PipelineParams(
    exchange="okx",
    symbols=["BTC-USDT", "ETH-USDT"],
    mode="backfill",
    start=date(2024, 1, 1),
    end=date(2024, 1, 31),
    data_types=["trades"],        # только сделки; стакан — ["trades", "orderbook"] (≈1–4 ГБ в день на актив)
    stage="all",                  # скачать → нормализовать → проверить → построить бары
)
result = run_pipeline(params, on_progress=lambda frac, text: print(f"{frac:5.0%} {text}"))
print("\n".join(result.summary_lines()))
assert result.ok, result.to_dict()["failed"]
```

Повторный запуск ничего не перекачает и не пересчитает.

### 5.2. Прочитать данные для исследования

```python
from datetime import date
from backtest_strat.storage.reader import load_derived, load_events, iter_events

# бары и лучшие цены — компактно, подходят для pandas
bars = load_derived("bars_1m", "BTC-USDT", date(2024, 1, 1), date(2024, 1, 7)).to_pandas()
l1   = load_derived("l1", "BTC-USDT", date(2024, 1, 1), date(2024, 1, 1)).to_pandas()   # нужен стакан

# полный поток событий — большой; читайте потоково
for batch in iter_events("BTC-USDT", date(2024, 1, 1), date(2024, 1, 1), batch_size=200_000):
    ...  # batch — pyarrow.RecordBatch в порядке SORT_KEYS
```

### 5.3. Своя стратегия на минутных барах

Движок принимает поток событий; из баров его делает `bars_to_events` (синтетический стакан: bid/ask = close ∓ полуспред).

```python
import numpy as np, pandas as pd
from backtest_strat.backtest import (BUY, SELL, BacktestEngine, InstrumentSpec, Strategy,
                                     bars_to_events, summarize, format_summary)

NS = 10 ** 9


class Breakout(Strategy):
    """Пробой максимума/минимума за lookback минут."""

    def __init__(self, size=0.01, lookback=30):
        self.size, self.lookback, self.mids = size, lookback, []

    def on_start(self, ctx):
        ctx.set_timer(60 * NS)                       # on_timer раз в минуту биржевого времени

    def on_timer(self, ctx):
        mid = ctx.book.mid()
        self.mids.append(mid)
        if len(self.mids) <= self.lookback:
            return
        window = self.mids[-self.lookback - 1:-1]
        if mid > max(window) and ctx.position <= 0:
            ctx.market(BUY, self.size - ctx.position, tag="breakout_up")
        elif mid < min(window) and ctx.position >= 0:
            ctx.market(SELL, self.size + ctx.position, tag="breakout_dn")


rng = np.random.default_rng(1)
idx = pd.date_range("2024-03-01", periods=3 * 1440, freq="min")
close = np.round(40000 * np.exp(np.cumsum(rng.normal(0, 0.0006, len(idx)))), 1)

spec = InstrumentSpec("TEST-USDT", tick_size=0.1, lot_size=0.001, maker_fee=0.0008, taker_fee=0.001)
events = bars_to_events(idx.as_unit("ns").asi8, close, 60 * NS, spec.tick_size, half_spread_bps=1.0)

result = BacktestEngine(Breakout(), spec).run_batches([events])
print(format_summary(summarize(result)))
```

Реальный вывод этого примера: `total_pnl ≈ −68`, `fees ≈ 68`, `fills = 87`, `final_position = −0.01`.
Движок честно показывает, что стратегия на случайном блуждании теряет только комиссии.

> **Важно про `asi8`.** Индекс pandas ≥ 3 может иметь разрешение не в наносекундах. `idx.as_unit("ns").asi8`
> гарантирует наносекунды; `idx.astype("int64")` — нет.

### 5.4. Бэктест на реальных данных стакана (L2)

Когда нужны глубина, очередь заявок и проскальзывание от размера:

```bash
bt-data --exchange okx --symbol BTC-USDT --mode backfill --start 2024-01-01 --end 2024-01-02 \
        --data-type trades orderbook --stage all
```
```python
from datetime import date
from backtest_strat.backtest import BacktestEngine, InstrumentSpec, summarize, format_summary

spec = InstrumentSpec("BTC-USDT", tick_size=0.1, lot_size=0.00000001,
                      maker_fee=0.0008, taker_fee=0.001,
                      feed_latency_ns=1_000_000, order_latency_ns=2_000_000)   # 1 мс + 2 мс
result = BacktestEngine(MyAlpha(), spec).run(date(2024, 1, 1), date(2024, 1, 2))
print(format_summary(summarize(result)))
```

Тик и лот биржи: `research.market.instrument_specs("okx")["BTC-USDT"]`.

> Скорость ≈ 2–3 млн событий в секунду на чистом Python: день BTC-USDT (≈ 24 млн строк) — около 10 секунд.

### 5.5. Исследование: сигнал → значимость → издержки

```python
from datetime import date
import numpy as np, pandas as pd
from backtest_strat.research import stats
from backtest_strat.research.bars import minute_bars, to_hourly
from backtest_strat.research.evaluate import fast_pnl

m = minute_bars("BTC-USDT", date(2024, 1, 1), date(2024, 3, 31))
h = to_hourly(m)
ret = np.log(h["close"] / h["close"].shift(1))           # доходность внутри часа t
sig = np.sign(ret.shift(24)).fillna(0)                    # решение в начале часа t по часу вчера (только прошлое!)

valid = (sig != 0).to_numpy()
day_ids = pd.factorize(h.index.normalize())[0]
e = stats.edge_ttest(sig.to_numpy(), np.expm1(ret.fillna(0)).to_numpy(), valid, day_ids)
print(e)        # {'edge_bps':…, 'edge_excess_bps':…, 't':…, 'p':…, 'n_active':…, 'n_days':…}

pnl = fast_pnl(ret, sig, cost_bps_per_side=10.0, notional=1000.0)
print(pnl.sum(), stats.daily_tstat(pnl))
```

Если проверяете много активов/вариантов — примените поправку: `stats.benjamini_hochberg(pd.Series(p_values))`.
Чек-лист честного исследования — в [research/README.md](../research/README.md).

### 5.6. Внешний ряд без заглядывания в будущее

```python
from datetime import date
from backtest_strat.external.weather import load_daily

w = load_daily(date(2024, 1, 1), date(2024, 6, 30), cities=["new_york", "london"], lag_days=1)
# w.index — дата ИСПОЛЬЗОВАНИЯ; значение в строке D — наблюдение за D-1
```

### 5.7. Ночное обновление данных

```bash
bt-schedule install --time 00:30        # строка в crontab (запуск из папки проекта!)
bt-schedule enable                      # включить; disable — пауза без изменения crontab
bt-schedule status                      # оба выключателя: crontab и enabled
```

Подробности и безопасность — в разделе [17](#17-backtest_stratscheduler--ночной-запуск).

---

## 6. Как добавить свою биржу

Достаточно двух классов и регистрации; остальное (повторы, атомарная запись, идемпотентность, backfill, нормализация
по чанкам, проверки, бары, движок) работает без изменений.

```python
# my_project/exchanges/binance.py
from datetime import date
from typing import List
from backtest_strat.fetchers import FETCHERS
from backtest_strat.fetchers.base import BaseExchangeFetcher
from backtest_strat.normalizers import NORMALIZERS
from backtest_strat.normalizers.base import BaseNormalizer


class BinanceFetcher(BaseExchangeFetcher):
    supported_data_types = ("trades",)

    def __init__(self, base_dir: str = "data/raw", **kw):
        super().__init__("binance", base_dir=base_dir, **kw)

    def archive_filename(self, symbol: str, target_date: date, data_type: str) -> str:
        return f"{symbol}-trades-{target_date.isoformat()}.zip"

    def candidate_urls(self, symbol: str, target_date: date, data_type: str) -> List[str]:
        d = target_date.isoformat()
        return [f"https://data.binance.vision/data/spot/daily/trades/{symbol}/{symbol}-trades-{d}.zip"]


class BinanceNormalizer(BaseNormalizer):
    def __init__(self, raw_dir="data/raw", processed_dir="data/processed"):
        super().__init__("binance", raw_dir=raw_dir, processed_dir=processed_dir)

    def source_date(self, path): ...                      # дата архива по имени файла
    def trade_dates_for_utc_day(self, day): return [day]   # файлы, покрывающие UTC-сутки (у OKX: [D, D+1])
    def iter_trades(self, path): ...                       # yield pyarrow.Table в EVENT_SCHEMA, отсортированные по ts
    def iter_orderbook(self, path): ...                    # если стакан не поддерживается — raise NotImplementedError


FETCHERS["binance"] = BinanceFetcher
NORMALIZERS["binance"] = BinanceNormalizer
```

После регистрации биржа доступна во всех интерфейсах (`PipelineParams(exchange="binance", ...)`, `get_fetcher("binance")`).
Таблицы, которые возвращают `iter_trades`/`iter_orderbook`, обязаны иметь схему `EVENT_SCHEMA`
(`backtest_strat.normalizers.schema`) и внутри батча быть отсортированы по `ts`.

---

## 7. Частые проблемы

| Симптом | Причина | Решение |
|---|---|---|
| `ModuleNotFoundError: backtest_strat` | пакет поставлен не в то окружение | активируйте venv проекта и повторите `pip install -e …` |
| `Нужен extra [ui]` / `[api]` | не установлены опциональные зависимости | `pip install -e "…[ui]"` |
| `Нет событий за период` (`bt-backtest`) | нет данных или другая рабочая папка | проверьте `PIPELINE_PROCESSED_DIR`/cwd; сначала `bt-data … --stage all` |
| Данные «пропали» после смены папки запуска | пути относительные от cwd | `export PIPELINE_DATA_DIR=/абсолютный/путь` |
| `RuntimeError: asyncio.run() cannot be called from a running event loop` | вызов `asyncio.run` в Jupyter | используйте `utils.sync.run_sync` или `research.market.*` |
| События/бары за последний день диапазона не строятся (`incomplete`) | нет файла сделок за D+1 (OKX режет по UTC+8) | скачайте на день дальше или исключите последний день |
| Первая минута дня в L1/бэктесте отсутствует | архив стакана начинается с апдейтов, а не со снапшота | ожидаемо: книга неполна до первого снапшота (WARN в проверках) |
| `ValueError: amount меньше лота` | заявка меньше `lot_size` после округления вниз | увеличьте количество или проверьте `lot_size` |
| `WARN`/`FAIL` в отчете качества | реальные события рынка или дефект данных | смотрите `detail`/`examples` проверки; не торгуйте вслепую на FAIL |
| Windows: `ModuleNotFoundError: fcntl` | межпроцессные блокировки POSIX | используйте WSL или Linux/macOS |
| Cron запускается, но данных нет | у cron другая рабочая папка | `bt-schedule install` запускайте из папки проекта или задайте `PIPELINE_WORKDIR` |

---

# Часть II. Справочник API

Соглашения: все публичные имена перечислены в таблицах; «возвращает» — тип результата; исключения указаны, если
они часть контракта. Параметры со значением по умолчанию необязательны.

## 8. `backtest_strat.backtest` — движок бэктеста

```python
from backtest_strat.backtest import (
    BacktestEngine, BacktestResult, Strategy, Context, InstrumentSpec, Order, Fill, Portfolio,
    ReplayBook, ScheduledPosition, bars_to_events, summarize, format_summary, BUY, SELL,
)
```

Принцип: записанный рынок неизменяем; ордера стратегии живут в отдельном слое исполнения. Агрессивный ордер проходит по
уровням записанной книги; пассивный встает в конец очереди уровня и исполняется, когда записанные сделки съедят объем перед ним.

### 8.1. `InstrumentSpec`

Неизменяемое описание инструмента и модели исполнения (`@dataclass(frozen=True)`).

```python
InstrumentSpec(symbol, tick_size, lot_size, contract_multiplier=1.0, maker_fee=0.0, taker_fee=0.0005,
               feed_latency_ns=1_000_000, order_latency_ns=2_000_000)
```

| Параметр | Тип | По умолч. | Описание |
|---|---|---|---|
| `symbol` | str | — | тикер; для `BacktestEngine.run` должен совпадать с каталогом данных |
| `tick_size` | float | — | шаг цены |
| `lot_size` | float | — | минимальный шаг количества |
| `contract_multiplier` | float | 1.0 | множитель контракта (для спота 1.0) |
| `maker_fee`, `taker_fee` | float | 0.0 / 0.0005 | комиссии как **доли** (0.001 = 10 б.п.) |
| `feed_latency_ns` | int | 1 000 000 | задержка доставки данных до стратегии, нс |
| `order_latency_ns` | int | 2 000 000 | задержка доставки ордера/отмены на биржу, нс |

| Член | Возвращает | Описание |
|---|---|---|
| `round_trip_ns` (property) | int | `feed_latency_ns + order_latency_ns` — через столько после решения ордер дойдет до биржи |
| `to_ticks(price)` | int | цена → целые тики (округление к ближайшему) |
| `to_price(ticks)` | float | тики → цена |

> У архивов OKX есть только биржевое время, локального времени получения нет, поэтому задержки моделируются явно.

### 8.2. `Strategy`

Базовый класс. Переопределяйте только нужные колбэки; все они получают `ctx: Context` первым аргументом.

| Метод | Когда вызывается | Дополнительные аргументы |
|---|---|---|
| `on_start(ctx)` | один раз, после первого **снапшота** с валидной книгой | — |
| `on_book(ctx)` | после каждого сообщения стакана (после его применения) | — |
| `on_trade(ctx, side, price, amount)` | после каждой записанной сделки (после исполнения наших пассивных ордеров) | `side` — сторона агрессора |
| `on_fill(ctx, fill)` | при каждом исполнении **нашего** ордера | `fill: Fill` |
| `on_timer(ctx)` | по таймеру, заданному `ctx.set_timer` (только при валидной книге) | — |
| `on_end(ctx)` | один раз в конце данных | — |

События до первого снапшота игнорируются (книга неполна).

### 8.3. `Context`

Все, что стратегия видит и может делать. Объект создает движок; конструировать его самостоятельно не нужно.

**Состояние (только чтение)**

| Член | Тип | Описание |
|---|---|---|
| `now` | int | текущее биржевое время, нс |
| `book` | `ReplayBook` | восстановленный стакан (см. [8.8](#88-replaybook)) |
| `spec` | `InstrumentSpec` | параметры инструмента |
| `position` | float | текущая позиция (знак — направление) |
| `portfolio` | `Portfolio` | позиция, PnL, комиссии, сделки |
| `equity()` | float | PnL с начала теста: realized + unrealized − комиссии (по mid; без mid — realized − комиссии) |
| `open_orders()` | `List[Order]` | рабочие и еще не дошедшие до биржи ордера |

**Действия.** Все доходят до биржи с задержкой `spec.round_trip_ns`.

| Метод | Возвращает | Описание |
|---|---|---|
| `limit(side, price, amount, tif="GTC", tag=None, ttl_ns=None)` | int (`order_id`) | лимитный ордер |
| `market(side, amount, tag=None)` | int | рыночный: IOC по предельной цене, проходит по стакану на момент прихода на биржу |
| `cancel(order_id)` | None | отмена; вступает в силу через `round_trip_ns` |
| `cancel_all()` | None | отмена всех рабочих и ожидающих ордеров |
| `set_timer(interval_ns)` | None | периодический `on_timer` каждые `interval_ns` (кратно эпохе); повторный вызов заменяет таймер |

`limit` параметры:

| Параметр | Описание |
|---|---|
| `side` | `BUY` или `SELL`; иначе `ValueError` |
| `price` | цена (округляется к тику) |
| `amount` | количество; **округляется вниз** к лоту; меньше лота → `ValueError` |
| `tif` | `"GTC"`, `"IOC"`, `"FOK"`, `"PostOnly"`; иначе `ValueError` |
| `tag` | произвольная метка; попадет в `Fill.tag` |
| `ttl_ns` | время жизни после активации; по истечении ордер снимается |

Типы исполнения (`tif`):

| `tif` | Поведение |
|---|---|
| `GTC` | исполняется по пересекаемым уровням, остаток встает в книгу |
| `IOC` | исполняется сразу, остаток отбрасывается |
| `FOK` | исполняется целиком или не исполняется вовсе |
| `PostOnly` | если пересекает спред — отклоняется; иначе встает в книгу (всегда maker) |

### 8.4. `BacktestEngine`

Синхронный event-driven движок: один процесс, один цикл, без потоков и сети.

```python
BacktestEngine(strategy: Strategy, spec: InstrumentSpec, equity_sample_ns: int = 60_000_000_000)
```

| Параметр | Описание |
|---|---|
| `strategy` | экземпляр вашей стратегии |
| `spec` | `InstrumentSpec` |
| `equity_sample_ns` | как часто снимать точку кривой equity (по биржевому времени), нс |

**`run(start, end, exchange="okx", processed_dir=PROCESSED_DIR, batch_size=500_000) -> BacktestResult`**
читает нормализованные события символа `spec.symbol` за `[start, end]` (`date`) и прогоняет стратегию.

**`run_batches(batches: Iterable[pyarrow.RecordBatch]) -> BacktestResult`**
прогоняет произвольный поток. Обязательные колонки каждого батча: `ts`, `event_type`, `side`, `price`, `size`, `msg_id`.
Батчи должны идти в порядке времени; сообщение стакана может быть разрезано между батчами.

**`set_timer(interval_ns)`** — служебный; используйте `ctx.set_timer`.

Порядок обработки события с временем `t`:
1. таймеры, наступившие до `t`;
2. отмены, истечения и активация ордеров с `active_ts ≤ t` — против книги **до** события;
3. само событие: сообщение стакана применяется целиком; сделка сначала исполняет пассивные ордера;
4. колбэк стратегии (`on_book` / `on_trade`).

### 8.5. `BacktestResult`

| Поле | Тип | Описание |
|---|---|---|
| `spec` | `InstrumentSpec` | использованный инструмент |
| `portfolio` | `Portfolio` | итоговое состояние, сделки, кривая equity |
| `start_ts`, `end_ts` | int | первое и последнее биржевое время, нс |
| `events` | int | обработано строк |
| `book_messages`, `trades` | int | сообщений стакана / сделок |
| `orders_sent` | int | ордеров отправлено стратегией |
| `wall_seconds` | float | реальное время прогона |
| `final_mid` | float \| None | mid в конце (для оценки открытой позиции) |

### 8.6. `Portfolio`

Учет позиции, денег и PnL для линейного контракта (спот / USDT-margined perp). Средняя цена входа;
realized фиксируется при сокращении и развороте.

| Поле | Описание |
|---|---|
| `position`, `avg_price` | позиция и средняя цена входа |
| `realized_pnl`, `fees` | зафиксированный PnL; накопленные комиссии |
| `volume`, `notional` | оборот в базовой валюте / в валюте котировки |
| `maker_fills`, `taker_fills` | число исполнений по типам |
| `fills` | `List[Fill]` |
| `equity_curve` | `List[(ts_ns, equity)]`, шаг — `equity_sample_ns` |

| Метод | Возвращает | Описание |
|---|---|---|
| `apply(fill)` | None | учесть исполнение (вызывает движок) |
| `unrealized_pnl(mark)` | float | нереализованный PnL по цене `mark` |
| `equity(mark)` | float | `realized + unrealized − fees` |

### 8.7. `Order`, `Fill`

`Order` — ордер в слое исполнения (создается `Context.limit`; напрямую нужен при работе с `ReplayBook`).

| Поле | Описание |
|---|---|
| `order_id` | идентификатор; `next_order_id()` из `backtest_strat.backtest.types` |
| `side` | `BUY`/`SELL` |
| `price_ticks` | цена в тиках |
| `amount`, `remaining` | исходное и оставшееся количество; `filled` (property) = разность |
| `placed_ts` | момент решения |
| `active_ts` | момент, с которого ордер участвует в матчинге |
| `tif`, `tag`, `expiry_ts`, `active` | тип исполнения, метка, время снятия, признак «стоит в книге» |

`Fill` — исполнение нашего ордера (неизменяемое).

| Поле | Описание |
|---|---|
| `fill_id`, `order_id` | идентификаторы |
| `side` | сторона нашего ордера |
| `price`, `amount` | цена и количество исполнения |
| `ts` | биржевое время, нс |
| `is_maker` | `True` — пассивное исполнение |
| `fee` | комиссия в валюте котировки: `price · amount · multiplier · ставка` |
| `tag` | метка ордера |

### 8.8. `ReplayBook`

Восстановленный L2-стакан + слой исполнения наших ордеров. Обычно используется через `ctx.book`; напрямую — для
тестов и нестандартных симуляций.

```python
ReplayBook(spec: InstrumentSpec)
```

**Запросы к книге**

| Метод | Возвращает | Описание |
|---|---|---|
| `best_bid()`, `best_ask()` | float \| None | лучшие цены |
| `best_bid_ticks()`, `best_ask_ticks()` | int \| None | то же в тиках |
| `mid()`, `spread()` | float \| None | середина и спред |
| `depth(levels=10)` | `(bids, asks)` | списки `(price, size)`, bid по убыванию, ask по возрастанию |
| `volume_at(side, price)` | float | объем записанного уровня |
| `is_valid()` | bool | есть и bid, и ask |
| `open_orders()` | `List[Order]` | рабочие и ожидающие наши ордера |
| `queue_ahead(order_id)` | float \| None | объем перед нашим пассивным ордером |

**Изменение состояния** (все принимают `on_fill: Callable[[Fill], None]`)

| Метод | Описание |
|---|---|
| `apply_message(is_snapshot, sides, prices, sizes, ts, on_fill)` | применить сообщение стакана целиком; снапшот сбрасывает книгу и учет съеденной нами ликвидности |
| `on_trade(aggressor_side, price, amount, now, on_fill)` | записанная сделка: исполняет наши пассивные ордера с учетом очереди |
| `submit(order, now, on_fill)` | отправить ордер (с `active_ts > now` — ждет) |
| `request_cancel(order_id, effective_ts)` | запросить отмену с задержкой |
| `process_due(now, on_fill) -> List[int]` | отмены, истечения и активации, наступившие к `now`; возвращает отмененные `order_id` |

Модель исполнения и ее допущения:
* **Съеденная ликвидность** уровня не используется повторно, пока уровень не обновится записанным сообщением.
* **Очередь**: при постановке перед нами весь объем уровня; он уменьшается записанными сделками и не может быть больше текущего
  объема уровня (отмены перед нами сокращают очередь).
* **Trade-through**: сделка по цене хуже нашей исполняет нас без учета очереди.
* Если книга прошла сквозь наш пассивный ордер без сделки, он исполняется как maker.
* **Известное ограничение**: диффы OKX неполные — объем на лучшем уровне расходится со снапшотами в ≈ 11% случаев,
  поэтому очередь для пассивных ордеров оценивается шумно; агрессивное исполнение достоверно.

### 8.9. `bars_to_events`

```python
bars_to_events(bar_ts_ns, close, interval_ns, tick_size, half_spread_bps, fill_gaps=True) -> pyarrow.RecordBatch
```

Строит из баров синтетический стакан для движка: на закрытии каждого бара — снапшот с одним уровнем на сторону.

| Параметр | Описание |
|---|---|
| `bar_ts_ns` | `np.ndarray[int64]`, начало бара, нс, отсортировано |
| `close` | `np.ndarray[float]`, цена закрытия |
| `interval_ns` | длина бара, нс (минута — `60 * 10**9`) |
| `tick_size` | шаг цены; bid округляется вниз, ask вверх |
| `half_spread_bps` | полуспред в б.п.; не меньше одного тика |
| `fill_gaps` | интервалы без сделок получают прошлый `close` (иначе пропускаются) |

Возвращает батч с колонками `ts, event_type, side, price, size, msg_id` (две строки на бар, снапшот на моменте `ts + interval`,
объем на уровне — «бесконечный»).

Ограничения: нет глубины и проскальзывания от размера; спред — параметр, а не наблюдение; внутри бара книга неподвижна;
модель очереди для пассивных ордеров на такой книге бессмысленна. Подходит для проверки сигналов небольшого размера на
ликвидных активах.

### 8.10. `ScheduledPosition`

```python
ScheduledPosition(schedule: Dict[int, int], notional: float)
```

Стратегия «держать позицию по расписанию»: `schedule = {ts_ns: -1 | 0 | +1}`. На снапшоте с точным совпадением `ts`
приводит позицию к целевому знаку рыночным ордером; торгует только при смене знака; разворот — одним ордером.
Количество фиксируется при входе из нуля как `notional / mid` (округляется вниз к лоту) и сохраняется, пока позиция не закрыта.
Используйте, когда сигнал считается векторно, а исполнением, издержками и учетом должен заниматься движок.

### 8.11. `summarize`, `format_summary`

**`summarize(result: BacktestResult) -> Dict[str, float]`**

| Ключ | Описание |
|---|---|
| `period` | строка `начало → конец` (UTC) |
| `total_pnl`, `realized_pnl`, `unrealized_pnl` | итоговый, зафиксированный, нереализованный, в валюте котировки |
| `fees` | комиссии |
| `final_position` | позиция в конце |
| `fills`, `orders_sent` | число исполнений / ордеров |
| `order_fill_ratio` | доля ордеров с хотя бы одним исполнением |
| `maker_share` | доля пассивных исполнений |
| `volume`, `notional` | оборот в базовой валюте / в валюте котировки |
| `max_drawdown` | максимальная просадка кривой equity |
| `sharpe_annualized` | Sharpe по приращениям equity с шагом выборки, годовой коэффициент для 24/7 |
| `events`, `events_per_sec` | обработано / скорость |

**`format_summary(stats) -> str`** — человекочитаемая таблица.

---

## 9. `backtest_strat.pipeline` — оркестрация

Единая точка запуска всех этапов; CLI, API, планировщик и дашборд вызывают только ее.

```python
from backtest_strat.pipeline import PipelineParams, PipelineResult, run_pipeline, run_pipeline_async
```

### 9.1. `PipelineParams`

| Поле | Тип | По умолч. | Описание |
|---|---|---|---|
| `exchange` | str | — | ключ из `FETCHERS` (`"okx"`) |
| `symbols` | List[str] | — | тикеры |
| `mode` | str | `"backfill"` | `"backfill"` — диапазон `start..end`; `"daily"` — один день (`end` или вчера по UTC) |
| `start`, `end` | date \| None | None | границы; для `backfill` обязательны |
| `data_types` | List[str] | `["trades","orderbook"]` | какие архивы скачивать |
| `stage` | str | `"all"` | `all`, `ingest`, `normalize`, `validate`, `derive` |
| `raw_dir`, `processed_dir` | str | из `paths` | каталоги данных |
| `max_concurrent` | int | 5 | параллельных загрузок |
| `force` | bool | False | пересобрать нормализацию, даже если актуальна |

| Метод | Описание |
|---|---|
| `validate()` | проверка параметров; `ValueError` при неверном `mode`/`stage`/`data_types`, пустых `symbols`, отсутствующих датах `backfill`, `end < start`, `max_concurrent < 1` |
| `date_bounds()` | `(start, end)` с учетом режима |

### 9.2. `run_pipeline(params, on_progress=None) -> PipelineResult`

Выполняет этапы для каждого символа по порядку: загрузка → нормализация → проверка → производные датасеты.
Ошибка одного дня/файла не останавливает остальные. `on_progress(fraction: float, text: str)` вызывается по ходу.
`run_pipeline_async` — то же как корутина (для использования внутри уже работающего цикла событий).

### 9.3. `PipelineResult`

| Член | Описание |
|---|---|
| `ingest`, `normalize`, `validate`, `derive` | списки отчетов по этапам и символам |
| `ok` (property) | `True`, если нигде нет `failed`/`FAIL` |
| `summary_lines()` | список строк-сводок для печати |
| `to_dict()` | `{"ok", "summary", "failed": [...]}` — JSON-совместимо |

---

## 10. `backtest_strat.fetchers` — загрузка

```python
from backtest_strat.fetchers import get_fetcher, FETCHERS, OkxFetcher, DownloadStatus, BackfillReport, DownloadResult
```

**`get_fetcher(exchange, **kwargs) -> BaseExchangeFetcher`** — по ключу из `FETCHERS`; неизвестная биржа → `ValueError`.
`kwargs` передаются конструктору (`base_dir`, `max_attempts`, `base_delay`).

### 10.1. `BaseExchangeFetcher`

Шаблонный метод: наследник описывает только «откуда и под каким именем». Повторы, атомарная запись, идемпотентность и
параллельный backfill — в базовом классе.

```python
BaseExchangeFetcher(exchange_name, base_dir=RAW_DIR, max_attempts=5, base_delay=1.0)
```

**Что реализует наследник**

| Член | Описание |
|---|---|
| `supported_data_types` (атрибут класса) | кортеж, напр. `("trades", "orderbook")` |
| `candidate_urls(symbol, target_date, data_type) -> List[str]` | URL архива по приоритету; 404 по одному — пробуется следующий |
| `archive_filename(symbol, target_date, data_type) -> str` | имя файла на диске |

**Что получает наследник бесплатно**

| Метод | Возвращает | Описание |
|---|---|---|
| `await download_historical_range(symbol, start_date, end_date, data_type, max_concurrent=5, on_progress=None, on_partial=None)` | `BackfillReport` | параллельный backfill; падение одного дня не останавливает остальные; `on_progress(done, total)` по дням, `on_partial(fraction)` с учетом байт |
| `await download_daily(symbol, data_type, target_date=None)` | `BackfillReport` | один день; по умолчанию последние полные сутки UTC |
| `await download_daily_archive(session, symbol, target_date, data_type, on_bytes=None)` | `DownloadResult` | один архив; **не бросает исключений** — статус в результате |
| `get_target_path(symbol, target_date, data_type)` | Path | где лежит (или будет лежать) файл |
| `await list_symbols(inst_type="SPOT")` | List[str] | тикеры биржи (если поддержано, иначе `[]`) |
| `await instrument_specs(inst_type="SPOT")` | Dict[str, dict] | `{symbol: {tick_size, lot_size, min_size, multiplier}}` |
| `await spread_snapshot(inst_type="SPOT")` | Dict[str, float] | текущий спред, б.п. — прокси издержек, **не история** |

`data_type` вне `supported_data_types` → `ValueError`.

### 10.2. Отчеты

`DownloadStatus` (`str`-enum): `DOWNLOADED` — скачан сейчас; `SKIPPED` — уже был на диске; `MISSING` — биржа не публиковала
архив (404 по всем URL); `FAILED` — ошибка после всех повторов.

`DownloadResult`: `target_date`, `status`, `path`, `error`.

`BackfillReport`: `symbol`, `data_type`, `results` (по возрастанию даты); `ok` (нет `FAILED`);
`by_status(status) -> List[DownloadResult]`; `summary() -> str`.

### 10.3. `OkxFetcher`

Публичные daily-архивы OKX без API-ключей.

| Тип данных | Источник | Формат |
|---|---|---|
| `trades` | `…/traderecords/trades/daily/<YYYYMMDD>/<SYMBOL>-trades-<YYYY-MM-DD>.zip` | ZIP → CSV |
| `orderbook` | `…/match/orderbook/{pro/,}L2/400lv/daily/…/<SYMBOL>-L2orderbook-400lv-<date>.tar.gz` | TAR.GZ → NDJSON; два пути CDN, пробуются оба |

`inst_type`: `SPOT`, `SWAP`, `FUTURES`. Дополнительно к базовым методам реализует `list_symbols`, `instrument_specs`,
`spread_snapshot` через публичный REST v5.

Отказоустойчивость: повторы при 408/425/429/5xx и обрывах сети с экспоненциальной паузой и джиттером,
`Retry-After` уважается; запись во временный `.part` с проверкой `Content-Length` и атомарным переименованием.

---

## 11. `backtest_strat.normalizers` — нормализация

```python
from backtest_strat.normalizers import get_normalizer, NORMALIZERS, OkxNormalizer, StepStatus, NormalizeReport, StepResult, sort_events
from backtest_strat.normalizers.schema import EVENT_SCHEMA, SORT_KEYS, TRADE, BOOK_SNAPSHOT, BOOK_UPDATE, BUY, SELL
```

**`get_normalizer(exchange, **kwargs) -> BaseNormalizer`** (`raw_dir`, `processed_dir`).

### 11.1. `BaseNormalizer`

Сырой архив → единая схема. Два идемпотентных этапа:
1. `normalize_file`: сырой архив → `processed/<ex>/<sym>/<data_type>/year=/month=/<источник>.parquet`;
2. `build_events`: стакан и сделки за UTC-сутки → `events/…/<YYYY-MM-DD>.parquet`, отсортированные по `SORT_KEYS`.

Чтение потоковое (чанками): память не растет с длиной истории (≈ 0,8 ГБ пик на день BTC-USDT со стаканом).

| Метод | Возвращает | Описание |
|---|---|---|
| `run(symbol, start=None, end=None, force=False, on_progress=None)` | `NormalizeReport` | оба этапа по всем доступным файлам; блокировка на символ; ошибка одного шага не останавливает остальные |
| `normalize_file(symbol, data_type, raw_path, force=False)` | `StepResult` | один сырой архив |
| `build_events(symbol, day, force=False)` | `StepResult` | события за сутки; `incomplete`, если нет стакана или сделок за D+1 |
| `raw_files(symbol, data_type)` | List[Path] | сырые файлы символа |
| `symbol_dir(symbol, layer="processed")` | Path | каталог символа (`"raw"` или `"processed"`) |
| `manifest(symbol)` | `Manifest` | реестр обработанного |

**Для наследника:** `iter_trades(path)`, `iter_orderbook(path)` (генераторы `pyarrow.Table` в `EVENT_SCHEMA`),
`source_date(path)`, `trade_dates_for_utc_day(day)`. Размер чанка — атрибут класса `batch_rows` (250 000).

### 11.2. Отчеты

`StepStatus`: `PROCESSED`, `SKIPPED`, `INCOMPLETE`, `FAILED`.
`StepResult`: `name`, `status`, `outputs`, `error`.
`NormalizeReport`: `symbol`, `results`, `ok`, `by_status(status)`, `summary()`.

### 11.3. `sort_events(table) -> Table`

Детерминированная сортировка по `SORT_KEYS = ("ts", "is_book", "msg_id", "level")`. При равном `ts` сделки раньше стакана.
Нужна, потому что биржа отдает время в миллисекундах и в одной миллисекунде много событий.

### 11.4. `OkxNormalizer`

Сделки: ZIP → CSV (`instrument_name, trade_id, side, price, size, created_time[ms]`); файл сутки UTC+8, поэтому
`trade_dates_for_utc_day(D) = [D, D+1]`. Стакан: TAR.GZ → NDJSON `{instId, action, ts, asks, bids}`, уровни
`[price, size, num_orders]`, снапшот примерно каждую минуту.

---

## 12. `backtest_strat.storage` — чтение и запись

### 12.1. `storage.reader`

```python
from backtest_strat.storage.reader import event_files, iter_events, load_events, derived_files, load_derived
```

| Функция | Возвращает | Описание |
|---|---|---|
| `event_files(symbol, start, end, exchange="okx", processed_dir=PROCESSED_DIR)` | List[Path] | файлы событий за диапазон по дням; отсутствующие дни пропускаются |
| `iter_events(symbol, start, end, exchange, processed_dir, columns=None, batch_size=100_000)` | Iterator[RecordBatch] | потоковое чтение в порядке `SORT_KEYS`; память ∼ `batch_size` |
| `load_events(symbol, start, end, exchange, processed_dir, columns=None)` | pyarrow.Table | все события одной таблицей — только для небольших диапазонов |
| `derived_files(kind, symbol, start, end, exchange, processed_dir)` | List[Path] | файлы `l1` / `bars_1s` / `bars_1m`; неверный `kind` → `ValueError` |
| `load_derived(kind, symbol, start, end, exchange, processed_dir, columns=None)` | pyarrow.Table | датасет за период; пусто → пустая таблица со схемой |

Для pandas: `.to_pandas()`. Колонка `ts` — `timestamp[ns, UTC]`; для tz-naive индекса:
`df["ts"].dt.tz_convert("UTC").dt.tz_localize(None)`.

### 12.2. `storage.parquet`

**`PartitionedParquetWriter(root, filename, schema)`** — пишет поток таблиц в `root/year=YYYY/month=MM/<filename>`,
раскладывая строки по месяцу колонки `ts`. Каждая партиция пишется во временный `.part` и переименовывается только в `commit()`.

| Метод | Описание |
|---|---|
| `write(table)` | добавить таблицу (пустая игнорируется) |
| `commit() -> List[Path]` | закрыть и атомарно опубликовать файлы |
| `abort()` | закрыть и удалить временные файлы |

Как контекстный менеджер при исключении вызывает `abort()`.

**`Manifest(path)`** — JSON-реестр `ключ → {fingerprint, outputs}`.

| Метод | Описание |
|---|---|
| `get(key)` | запись или `None` |
| `is_current(key, fingerprint) -> bool` | отпечаток совпал **и** все выходные файлы на месте |
| `set(key, fingerprint, outputs)` | сохранить (атомарная запись файла) |
| `remove_outputs(key)` | удалить выходы прошлой сборки и запись |

**`partition_dir(root, year, month) -> Path`**.

### 12.3. `storage.layout`

`events_files(processed_dir, exchange, symbol) -> Dict[date, Path]` — `{UTC-день: файл событий}`.

---

## 13. `backtest_strat.derived` — L1 и бары

```python
from backtest_strat.derived import derive_symbol, build_l1, build_bars, build_day, KINDS, L1_SCHEMA, BAR_SCHEMA
```

### 13.1. Датасеты

| `kind` | Содержимое | Источник |
|---|---|---|
| `l1` | лучшие bid/ask и их объемы на каждое изменение вершины | события со стаканом |
| `bars_1s`, `bars_1m` | OHLCV-бары по сделкам | события **или** одни сделки |

Колонки `l1`: `ts, bid_px, bid_sz, ask_px, ask_sz`.
Колонки баров: `ts` (начало интервала), `open, high, low, close, volume, vwap, buy_volume, sell_volume, imbalance`
(`(buy−sell)/volume`, диапазон [−1; 1]), `trades`, `mid_close`, `spread_close` (состояние L1 на конец интервала;
пусто, если бары построены из одних сделок).

Допущения: книга для L1 восстанавливается независимо внутри суток и начинается с первого снапшота; пустые
интервалы в барах не заполняются.

### 13.2. `derive_symbol(symbol, exchange="okx", processed_dir=PROCESSED_DIR, start=None, end=None, force=False, on_progress=None) -> DeriveReport`

Для каждых суток: если есть файл событий — `l1` + оба набора баров; иначе, если покрыты сделки (файлы D и D+1), — только бары
из сделок. Идемпотентно по отпечатку входов и версии логики.

`DeriveReport`: `symbol`, `results: List[DeriveStep]`, `ok`, `summary()`.
`DeriveStep`: `day`, `status` (`processed`/`skipped`/`failed`), `outputs`, `rows`, `error`.

### 13.3. Функции построения

| Функция | Возвращает | Описание |
|---|---|---|
| `build_day(events_path)` | `{kind: Table}` | все датасеты суток из файла событий |
| `build_l1(events_path, batch_size=500_000)` | Table | потоковый проход, одна строка на изменение вершины |
| `build_bars(trades, interval_ns, l1)` | Table | `trades = {"ts","side","price","size"}` (numpy, отсортированы), `l1` — Table (может быть пустой) |
| `derived.trade_bars.build_trade_bars_day(processed_dir, exchange, symbol, day)` | `{kind: Table}` | бары суток из нормализованных сделок |
| `derived.trade_bars.covered_utc_days(source_days)` | List[date] | сутки, для которых есть файлы D и D+1 |

---

## 14. `backtest_strat.validation` — проверки качества

```python
from backtest_strat.validation import validate_symbol, load_reports, validate_events_file, Thresholds, DayReport, OK, WARN, FAIL
from backtest_strat.validation.trades import validate_trade_day, TradeThresholds
```

### 14.1. `validate_symbol(symbol, exchange="okx", processed_dir=PROCESSED_DIR, start=None, end=None, force=False, thresholds=Thresholds(), on_progress=None) -> ValidationSummary`

Проверяет каждые сутки (по событиям, а при отсутствии стакана — по одним сделкам), ищет пропущенные дни внутри диапазона,
пишет отчеты `validation/<день>.json`. Идемпотентно по отпечатку входного файла и версии логики.
`thresholds` относится к дням со стаканом; для дней «только сделки» применяются значения `TradeThresholds` по умолчанию.

`ValidationSummary`: `symbol`; `days: List[dict]` (отчеты дней + `from_cache`); `missing_days: List[str]`;
`status` (`OK`/`WARN`/`FAIL`; пропущенный день = `FAIL`); `ok` (`status != FAIL`); `summary() -> str`.

**`load_reports(exchange, symbol, processed_dir) -> List[dict]`** — сохраненные отчеты без пересчета.

### 14.2. Формат отчета дня (`DayReport.to_dict()`)

Пример (значения условные):

```json
{"day": "2024-01-02", "status": "WARN", "events": 43821261, "book_messages": 4400000, "trades": 549355, "snapshots": 1439,
 "first_ts": "…", "last_ts": "…",
 "checks": [{"name": "no_initial_book", "status": "WARN", "detail": "…", "value": 59.99, "examples": ["…"]}],
 "stats": {"max_gap_s": 0.7, "trades_outside_share": 0.004, "snapshot_best_price_mismatch": 0.02}}
```
Статус дня — худший из статусов проверок.

### 14.3. Проверки по дню со стаканом (`validate_events_file`)

| Проверка | Условие | Статус |
|---|---|---|
| `no_initial_book` | день начинается не со снапшота: книга неполна до первого | WARN, если снапшот ≤ `first_snapshot_warn_s`; иначе FAIL |
| `crossed_book` | лучший bid ≥ лучшего ask после сообщения | FAIL при любом |
| `book_gap` | пауза между сообщениями стакана | WARN > `gap_warn_s`, FAIL > `gap_fail_s` |
| `trade_vs_book` | покупка ниже лучшего bid / продажа выше лучшего ask | WARN при доле > `trade_outside_warn` |
| `price_spike` | скачок mid ≥ `spike_bps` за 1 с с возвратом ≥ `spike_reversal` за окно | WARN |
| `non_positive` | цена или объем сделки ≤ 0, отрицательный объем уровня | FAIL |
| `snapshot_drift` | расхождение книги, восстановленной диффами, со снапшотом биржи (лучшая цена) | WARN при доле > `snapshot_best_price_warn` |

`Thresholds` по умолчанию: `gap_warn_s=30`, `gap_fail_s=300`, `initial_book_max_s=60`, `first_snapshot_warn_s=120`,
`trade_outside_warn=0.01`, `spike_bps=50`, `spike_reversal=0.8`, `spike_reversal_window_s=5`,
`snapshot_best_price_warn=0.05`, `max_examples=5`.

### 14.4. Проверки по дню только со сделками (`validate_trade_day`)

| Проверка | Условие | Статус |
|---|---|---|
| `no_trades` | в сутках нет сделок | FAIL |
| `duplicate_ids` | повторяющиеся `trade_id` | FAIL |
| `non_positive` | цена или объем ≤ 0 | FAIL |
| `trade_gap` | пауза между сделками (включая края суток) | WARN > `gap_warn_s`, FAIL > `gap_fail_s` |
| `price_spike` | скачок ≥ `spike_pct` % минута-к-минуте с возвратом | WARN |
| `day_coverage` | доля минут суток со сделками < `min_active_minutes_share` | WARN |

`TradeThresholds` по умолчанию: `gap_warn_s=3600`, `gap_fail_s=21600`, `spike_pct=5.0`, `spike_reversal=0.8`,
`spike_window_min=5`, `min_active_minutes_share=0.25`, `max_examples=5`.

**Что делать с результатом.** WARN — выпишите причину и решите: принять, исключить день, исключить актив. FAIL — день
нельзя использовать вслепую. Реальные рыночные события (обвалы, «флеш-крэши») дают WARN `price_spike` — это не дефект данных.

---

## 15. `backtest_strat.research` — утилиты исследований

```python
from backtest_strat.research import stats
from backtest_strat.research.bars import minute_bars, to_hourly
from backtest_strat.research.evaluate import fast_pnl, run_schedule
from backtest_strat.research.market import instrument_specs, spread_snapshot
```

### 15.1. `research.bars`

| Функция | Возвращает | Описание |
|---|---|---|
| `minute_bars(symbol, start, end, exchange="okx", processed_dir=PROCESSED_DIR)` | DataFrame | 1-минутные бары; индекс — начало бара (UTC, tz-naive); колонки `open, high, low, close, volume, vwap, buy_volume, sell_volume, imbalance, trades, mid_close, spread_close`; только интервалы со сделками |
| `to_hourly(m, rule="1h")` | DataFrame | агрегат минутных баров: `open, high, low, close, volume, notional, trades, minutes_active`; `close` ffill-ится (цена «стоит»); `notional = Σ volume·vwap` |

### 15.2. `research.evaluate`

**`fast_pnl(ret, pos, cost_bps_per_side, notional) -> Series`**
Векторный PnL в валюте котировки: `notional · (pos · (e^ret − 1) − |Δpos| · cost/1e4)`.

| Параметр | Описание |
|---|---|
| `ret` | лог-доходность внутри периода `t` |
| `pos` | позиция (−1/0/+1), **принятая до** периода `t` |
| `cost_bps_per_side` | издержки на единицу изменения позиции: комиссия + полуспред (+ 0,5 тика у крупнотиковых активов) |
| `notional` | сумма позиции |

Разворот `+1 → −1` считается двумя единицами оборота. Быстрая оценка предназначена для перебора и плацебо;
итоговые цифры берите из движка.

**`run_schedule(minute, positions, symbol, tick_size, lot_size, half_spread_bps, taker_fee_bps, notional, order_latency_ms=3.0, equity_sample_ns=3_600_000_000_000) -> dict`**
Прогон расписания позиций через движок на синтетическом стакане из минутных баров.

| Параметр | Описание |
|---|---|
| `minute` | DataFrame минутных баров (из `minute_bars`); используется `close` и индекс |
| `positions` | Series −1/0/+1, индекс — момент принятия решения (начало периода) |
| `half_spread_bps`, `taker_fee_bps` | полуспред и комиссия в б.п. |
| `notional` | сумма позиции |

Возвращает: `net_pnl`, `fees`, `fills`, `notional_traded`, `equity_hourly` (Series), `result` (`BacktestResult`).

### 15.3. `research.market`

Синхронные обертки (работают в скриптах и в Jupyter):

| Функция | Возвращает |
|---|---|
| `instrument_specs(exchange="okx", inst_type="SPOT")` | `{symbol: {tick_size, lot_size, min_size, multiplier}}` |
| `spread_snapshot(exchange="okx", inst_type="SPOT")` | `{symbol: спред в б.п.}` — снимок «сейчас», не история |

### 15.4. `research.stats`

Единица независимости — **сутки**. Подробное обоснование и калибровка — в журнале exp01
([research/exp01_seasonal_weather_liquidity/PROBLEMS.md](../research/exp01_seasonal_weather_liquidity/PROBLEMS.md), P12).

**`edge_ttest(signal, ret, valid, day_ids=None) -> dict`** — основной тест связи сигнала с доходностью.

| Параметр | Описание |
|---|---|
| `signal` | массив −1/0/+1; построен **только по прошлому** |
| `ret` | простая доходность (`np.expm1` от лог) того же периода |
| `valid` | булев массив — где признаки определены (иначе час не участвует) |
| `day_ids` | целочисленные метки суток; по умолчанию `arange(n)//24` (ряд с шагом час, начинающийся в 00:00) |

Вклад часа `S·(r − r̄)`; суточные суммы проверяются t-тестом. Вычитание среднего убирает ложный edge от дрейфа.
Возвращает ключи: `edge_bps` (среднее `S·r` на активный час), `edge_excess_bps` (то же для `S·(r−r̄)`), `t`, `p` (двусторонний),
`n_active`, `n_days`. Калибровка: ложные срабатывания 4,6–5,3% при номинальных 5%; мощность 60% при эффекте 15 б.п. в 4 часах суток.

**`placebo_selectivity(contrib, signal, mask, rng, draws, min_shift) -> dict`** — «фильтр выбирает лучшие часы?»
Статистика `E[S·r | mask] − E[S·r | ¬mask]` на активных часах (б.п.), плацебо — сдвиг маски на случайное число суток.
Ключи: `selectivity_bps`, `p`, `n_on`. **Ограничение:** маска, строго периодичная по суткам («первые 12 часов»), при сдвиге
не меняется — `p = 1`; метод для масок-режимов (погода, относительная ликвидность), не для фильтров «час суток».

**`benjamini_hochberg(p: Series) -> Series`** — FDR-поправка; возвращает q-значения; `NaN` игнорируются.
Применяйте по всему семейству проверенных гипотез (активы × варианты).

**`daily_tstat(hourly_pnl: Series) -> float`** — t среднего суточного PnL (суммы по UTC-суткам). **`sharpe_hourly(hourly_pnl, notional) -> float`** — годовой Sharpe по почасовым доходностям (`√(24·365)`).

**`cluster_ols(y, X, groups) -> dict`** — OLS с кластерно-робастными ошибками (CR1) по группам; ключи `beta`, `se`, `t`.

**`interaction_regression(f: DataFrame) -> dict`** — регрессия `ret(б.п.) ~ a + b0·S + b1·S·W + b2·S·(L−1)`;
нужные колонки `ret, seasonal_sig, weather_z, rel_liq`; ключи `b_seasonal, t_seasonal, b_weather, t_weather, b_liq, t_liq, n`.

**`placebo_edge(...)`** — **устарела**, оставлена для сравнения: плацебо по сдвигу сигнала не обменяемо (ложные срабатывания
8–15%). Используйте `edge_ttest`.

---

## 16. `backtest_strat.external.weather` — погода

Open-Meteo Archive (реанализ ERA5), без ключа. Кэш: `<PIPELINE_EXTERNAL_DIR>/weather/<город>.parquet`; докачиваются
только недостающие даты. Повторы при 429/5xx.

**Точка во времени.** Значение суток D известно только после их конца, поэтому `load_daily` по умолчанию сдвигает ряд на
`lag_days=1`. Реанализ — не прогноз, который был доступен в тот момент: для поиска закономерностей допустимо,
для оценки реальной торговой пригодности — нет.

| Имя | Описание |
|---|---|
| `CITIES` | `{город: (lat, lon)}`: `new_york`, `london`, `tokyo`, `hong_kong` |
| `VARIABLES` | `("temperature_2m_mean",)` |
| `load_city(city, start, end, variables=VARIABLES, cache_dir=CACHE_DIR) -> DataFrame` | значения города «как есть», колонка `date` |
| `load_daily(start, end, cities=None, variables=VARIABLES, lag_days=1) -> DataFrame` | широкая таблица; индекс — дата **использования**; колонки `<город>__<переменная>`; значение в строке D — наблюдение за D−`lag_days` |

---

## 17. `backtest_strat.scheduler` — ночной запуск

Два независимых выключателя: **строка в crontab** (запускается ли процесс) и **флаг `enabled`** в `config/schedule.json`
(делает ли запуск что-то). Выключенный запуск пишет в журнал «пропуск» и завершается кодом 0. По умолчанию
`enabled: false`.

Каждый запуск перепроверяет последние `lookback_days` суток: если машина была выключена или биржа выложила архив с
опозданием, пропуск закроется сам; готовое пропускается.

Конфигурация `config/schedule.json`:

| Ключ | По умолч. | Описание |
|---|---|---|
| `enabled` | `false` | включено ли расписание |
| `time` | `"00:30"` | время запуска `HH:MM` (локальное время сервера) |
| `exchange` | `"okx"` | биржа |
| `symbols` | `["BTC-USDT"]` | тикеры |
| `data_types` | `["trades","orderbook"]` | типы данных |
| `lookback_days` | 3 | сколько последних суток перепроверять |
| `raw_dir`, `processed_dir` | из `paths` | каталоги |

| Функция | Возвращает | Описание |
|---|---|---|
| `load_config(path=CONFIG_PATH)` | dict | конфиг поверх значений по умолчанию |
| `save_config(cfg, path=CONFIG_PATH)` | None | атомарная запись; неверное `time` → `ValueError` |
| `set_enabled(enabled, path=CONFIG_PATH)` | dict | включить/выключить |
| `cron_line(time_hhmm)` | str | строка crontab (без установки) |
| `install(time_hhmm=None)` | str | поставить/заменить **только свою** строку в crontab (чужие не трогаются) |
| `uninstall()` | bool | удалить свою строку; `False`, если ее не было |
| `installed_cron_line()` | str \| None | текущая строка |
| `status()` | dict | `enabled`, `cron_installed`, `cron_line`, `cron_error`, `will_run`, `config` |
| `run(force_run=False)` | int | выполнить; `0` успех/пропуск, `1` есть `FAILED` |

Окружение: `PIPELINE_SCHEDULE_CONFIG` (путь к конфигу), `PIPELINE_WORKDIR` (рабочая папка cron-задачи),
`PIPELINE_CRONTAB_BIN` (для тестов — подмена `crontab`).

> `install()` должна вызываться из **папки вашего проекта** (или с `PIPELINE_WORKDIR`): cron запускает `python -m
> backtest_strat.scheduler run` именно оттуда, и относительные пути данных считаются от нее. Вывод cron уходит в `/dev/null`,
> события пишутся в общий журнал `logs/`.

---

## 18. `backtest_strat.utils` — вспомогательные модули

### 18.1. `utils.dates`

| Функция | Описание |
|---|---|
| `date_range(start, end) -> List[date]` | все даты включительно; `end < start` → `ValueError` |
| `parse_date(value: str) -> date` | формат `YYYY-MM-DD` |
| `yesterday_utc() -> date` | последние полные сутки по UTC — за них биржи публикуют daily-архивы |

### 18.2. `utils.retry`

| Имя | Описание |
|---|---|
| `RetryableError(message, retry_after=None)` | временная ошибка (обрыв, 429, 5xx); `retry_after` — пауза от сервера, сек |
| `NotFoundError` | ресурса нет (404); повторять бессмысленно |
| `RETRYABLE_STATUSES` | `{408, 425, 429, 500, 502, 503, 504}` |
| `compute_backoff(attempt, base_delay, max_delay) -> float` | экспоненциальная пауза с full jitter |
| `await retry_async(func, *, max_attempts=5, base_delay=1.0, max_delay=60.0, description="request")` | повторяет `func` при `RetryableError`; `Retry-After` приоритетнее расчетной паузы; остальные исключения пробрасываются сразу |

### 18.3. `utils.http`

**`await download_to_file(session, url, dest, on_progress=None) -> Path`** — атомарная загрузка: пишет в
`<dest>.<hex>.part`, проверяет `Content-Length`, затем `os.replace`. 404 → `NotFoundError`; повторяемые статусы и
обрывы → `RetryableError`; прочие коды → `RuntimeError`. `on_progress(written, total_or_None)`.

### 18.4. `utils.locks`

**`file_lock(path)`** — контекстный менеджер: межпроцессная эксклюзивная блокировка (`flock`). Ночной cron и задача из
веб-интерфейса по одному символу выполняются по очереди. Снимается ОС автоматически, даже если процесс упал.

### 18.5. `utils.logging_setup`

Единый журнал: суточные файлы `pipeline-YYYY-MM-DD.log` (UTC), формат
`время [LEVEL] [источник] логгер: сообщение`, хранение 30 дней.

| Функция | Описание |
|---|---|
| `setup_logging(source, level="INFO", console=True, log_dir=None)` | настроить корневой логгер процесса; `source` — метка (`cli`, `cron`, `api`, `ui`, свое); повторный вызов безопасен |
| `read_logs(log_dir=None, days=1, levels=None, source=None, query=None, limit=500) -> List[dict]` | последние записи (новые первыми): `time, level, source, logger, message` |
| `cleanup_old_logs(log_dir, retention_days)` | удалить файлы старше срока |

Окружение: `PIPELINE_LOG_DIR` (по умолчанию `logs`), `PIPELINE_LOG_RETENTION_DAYS` (30).
Метку источника отдельной записи можно переопределить: `logger.info("…", extra={"source": "my_tool"})`.

### 18.6. `utils.sync`

**`run_sync(coro) -> T`** — `asyncio.run`, который работает и внутри уже запущенного цикла событий (Jupyter):
тогда корутина выполняется в отдельном потоке. Исключения пробрасываются.

### 18.7. `utils.simple_book`

**`SimpleBook`** — облегченная книга «цена → объем» для проходов по событиям (валидация, L1).
`set(side, price, size)` (объем ≤ 0 удаляет уровень), `reset()`, `finalize()` (пересчитать лучшие цены), `valid()`;
атрибуты `bids`, `asks`, `bb`, `ba`. Для симуляции исполнения используйте `ReplayBook`.

---

## 19. `backtest_strat.paths` — пути данных

Значения читаются из окружения **при импорте модуля**; относительные пути — от текущей папки процесса.

| Переменная | По умолчанию | Константа |
|---|---|---|
| `PIPELINE_DATA_DIR` | `data` | `DATA_DIR` |
| `PIPELINE_RAW_DIR` | `$DATA_DIR/raw` | `RAW_DIR` |
| `PIPELINE_PROCESSED_DIR` | `$DATA_DIR/processed` | `PROCESSED_DIR` |
| `PIPELINE_EXTERNAL_DIR` | `$DATA_DIR/external` | `EXTERNAL_DIR` |
| `PIPELINE_JOBS_DB` | `$DATA_DIR/jobs.sqlite` | `JOBS_DB` |
| `PIPELINE_WORKDIR` | `.` | `WORKDIR` |
| `PIPELINE_LOG_DIR` | `logs` | (в `utils.logging_setup`) |
| `PIPELINE_SCHEDULE_CONFIG` | `config/schedule.json` | (в `scheduler`) |

Частный случай «разные данные для разных запусков» — передавайте каталоги явно параметрами (`raw_dir=`, `processed_dir=`).

---

## 20. Консольные команды

Устанавливаются вместе с пакетом в `bin/` окружения.

### `bt-data` — данные

```
bt-data --exchange okx --symbol BTC-USDT ETH-USDT --mode backfill --start 2024-01-01 --end 2024-01-31 \
        --data-type trades --stage all
```

| Флаг | Значения | Умолч. | Описание |
|---|---|---|---|
| `--exchange` | ключи реестра | — (обяз.) | биржа |
| `--symbol` | один или несколько | — (обяз.) | тикеры |
| `--mode` | `backfill`, `daily` | `backfill` | диапазон или один день |
| `--start`, `--end` | `YYYY-MM-DD` | — | для backfill обязательны; для daily `--end` — дата вместо вчерашней |
| `--data-type` | `trades`, `orderbook` (несколько) | оба | какие архивы |
| `--stage` | `all`, `ingest`, `normalize`, `validate`, `derive` | `all` | этап |
| `--raw-dir`, `--processed-dir` | путь | из `paths` | каталоги |
| `--concurrency` | int | 5 | параллельных загрузок |
| `--force` | флаг | — | пересобрать нормализацию |
| `--quiet` | флаг | — | без прогресс-бара |
| `--log-level` | `DEBUG`…`ERROR` | `INFO` | уровень журнала |

Код возврата: `0` — без ошибок; `1` — были `FAILED`/`FAIL`; `2` — неверные аргументы.

### `bt-backtest` — прогон стратегии

```
bt-backtest --strategy strategies.my_alpha:MyAlpha --param size=0.01 --param lookback=30 \
            --symbol BTC-USDT --start 2024-01-01 --end 2024-01-02 --tick 0.1 --lot 0.00000001
```

| Флаг | Описание |
|---|---|
| `--strategy` | `модуль:Класс` из вашего проекта (ищется от текущей папки) |
| `--param key=value` | аргумент конструктора, значение — JSON; повторяемый |
| `--symbol`, `--start`, `--end`, `--exchange` | что прогонять (по умолчанию `okx`) |
| `--tick`, `--lot`, `--multiplier` | параметры инструмента (обяз. `--tick`, `--lot`) |
| `--maker-fee-bps`, `--taker-fee-bps` | 8.0 / 10.0 |
| `--feed-latency-ms`, `--order-latency-ms` | 1.0 / 2.0 |
| `--processed-dir` | каталог данных |
| `--fills-csv` | сохранить сделки в CSV |

### `bt-schedule` — планировщик

`bt-schedule {status | enable | disable | install [--time HH:MM] | uninstall | run [--force-run] | show-cron}`

### `bt-api`, `bt-dashboard`, `bt-monitor` — веб-части (extras `api`/`ui`)

| Команда | Что | Примечание |
|---|---|---|
| `bt-api` | HTTP API фоновых задач | слушает `127.0.0.1:8000`; другой порт: `uvicorn backtest_strat.api.app:app --port 8001` |
| `bt-dashboard` | дашборд: задачи, покрытие, качество, расписание, логи | дополнительные аргументы передаются в `streamlit run` (напр. `--server.port 8501`) |
| `bt-monitor` | монитор процесса: читает состояние с диска текущей папки | то же |

Спецификация эндпоинтов API — [web_ui.md](web_ui.md).

---

## 21. Версионирование и стабильность

* Версия пакета — `backtest_strat.__version__` (сейчас `0.1.0`); следует семантическому версионированию в рамках 0.x:
  несовместимые изменения публичного интерфейса возможны в минорных версиях и будут отмечены.
* **Публичным** считается то, что описано в части II. Модули и функции с префиксом `_`, а также внутренние детали
  (формат манифестов и отчетов — кроме описанных полей, имена временных файлов) могут меняться.
* Формат данных на диске (`EVENT_SCHEMA`, `L1_SCHEMA`, `BAR_SCHEMA`, раскладка каталогов) считается контрактом;
  изменение логики производных датасетов сопровождается повышением внутренней версии, после чего датасеты пересоберутся сами.
* Старое имя пакета `src` существует **только в репозитории** как алиас для устаревших тестов и в wheel не входит.
