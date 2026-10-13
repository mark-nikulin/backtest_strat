import pytest
from backtest_strat.backtest.portfolio import Portfolio
from backtest_strat.backtest.types import BUY, SELL, Fill, next_fill_id


def test_portfolio_accumulation_and_average_price():
    p = Portfolio(multiplier=1.0)

    # 1. Первая покупка: 1.0 по 100.0
    f1 = Fill(next_fill_id(), 1, BUY, 100.0, 1.0, 1_000, is_maker=True, fee=0.02)
    p.apply(f1)
    assert p.position == 1.0
    assert p.avg_price == 100.0
    assert p.realized_pnl == 0.0
    assert p.fees == 0.02
    assert p.volume == 1.0
    assert p.notional == 100.0
    assert p.maker_fills == 1
    assert p.taker_fills == 0

    # 2. Вторая покупка: 1.0 по 110.0 -> средняя 105.0
    f2 = Fill(next_fill_id(), 2, BUY, 110.0, 1.0, 2_000, is_maker=False, fee=0.05)
    p.apply(f2)
    assert p.position == 2.0
    assert p.avg_price == 105.0
    assert p.fees == 0.07
    assert p.taker_fills == 1


def test_portfolio_partial_close_and_realized_pnl():
    p = Portfolio(multiplier=1.0)
    # Открыли 2.0 по 100.0
    p.apply(Fill(next_fill_id(), 1, BUY, 100.0, 2.0, 1_000, is_maker=True, fee=0.0))

    # Частично закрыли: продали 1.0 по 120.0
    # Прибыль: (120 - 100) * 1.0 = +20.0
    p.apply(Fill(next_fill_id(), 2, SELL, 120.0, 1.0, 2_000, is_maker=False, fee=1.0))
    assert p.position == 1.0
    assert p.avg_price == 100.0  # средняя цена оставшейся позиции не изменилась
    assert p.realized_pnl == 20.0
    assert p.fees == 1.0

    # Проверка unrealized и equity при mark=115.0
    # unrealized = (115 - 100) * 1.0 = 15.0
    # equity = 20.0 + 15.0 - 1.0 = 34.0
    assert p.unrealized_pnl(115.0) == 15.0
    assert p.equity(115.0) == 34.0


def test_portfolio_reversal_and_flat():
    p = Portfolio(multiplier=1.0)
    # Купили 1.0 по 100.0
    p.apply(Fill(next_fill_id(), 1, BUY, 100.0, 1.0, 1_000, is_maker=True, fee=0.0))

    # Разворот: продали 2.5 по 110.0
    # Закрыли 1.0 лонг: pnl = (110 - 100) * 1.0 = +10.0
    # Открыли 1.5 шорт по цене сделки 110.0
    p.apply(Fill(next_fill_id(), 2, SELL, 110.0, 2.5, 2_000, is_maker=False, fee=0.0))
    assert p.position == -1.5
    assert p.avg_price == 110.0
    assert p.realized_pnl == 10.0

    # Закрытие в ноль (flat): купили 1.5 по 105.0
    # Прибыль по шорту: (110 - 105) * 1.5 = +7.5
    p.apply(Fill(next_fill_id(), 3, BUY, 105.0, 1.5, 3_000, is_maker=True, fee=0.0))
    assert p.position == 0.0
    assert p.avg_price == 0.0
    assert p.realized_pnl == 17.5
    assert p.unrealized_pnl(120.0) == 0.0
    assert p.equity(120.0) == 17.5
