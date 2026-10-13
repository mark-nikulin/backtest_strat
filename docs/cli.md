# Документация командной строки (CLI)

Консольная утилита [`backtest_strat.cli`](file:///Users/kyle/main/py_files/backtest_strat/backtest_strat/cli.py) предназначена для ручного запуска и автоматизации (cron) сбора и нормализации рыночных данных.

---

## 1. Параметры командной строки

```bash
python -m backtest_strat.cli --exchange <EXCHANGE> --symbol <SYMBOL...> [ОПЦИИ]
```

| Флаг | Тип / Значения | По умолчанию | Описание |
| :--- | :--- | :--- | :--- |
| `--exchange` | `okx` | *(обязательный)* | Идентификатор биржи из реестра `FETCHERS`. |
| `--symbol` | строки (`nargs=+`) | *(обязательный)* | Один или несколько тикеров через пробел (`BTC-USDT`, `ETH-USDT-SWAP`). |
| `--mode` | `backfill`, `daily` | `backfill` | Режим работы: диапазон дат или регулярная загрузка. |
| `--start` | `YYYY-MM-DD` | `None` | Начальная дата диапазона (обязателен для `backfill`). |
| `--end` | `YYYY-MM-DD` | `None` | Конечная дата (для `backfill` обязателен; для `daily` заменяет вчерашнюю дату UTC). |
| `--data-type` | `trades`, `orderbook` | оба типа | Типы данных для обработки. |
| `--stage` | `all`, `ingest`, `normalize`, `validate`, `derive` | `all` | Этап пайплайна (`all` = скачать, нормализовать, проверить и собрать производные). |
| `--raw-dir` | путь к директории | `data/raw` | Каталог сохранения сырых архивов. |
| `--processed-dir`| путь к директории | `data/processed` | Каталог сохранения Parquet-файлов. |
| `--concurrency` | целое число $\ge 1$ | `5` | Количество параллельных потоков скачивания в режиме `backfill`. |
| `--force` | флаг | `False` | Принудительно пересобрать данные, игнорируя манифест. |
| `--quiet` | флаг | `False` | Отключить вывод интерактивного прогресс-бара. |
| `--log-level` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | `INFO` | Уровень подробности логирования. |

---

## 2. Коды возврата (Exit Codes)

Коды возврата стандартизированы для интеграции в CI/CD и планировщики:

* **`0`** — Успешное выполнение. Все запрошенные дни скачаны/нормализованы без ошибок (статусы `DOWNLOADED`, `SKIPPED`, `PROCESSED`).
* **`1`** — Сбой выполнения. В процессе возникли ошибки на одном или нескольких шагах (статус `FAILED`). Подробности выводятся в `sys.stderr`.
* **`2`** — Ошибка валидации аргументов (неверный формат даты, пропущен обязательный параметр, `end < start`, неподдерживаемая биржа).

---

## 3. Примеры использования

### 3.1. Исторический Backfill за месяц
Скачивание и нормализация сделок и стакана по паре `BTC-USDT` за январь 2024 года с параллелизмом в 8 потоков:
```bash
python -m backtest_strat.cli \
  --exchange okx \
  --symbol BTC-USDT \
  --mode backfill \
  --start 2024-01-01 \
  --end 2024-01-31 \
  --concurrency 8
```

### 3.2. Backfill только сделок для нескольких инструментов
Скачивание только сырых сделок без запуска этапа нормализации:
```bash
python -m backtest_strat.cli \
  --exchange okx \
  --symbol BTC-USDT ETH-USDT SOL-USDT \
  --mode backfill \
  --start 2024-02-01 \
  --end 2024-02-10 \
  --data-type trades \
  --stage ingest
```

### 3.3. Принудительная пересборка нормализации (Force)
Пересобрать уже скачанные сырые данные в Parquet:
```bash
python -m backtest_strat.cli \
  --exchange okx \
  --symbol BTC-USDT \
  --mode backfill \
  --start 2024-01-01 \
  --end 2024-01-05 \
  --stage normalize \
  --force
```

### 3.4. Daily режим (вчерашний полный день UTC)
Загрузка и сборка данных за вчерашний день (UTC):
```bash
python -m backtest_strat.cli \
  --exchange okx \
  --symbol BTC-USDT BTC-USDT-SWAP \
  --mode daily \
  --quiet
```

---

## 4. Настройка автоматического запуска через Crontab

Для ежедневного регулярного сбора данных рекомендуется запускать CLI ранним утром по UTC (например, в 03:15 UTC, когда все архивы за прошлый день гарантированно сформированы биржей):

```cron
# Запуск daily-сбора каждый день в 03:15 UTC
15 3 * * * cd /path/to/backtest_strat && /path/to/backtest_strat/.venv/bin/python -m backtest_strat.cli --exchange okx --symbol BTC-USDT --mode daily --quiet >> /path/to/backtest_strat/data/logs/cron.log 2>&1
```
