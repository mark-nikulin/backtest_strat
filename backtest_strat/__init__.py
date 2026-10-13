"""
backtest_strat — рыночные данные, event-driven бэктест и инструменты исследований.

Основные точки входа (импортируйте нужное из подпакетов, пакет верхнего уровня намеренно легкий):

  backtest_strat.backtest     BacktestEngine, Strategy, Context, InstrumentSpec, ScheduledPosition, bars_to_events, summarize
  backtest_strat.pipeline     PipelineParams, run_pipeline — скачать → нормализовать → проверить → построить датасеты
  backtest_strat.storage      reader.load_derived / load_events / iter_events
  backtest_strat.research     bars, evaluate, stats, market — утилиты исследований
  backtest_strat.fetchers     get_fetcher — загрузчики бирж (OKX)
  backtest_strat.validation   validate_symbol — проверки качества
  backtest_strat.derived      derive_symbol — L1 и бары
  backtest_strat.external     weather — внешние ряды с задержкой «точка во времени»

Пути данных по умолчанию заданы в backtest_strat.paths и переопределяются переменными окружения PIPELINE_*.
"""
__version__ = "0.1.0"
