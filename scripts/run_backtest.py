#!/usr/bin/env python3
"""Совместимая обертка: теперь это консольная команда `bt-backtest` (backtest_strat.backtest.cli)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest_strat.backtest.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
