from backtest_strat.backtest.engine import BacktestResult
from backtest_strat.backtest.portfolio import Portfolio
from backtest_strat.backtest.report import format_summary, summarize
from backtest_strat.backtest.types import BUY, Fill, InstrumentSpec


def test_summarize_order_fill_ratio_and_max_dd():
    spec = InstrumentSpec(symbol="BTC-USDT", tick_size=0.1, lot_size=0.01)
    p = Portfolio(multiplier=1.0)

    # 1 ордер получил 2 исполнения (fill 1 и fill 2 для order_id=10)
    p.apply(Fill(1, 10, BUY, 100.0, 1.0, 1_000, is_maker=False, fee=0.1))
    p.apply(Fill(2, 10, BUY, 101.0, 1.0, 2_000, is_maker=False, fee=0.1))

    # Известная кривая equity: 0 -> 100 -> 60 -> 120 -> 50
    # Пик 100: dd = 100 - 60 = 40
    # Пик 120: dd = 120 - 50 = 70 (max dd = 70)
    p.equity_curve = [
        (1_000_000_000, 0.0),
        (2_000_000_000, 100.0),
        (3_000_000_000, 60.0),
        (4_000_000_000, 120.0),
        (5_000_000_000, 50.0),
    ]

    res = BacktestResult(
        spec=spec,
        portfolio=p,
        start_ts=1_000_000_000,
        end_ts=5_000_000_000,
        events=100,
        book_messages=80,
        trades=20,
        orders_sent=4,  # Всего отправлено 4 ордера
        wall_seconds=0.5,
        final_mid=100.0,
    )

    stats = summarize(res)

    # Исполнен ровно 1 уникальный ордер из 4 отправленных -> ratio = 1 / 4 = 0.25 (не 2 / 4!)
    assert stats["order_fill_ratio"] == 0.25
    assert stats["fills"] == 2
    assert stats["orders_sent"] == 4
    assert stats["max_drawdown"] == 70.0
    assert stats["events_per_sec"] == 200.0

    # Проверка строкового форматирования
    formatted = format_summary(stats)
    assert "total_pnl" in formatted
    assert "max_drawdown" in formatted
    assert "order_fill_ratio" in formatted
