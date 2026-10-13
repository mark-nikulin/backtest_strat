# Архитектура модуля Ingestion (Сбор сырых данных)

Модуль `backtest_strat/fetchers` отвечает за отказоустойчивую загрузку исторических архивов рыночных данных (сделки, снимки и обновления стакана) с криптовалютных бирж без использования приватных API-ключей.

---

## 1. Архитектура и принципы работы

### 1.1. Базовые абстракции
Архитектура построена на паттерне «Шаблонный метод» (Template Method):
* [`BaseExchangeFetcher`](file:///Users/kyle/main/py_files/backtest_strat/backtest_strat/fetchers/base.py) инкапсулирует всю общую инфраструктурную логику:
  * Проверка идемпотентности (пропуск уже скачанных файлов).
  * Атомарная запись на диск через временные `.part` файлы.
  * Механизм повторов (Exponential Backoff + full jitter, обработка `Retry-After`).
  * Параллельный Backfill с ограничением конкурентности (`asyncio.Semaphore`).
  * Сбор отчета `BackfillReport` с изоляцией ошибок (падение одного дня не ломает весь диапазон).
* Наследники (например, [`OkxFetcher`](file:///Users/kyle/main/py_files/backtest_strat/backtest_strat/fetchers/okx.py)) определяют только специфику конкретной биржи:
  * Список поддерживаемых типов данных (`supported_data_types`).
  * Схему формирования имени архива (`archive_filename`).
  * Список кандидатных URL-адресов в порядке приоритета (`candidate_urls`).

---

## 2. Раскладка хранения сырых данных (Storage Layout)

Все скачиваемые архивы сохраняются по строгой иерархической схеме:

```text
data/raw/
└── <exchange>/
    └── <symbol>/
        └── <data_type>/
            └── <archive_filename>
```

### Примеры путей для OKX:
* **Сделки:** `data/raw/okx/BTC-USDT/trades/BTC-USDT-trades-2024-01-01.zip`
* **Стакан L2 400lv:** `data/raw/okx/BTC-USDT/orderbook/BTC-USDT-L2orderbook-400lv-2024-01-01.tar.gz`
* **Свопы (Perpetual):** `data/raw/okx/BTC-USDT-SWAP/trades/BTC-USDT-SWAP-trades-2024-01-01.zip`

> [!NOTE]
> Директория `data/` добавлена в `.gitignore`, поэтому сырые гигабайтные архивы никогда не попадут в Git-репозиторий.

---

## 3. Статусы загрузки (DownloadStatus)

Каждый результат скачивания суток возвращает объект `DownloadResult` со статусом `DownloadStatus`:

| Статус | Описание |
| :--- | :--- |
| `DOWNLOADED` | Архив успешно скачан по сети и верифицирован по Content-Length. |
| `SKIPPED` | Архив уже существует на диске и имеет ненулевой размер (идемпотентность). Повторных сетевых запросов не выполняется. |
| `MISSING` | Архив отсутствует на серверах биржи за данную дату (все URL из `candidate_urls` вернули HTTP 404). Повторы не делаются. |
| `FAILED` | Ошибка скачивания после исчерпания всех попыток (сеть, 429, 5xx). |

---

## 4. Механизмы надежности (Fault Tolerance)

1. **Атомарная запись через `.part`:**
   Файл скачивается в `<name>.part`. Переименование в итоговый `<name>` происходит только после полной загрузки чанков и совпадения размера с заголовком `Content-Length`. Если соединение оборвалось, файл `.part` удаляется, а битый архив не сохраняется.
2. **Exponential Backoff с Full Jitter:**
   При ошибках `408`, `425`, `429`, `500`, `502`, `503`, `504` и сетевых разрывах выполняется повтор с нарастающей случайной паузой. Если сервер прислал заголовок `Retry-After`, скрипт уважает паузу от сервера.
3. **Изоляция ошибок:**
   В методе `download_historical_range` сбой одного дня заносится в `report.results` как `FAILED`, но не останавливает загрузку остальных дней диапазона.
4. **Fallback URL:**
   Если биржа меняла CDN-пути (как у OKX: `/pro/` для свежих архивов и путь без `/pro/` для старых), загрузчик по очереди пробует список из `candidate_urls`.

---

## 5. Публичный API модуля

```python
from datetime import date
from backtest_strat.fetchers import get_fetcher, DownloadStatus

# 1. Получение экземпляра загрузчика
fetcher = get_fetcher("okx", base_dir="data/raw")

# 2. Backfill диапазона дат
report = await fetcher.download_historical_range(
    symbol="BTC-USDT",
    start_date=date(2024, 1, 1),
    end_date=date(2024, 1, 10),
    data_type="trades",
    max_concurrent=5,
    on_progress=lambda done, total: print(f"Progress: {done}/{total}"),
)

print(report.summary())
# -> BTC-USDT/trades: downloaded=10, skipped=0, missing=0, failed=0

# 3. Daily режим (вчерашний полный день по UTC)
daily_report = await fetcher.download_daily(
    symbol="BTC-USDT",
    data_type="trades",
    target_date=None,  # по умолчанию yesterday_utc()
)

# 4. Рыночные метаданные (инструменты, спецификации, спреды)
symbols = await fetcher.list_symbols(inst_type="SPOT")
specs = await fetcher.instrument_specs(inst_type="SPOT")      # tick_size, lot_size, min_size
spreads = await fetcher.spread_snapshot(inst_type="SPOT")    # срез bid-ask спредов в базисных пунктах
```

---

## 6. Как добавить новую биржу (Extending to New Exchanges)

Чтобы подключить новую площадку (например, `bybit`, `binance`), достаточно выполнить 3 шага:

### Шаг 1: Создать класс-наследник в `backtest_strat/fetchers/<exchange>.py`
```python
from datetime import date
from typing import List
from backtest_strat.fetchers.base import BaseExchangeFetcher

class BybitFetcher(BaseExchangeFetcher):
    supported_data_types = ("trades", "orderbook")

    def __init__(self, base_dir: str = "data/raw", **kwargs):
        super().__init__("bybit", base_dir=base_dir, **kwargs)

    def archive_filename(self, symbol: str, target_date: date, data_type: str) -> str:
        d = target_date.isoformat()
        return f"{symbol}-{data_type}-{d}.csv.gz"

    def candidate_urls(self, symbol: str, target_date: date, data_type: str) -> List[str]:
        # Формирование ссылок на CDN / открытые архивы биржи
        filename = self.archive_filename(symbol, target_date, data_type)
        return [f"https://public.bybit.com/trading/{symbol}/{filename}"]
```

### Шаг 2: Зарегистрировать загрузчик в реестре `backtest_strat/fetchers/__init__.py`
```python
from backtest_strat.fetchers.bybit import BybitFetcher

FETCHERS = {
    "okx": OkxFetcher,
    "bybit": BybitFetcher,  # <- добавить одну строку
}
```

### Шаг 3: Добавить зеркальные тесты в `tests/fetchers/test_bybit.py`
Проверить генерацию URL, имен файлов и обработку статусов через `mock_server`.
