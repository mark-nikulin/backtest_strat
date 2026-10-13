# Исследовательский воркфлоу: от гипотезы к бэктесту

Данный документ описывает стандартный исследовательский пайплайн («идея → векторный тест → верификация на симуляторе»), инструменты проекта для низкочастотных и среднечастотных альф, ограничения симуляции на барах и правила защиты от заглядывания в будущее (look-ahead bias).

В качестве эталонного примера реализации см. шаблон исследования [`research/exp01_seasonal_weather_liquidity/`](file:///Users/kyle/main/py_files/backtest_strat/research/exp01_seasonal_weather_liquidity/).

---

## 1. Общий пайплайн исследования

```mermaid
flowchart TD
    Raw[Сырые сделки / L2] --> Derived[Почасовые / 1m бары (trade_bars)]
    Derived --> Quality[Проверка качества (trades validation)]
    Ext[Внешние ряды (weather, Open-Meteo)] -->|lag_days=1| Features[Построение признаков (Point-in-Time)]
    Quality --> Features
    Features --> FastEval[Быстрая векторная оценка (fast_pnl, placebo_edge)]
    FastEval -->|Сигнал устойчив| EngineEval[Симуляция в движке (bar_feed + ScheduledPosition)]
    EngineEval --> Decision[Итоговый отчет & аудит]
```

1. **Формулировка гипотезы и выбор гранулярности:**
   - Низкочастотные сигналы (часы, дни): достаточно минутных или часовых баров.
   - Микроструктурные сигналы (секунды, миллисекунды, очередь заявок): требуется полноценный L2-поток событий (`events/`).
2. **Подготовка и валидация данных:**
   - Агрегация минутных баров через [`trade_bars`](file:///Users/kyle/main/py_files/backtest_strat/src/derived/trade_bars.py) или [`load_derived`](file:///Users/kyle/main/py_files/backtest_strat/src/storage/reader.py).
   - Проверка полноты и чистоты данных через [`validate_symbol`](file:///Users/kyle/main/py_files/backtest_strat/src/validation/trades.py).
3. **Генерация признаков (Point-in-Time):**
   - Строгое соблюдение временной границы: признаки в строке $t$ используют только информацию, известную до $t$.
   - Внешние источники (например, погода через [`load_daily`](file:///Users/kyle/main/py_files/backtest_strat/src/external/weather.py)) загружаются с обязательным сдвигом `lag_days >= 1`.
4. **Быстрый векторный отбор (Fast Prototyping):**
   - Оценка PnL и оборота через [`fast_pnl`](file:///Users/kyle/main/py_files/backtest_strat/src/research/evaluate.py).
   - Проверка статистической устойчивости через [`daily_tstat`](file:///Users/kyle/main/py_files/backtest_strat/src/research/stats.py), [`placebo_edge`](file:///Users/kyle/main/py_files/backtest_strat/src/research/stats.py), [`placebo_selectivity`](file:///Users/kyle/main/py_files/backtest_strat/src/research/stats.py) и поправку на множественное тестирование [`benjamini_hochberg`](file:///Users/kyle/main/py_files/backtest_strat/src/research/stats.py).
5. **Симуляция исполнения на движке (Execution Simulation):**
   - Прогон расписания позиций через [`run_schedule`](file:///Users/kyle/main/py_files/backtest_strat/src/research/evaluate.py) с использованием [`ScheduledPosition`](file:///Users/kyle/main/py_files/backtest_strat/src/backtest/schedule.py) и генератора синтетических событий [`bars_to_events`](file:///Users/kyle/main/py_files/backtest_strat/src/backtest/bar_feed.py).

---

## 2. Когда достаточно сделок, а когда необходим стакан (L2)

Выбор источника данных напрямую влияет на объем дискового пространства и вычислительные затраты:

| Критерий | Достаточно сделок (`trades-only` / бары) | Необходим стакан (`orderbook` / L2 `events`) |
| :--- | :--- | :--- |
| **Характер стратегии** | Позиционная, свинговая, статистический арбитраж (горизонт от часов до дней). | HFT, маркет-мейкинг, пассивные лимитные ордера, микроструктурный арбитраж. |
| **Моделирование исполнения** | Рыночные ордера (taker) на ликвидных инструментах при небольшом размере позиции ($<\text{notional}$ минуты). | Пассивное исполнение (maker), конкуренция за очередь заявок (`queue_ahead`), симуляция отмен. |
| **Оценка спреда** | Параметрическая: медианный спред инструмента или снапшот плюс половина шага цены. | Динамическая: микроколебания спреда и дисбаланс объемов на уровнях (Order Book Imbalance). |
| **Требования к диску** | ~10-50 МБ на инструмент в месяц (компактный Parquet). | ~5-30 ГБ на инструмент в месяц (L2 tar.gz и распакованные события). |

---

## 3. Модель `bar_feed` и её ограничения

Модуль [`src/backtest/bar_feed.py`](file:///Users/kyle/main/py_files/backtest_strat/src/backtest/bar_feed.py) преобразует минутные бары в события `EVENT_SCHEMA` (`BOOK_SNAPSHOT`):

* На момент закрытия бара ($t + \Delta t$) формируется снапшот стакана с одним уровнем покупки и продажи:
  $$\text{bid} = \lfloor \text{close} \times (1 - \text{half\_spread\_bps} / 10^4) / \text{tick} \rfloor \times \text{tick}$$
  $$\text{ask} = \lceil \text{close} \times (1 + \text{half\_spread\_bps} / 10^4) / \text{tick} \rceil \times \text{tick}$$
  $$\text{полуспред} \ge \text{tick\_size}$$
* Объем на каждом уровне устанавливается искусственно большим ($10^{12}$).

> [!WARNING]
> ### Критические ограничения синтетической книги:
> 1. **Отсутствие реальной глубины:** объемы на синтетических уровнях не ограничены, проскальзывание от размера ордера (market impact) отсутствует. Подходит только для небольших позиций.
> 2. **Фиксированный спред:** спред задается параметром (`half_spread_bps`), а не наблюдается на рынке.
> 3. **Дискретность времени:** между закрытиями свечей котировки статичны; внутриминутная динамика не воспроизводится.
> 4. **Неприменимость для пассивных ордеров:** модель приоритета в очереди бессмысленна на синтетическом стакане.

---

## 4. Правила точки во времени (Point-in-Time)

Look-ahead bias — главная причина ложной доходности бэктестов. В проекте действуют строгие правила согласования временных меток:

1. **Метка закрытия свечи:**
   - Стратегия узнает цену `close` свечи, начинающейся в момент $t$, только в момент $t + \text{interval}$ (например, свеча 00:00:00 при интервале 1 минута закрывается в 00:01:00).
   - В `bars_to_events` временная метка снапшота строго равна `bar_ts_ns + interval_ns`.
2. **Индекс часовой строки:**
   - В датафреймах признаков строка с индексом $T$ (например, `14:00:00`) обозначает час, **начинающийся** в момент $T$.
   - Все признаки (`seasonal_mu`, `rel_liq`, скользящие средние) вычисляются со сдвигом `shift(1)`, используя данные строго до момента $T$.
   - `ret` в строке $T$ — это доходность, реализуемая **внутри** часа $T$ (от закрытия часа $T-1$ до закрытия часа $T$).
3. **Задержка внешних данных (`lag_days >= 1`):**
   - Суточные агрегаты (погода Open-Meteo, макропоказатели) публикуются с задержкой.
   - В функции [`load_daily(start, end, lag_days=1)`](file:///Users/kyle/main/py_files/backtest_strat/src/external/weather.py) наблюдение за сутки $D$ сопоставляется строке $D + \text{lag\_days}$.

---

## 5. Контроль качества данных и интерпретация отчетов

Перед запуском исследования необходимо проверить чистоту исходных рядов:

1. **Валидация сделок (`src/validation/trades.py`):**
   - **`FAIL` (недопустимо):** пустые сутки, дубликаты `trade_id`, неположительные цены или объемы ($\le 0$), паузы в сделках $> 6$ часов.
   - **`WARN` (требует внимания):** паузы в сделках $> 1$ часа, скачки цены $\ge 5\%$ за минуту с последующим возвратом $\ge 80\%$, покрытие активных минут $< 25\%$.
2. **Сводка покрытия (`coverage`):**
   - Анализ доли активных минут (`active_minutes_share`) и отсутствующих суток (`missing_days`).
   - Если актив имеет низкую ликвидность, часть часовых баров будет заполнена методом `ffill` (цена стоит), что отражается в метрике `minutes_active`.

---

## 6. Векторная оценка vs симуляция на движке

Для эффективной работы рекомендуется двухступенчатая схема:

1. **Быстрая оценка ([`fast_pnl`](file:///Users/kyle/main/py_files/backtest_strat/src/research/evaluate.py)):**
   - Вычисляет PnL по формуле: $\text{PnL}_t = \text{notional} \times (\text{pos}_t \times (e^{\text{ret}_t} - 1) - |\Delta \text{pos}_t| \times \text{cost})$.
   - Разворот позиции с $+1$ на $-1$ дает $2$ единицы оборота (списание комиссии с обеих сторон).
   - Используется для оптимизации параметров, плацебо-тестов (1000 перестановок) и перебора сетки признаков.
2. **Движок симулятора ([`run_schedule`](file:///Users/kyle/main/py_files/backtest_strat/src/research/evaluate.py)):**
   - Проверяет итоговую стратегию через [`ScheduledPosition`](file:///Users/kyle/main/py_files/backtest_strat/src/backtest/schedule.py) и [`BacktestEngine`](file:///Users/kyle/main/py_files/backtest_strat/src/backtest/engine.py).
   - Учитывает округление до шага лота (`lot_size`), задержку отправки ордера (`order_latency_ms`), комиссии биржи (`taker_fee`) и округление цен до тика.
   - На ликвидных инструментах расхождение между `fast_pnl` и `run_schedule` пренебрежимо мало ($< 0.1$ б.п.).
