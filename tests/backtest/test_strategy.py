import pytest
from src.backtest.engine import BacktestEngine
from src.backtest.strategy import Context, Strategy
from src.backtest.types import BUY, SELL, InstrumentSpec


@pytest.fixture
def engine():
    spec = InstrumentSpec(
        symbol="BTC-USDT",
        tick_size=0.1,
        lot_size=0.01,
        contract_multiplier=1.0,
    )
    return BacktestEngine(Strategy(), spec)


def test_context_validation_and_lot_rounding(engine):
    ctx = engine.ctx

    # 1. Округление к лоту: 0.015 -> 0.01
    assert ctx._round_lot(0.015) == 0.01
    assert ctx._round_lot(0.029) == 0.02

    # 2. amount < лота -> ValueError
    with pytest.raises(ValueError, match="amount меньше лота"):
        ctx.limit(BUY, 100.0, 0.005)

    # 3. Неверный side -> ValueError
    with pytest.raises(ValueError, match="side должен быть BUY или SELL"):
        ctx.limit(0, 100.0, 0.1)

    # 4. Неверный tif -> ValueError
    with pytest.raises(ValueError, match="tif:"):
        ctx.limit(BUY, 100.0, 0.1, tif="INVALID")


def test_context_market_order_and_cancels(engine):
    ctx = engine.ctx
    # Заполним книгу
    engine.book.apply_message(True, [BUY, SELL], [100.0, 101.0], [5.0, 5.0], 1_000_000, engine._on_fill)

    # market BUY отправляет агрессивный IOC
    oid = ctx.market(BUY, 0.05)
    assert oid > 0
    assert engine.orders_sent == 1

    # limit GTC
    oid_l1 = ctx.limit(BUY, 99.0, 0.1)
    oid_l2 = ctx.limit(BUY, 98.0, 0.1)
    assert len(ctx.open_orders()) >= 2

    # cancel_all
    ctx.cancel_all()
    # Задержка отмены: сейчас отмены в pending_cancels
    assert len(engine.book._pending_cancels) >= 2


def test_context_set_timer(engine):
    ctx = engine.ctx
    with pytest.raises(ValueError, match="interval_ns > 0"):
        ctx.set_timer(-100)

    ctx.set_timer(1_000_000_000)
    assert engine._timer_interval == 1_000_000_000
