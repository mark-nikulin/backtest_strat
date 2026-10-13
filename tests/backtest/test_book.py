import pytest
from backtest_strat.backtest.book import ReplayBook
from backtest_strat.backtest.types import BUY, SELL, InstrumentSpec, Order, Fill, next_order_id


@pytest.fixture
def spec():
    return InstrumentSpec(
        symbol="BTC-USDT",
        tick_size=0.1,
        lot_size=0.001,
        contract_multiplier=1.0,
        maker_fee=0.0002,   # 2 bps
        taker_fee=0.0005,   # 5 bps
        feed_latency_ns=1_000_000,
        order_latency_ns=2_000_000,
    )


# --- Инварианты из hft_sim ---

def test_replay_l2_replacement_and_delete(spec):
    book = ReplayBook(spec)
    fills = []

    # 1. Начальный снимок: bid 100.0 (2.0), ask 101.0 (3.0)
    book.apply_message(
        is_snapshot=True,
        sides=[BUY, SELL],
        prices=[100.0, 101.0],
        sizes=[2.0, 3.0],
        ts=1_000_000,
        on_fill=fills.append,
    )
    assert book.best_bid() == 100.0
    assert book.best_ask() == 101.0
    assert book.volume_at(BUY, 100.0) == 2.0
    assert book.volume_at(SELL, 101.0) == 3.0
    assert book.mid() == 100.5
    assert book.spread() == 1.0

    # 2. Обновление объема уровня 100.0 до 5.0
    book.apply_message(
        is_snapshot=False,
        sides=[BUY],
        prices=[100.0],
        sizes=[5.0],
        ts=2_000_000,
        on_fill=fills.append,
    )
    assert book.volume_at(BUY, 100.0) == 5.0
    assert book.best_bid() == 100.0

    # 3. Удаление уровня 100.0 (size = 0.0)
    book.apply_message(
        is_snapshot=False,
        sides=[BUY],
        prices=[100.0],
        sizes=[0.0],
        ts=3_000_000,
        on_fill=fills.append,
    )
    assert book.best_bid() is None
    assert book.volume_at(BUY, 100.0) == 0.0
    assert not book.is_valid()


def test_replay_aggressive_fill_does_not_mutate_recorded_depth(spec):
    book = ReplayBook(spec)
    fills = []

    # Стакан: ask 101.0 на 5.0 лотов
    book.apply_message(
        is_snapshot=True,
        sides=[BUY, SELL],
        prices=[100.0, 101.0],
        sizes=[5.0, 5.0],
        ts=1_000_000,
        on_fill=fills.append,
    )

    # Агрессивный ордер BUY на 2.0 лота по 101.0
    order = Order(
        order_id=next_order_id(),
        side=BUY,
        price_ticks=spec.to_ticks(101.0),
        amount=2.0,
        remaining=2.0,
        placed_ts=1_000_000,
        active_ts=1_000_000,
        tif="IOC",
    )
    book.submit(order, now=1_000_000, on_fill=fills.append)

    # Ордер исполнился
    assert len(fills) == 1
    assert fills[0].amount == 2.0
    assert fills[0].price == 101.0
    assert not fills[0].is_maker
    assert order.remaining == 0.0

    # Но записанная глубина рынка НЕ изменилась: объем все еще 5.0
    assert book.volume_at(SELL, 101.0) == 5.0


def test_replay_passive_fill_waits_for_recorded_queue(spec):
    book = ReplayBook(spec)
    fills = []

    # В стакане на уровне bid 100.0 есть 3.0 лота
    book.apply_message(
        is_snapshot=True,
        sides=[BUY, SELL],
        prices=[100.0, 102.0],
        sizes=[3.0, 2.0],
        ts=1_000_000,
        on_fill=fills.append,
    )

    # Встаем пассивно в очередь на 100.0 за 3.0 лотами
    order = Order(
        order_id=next_order_id(),
        side=BUY,
        price_ticks=spec.to_ticks(100.0),
        amount=1.0,
        remaining=1.0,
        placed_ts=1_000_000,
        active_ts=1_000_000,
        tif="GTC",
    )
    book.submit(order, now=1_000_000, on_fill=fills.append)
    assert book.queue_ahead(order.order_id) == 3.0

    # 1. Записанная сделка SELL (агрессор продает в bid) на 2.0 по 100.0:
    # Очередь съедается до 1.0, нас не задело
    book.on_trade(aggressor_side=SELL, price=100.0, amount=2.0, now=2_000_000, on_fill=fills.append)
    assert len(fills) == 0
    assert book.queue_ahead(order.order_id) == 1.0
    assert order.remaining == 1.0

    # 2. Еще одна сделка SELL на 1.5 по 100.0:
    # 1.0 доедает очередь перед нами, оставшиеся 0.5 исполняют половину нашего ордера
    book.on_trade(aggressor_side=SELL, price=100.0, amount=1.5, now=3_000_000, on_fill=fills.append)
    assert len(fills) == 1
    assert fills[0].amount == 0.5
    assert fills[0].is_maker
    assert book.queue_ahead(order.order_id) == 0.0
    assert order.remaining == 0.5

    # 3. Финальная сделка на 0.5:
    book.on_trade(aggressor_side=SELL, price=100.0, amount=0.5, now=4_000_000, on_fill=fills.append)
    assert len(fills) == 2
    assert fills[1].amount == 0.5
    assert order.remaining == 0.0


# --- Дополнительные тесты ReplayBook и логики TIF ---

def test_depth_and_best_levels_fallback(spec):
    book = ReplayBook(spec)
    book.apply_message(
        is_snapshot=True,
        sides=[BUY, BUY, SELL, SELL],
        prices=[100.0, 99.0, 101.0, 102.0],
        sizes=[1.0, 2.0, 1.5, 2.5],
        ts=1_000_000,
        on_fill=lambda f: None,
    )
    bids, asks = book.depth(levels=2)
    assert bids == [(100.0, 1.0), (99.0, 2.0)]
    assert asks == [(101.0, 1.5), (102.0, 2.5)]

    # Удаляем лучший bid 100.0 -> best_bid откатывается на 99.0
    book.apply_message(
        is_snapshot=False,
        sides=[BUY],
        prices=[100.0],
        sizes=[0.0],
        ts=2_000_000,
        on_fill=lambda f: None,
    )
    assert book.best_bid() == 99.0

    # Удаляем лучший ask 101.0 -> best_ask откатывается на 102.0
    book.apply_message(
        is_snapshot=False,
        sides=[SELL],
        prices=[101.0],
        sizes=[0.0],
        ts=3_000_000,
        on_fill=lambda f: None,
    )
    assert book.best_ask() == 102.0


def test_tif_rules(spec):
    book = ReplayBook(spec)
    fills = []
    book.apply_message(
        is_snapshot=True,
        sides=[BUY, SELL],
        prices=[100.0, 101.0],
        sizes=[2.0, 2.0],
        ts=1_000_000,
        on_fill=fills.append,
    )

    # 1. PostOnly через спред: BUY по 101.0 -> отклоняется без исполнения
    o_post = Order(next_order_id(), BUY, spec.to_ticks(101.0), 1.0, 1.0, 1_000_000, 1_000_000, tif="PostOnly")
    book.submit(o_post, now=1_000_000, on_fill=fills.append)
    assert len(fills) == 0
    assert len(book.open_orders()) == 0

    # 2. FOK при нехватке ликвидности: BUY 5.0 по 101.0 (в стакане только 2.0) -> ничего
    o_fok = Order(next_order_id(), BUY, spec.to_ticks(101.0), 5.0, 5.0, 1_000_000, 1_000_000, tif="FOK")
    book.submit(o_fok, now=1_000_000, on_fill=fills.append)
    assert len(fills) == 0
    assert len(book.open_orders()) == 0

    # 3. IOC: BUY 3.0 по 101.0 -> забирает доступные 2.0, остаток 1.0 не встает в книгу
    o_ioc = Order(next_order_id(), BUY, spec.to_ticks(101.0), 3.0, 3.0, 1_000_000, 1_000_000, tif="IOC")
    book.submit(o_ioc, now=1_000_000, on_fill=fills.append)
    assert len(fills) == 1
    assert fills[0].amount == 2.0
    assert len(book.open_orders()) == 0

    # 4. GTC: пассивный BUY 1.0 по 99.0 встает в книгу
    o_gtc = Order(next_order_id(), BUY, spec.to_ticks(99.0), 1.0, 1.0, 1_000_000, 1_000_000, tif="GTC")
    book.submit(o_gtc, now=1_000_000, on_fill=fills.append)
    assert len(book.open_orders()) == 1


def test_consumed_liquidity_and_restore(spec):
    book = ReplayBook(spec)
    fills = []
    # Стакан: ask 101.0 с объемом 2.0
    book.apply_message(True, [BUY, SELL], [100.0, 101.0], [5.0, 2.0], 1_000_000, fills.append)

    # Первый агрессивный ордер забирает 1.5
    o1 = Order(next_order_id(), BUY, spec.to_ticks(101.0), 1.5, 1.5, 1_000_000, 1_000_000, tif="IOC")
    book.submit(o1, 1_000_000, fills.append)
    assert len(fills) == 1
    assert fills[0].amount == 1.5

    # Второй агрессивный ордер пытается взять 1.0 -> берет только остаток 0.5
    o2 = Order(next_order_id(), BUY, spec.to_ticks(101.0), 1.0, 1.0, 1_000_000, 1_000_000, tif="IOC")
    book.submit(o2, 1_000_000, fills.append)
    assert len(fills) == 2
    assert fills[1].amount == 0.5

    # Третий агрессивный ордер не может забрать ничего (уровень исчерпан)
    o3 = Order(next_order_id(), BUY, spec.to_ticks(101.0), 1.0, 1.0, 1_000_000, 1_000_000, tif="IOC")
    book.submit(o3, 1_000_000, fills.append)
    assert len(fills) == 2

    # Апдейт уровня восстанавливает объем
    book.apply_message(False, [SELL], [101.0], [3.0], 2_000_000, fills.append)
    o4 = Order(next_order_id(), BUY, spec.to_ticks(101.0), 2.0, 2.0, 2_000_000, 2_000_000, tif="IOC")
    book.submit(o4, 2_000_000, fills.append)
    assert len(fills) == 3
    assert fills[2].amount == 2.0


def test_queue_ahead_reduction_and_trade_through(spec):
    book = ReplayBook(spec)
    fills = []
    book.apply_message(True, [BUY, SELL], [100.0, 102.0], [10.0, 5.0], 1_000_000, fills.append)

    # Наш ордер BUY по 100.0: очередь изначально 10.0
    o = Order(next_order_id(), BUY, spec.to_ticks(100.0), 1.0, 1.0, 1_000_000, 1_000_000, tif="GTC")
    book.submit(o, 1_000_000, fills.append)
    assert book.queue_ahead(o.order_id) == 10.0

    # Апдейт стакана вниз: объем уровня упал до 4.0 -> очередь режется до 4.0
    book.apply_message(False, [BUY], [100.0], [4.0], 2_000_000, fills.append)
    assert book.queue_ahead(o.order_id) == 4.0

    # Сделка по цене хуже нашей (trade-through: SELL по 99.0): исполняет нас без очереди
    book.on_trade(aggressor_side=SELL, price=99.0, amount=1.0, now=3_000_000, on_fill=fills.append)
    assert len(fills) == 1
    assert fills[0].amount == 1.0
    assert fills[0].price == 100.0
    assert fills[0].is_maker


def test_crossed_resting_order_fill(spec):
    book = ReplayBook(spec)
    fills = []
    book.apply_message(True, [BUY, SELL], [100.0, 102.0], [2.0, 2.0], 1_000_000, fills.append)

    # Наш пассивный ордер BUY по 100.0
    o = Order(next_order_id(), BUY, spec.to_ticks(100.0), 1.0, 1.0, 1_000_000, 1_000_000, tif="GTC")
    book.submit(o, 1_000_000, fills.append)

    # Стакан сдвинулся: ask стал 100.0 на 2.0 (книга прошла сквозь наш ордер)
    book.apply_message(False, [SELL], [100.0], [2.0], 2_000_000, fills.append)
    assert len(fills) == 1
    assert fills[0].is_maker
    assert fills[0].price == 100.0
    assert fills[0].amount == 1.0
    assert o.remaining == 0.0


def test_latency_and_cancellation_and_expiry(spec):
    book = ReplayBook(spec)
    fills = []
    book.apply_message(True, [BUY, SELL], [100.0, 101.0], [2.0, 2.0], 1_000_000, fills.append)

    # Ордер с задержкой активации (active_ts = 2_000_000)
    o = Order(
        next_order_id(), BUY, spec.to_ticks(101.0), 1.0, 1.0,
        placed_ts=1_000_000, active_ts=2_000_000, tif="IOC", expiry_ts=3_000_000
    )
    book.submit(o, now=1_000_000, on_fill=fills.append)
    # До active_ts ордер в pending, не исполняется
    assert len(fills) == 0

    # Запрос на отмену в 2_500_000
    book.request_cancel(o.order_id, effective_ts=2_500_000)

    # В 1_500_000 ничего не происходит
    book.process_due(1_500_000, fills.append)
    assert len(fills) == 0

    # В 2_000_000 наступает active_ts -> ордер активируется и исполняется
    book.process_due(2_000_000, fills.append)
    assert len(fills) == 1
    assert fills[0].amount == 1.0
