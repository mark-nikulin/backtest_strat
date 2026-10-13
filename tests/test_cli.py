from unittest.mock import MagicMock, patch
import pytest

from backtest_strat.cli import main
from backtest_strat.pipeline import PipelineResult
from backtest_strat.fetchers.base import BackfillReport, DownloadResult, DownloadStatus


def test_cli_missing_required_args():
    # Missing --exchange and --symbol -> exits with code 2
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 2


def test_cli_invalid_date_args():
    # Invalid date format
    with pytest.raises(SystemExit) as exc_info:
        main(["--exchange", "okx", "--symbol", "BTC-USDT", "--start", "invalid-date"])
    assert exc_info.value.code == 2


def test_cli_validation_error_exits_2():
    # Backfill with end < start
    with pytest.raises(SystemExit) as exc_info:
        main([
            "--exchange", "okx",
            "--symbol", "BTC-USDT",
            "--mode", "backfill",
            "--start", "2024-01-05",
            "--end", "2024-01-01",
        ])
    assert exc_info.value.code == 2


def test_cli_success_exit_0():
    fake_result = PipelineResult()

    with patch("backtest_strat.cli.run_pipeline", return_value=fake_result) as mock_run:
        code = main([
            "--exchange", "okx",
            "--symbol", "BTC-USDT", "ETH-USDT",
            "--mode", "daily",
            "--quiet",
        ])

    assert code == 0
    assert mock_run.called
    params = mock_run.call_args[0][0]
    assert params.exchange == "okx"
    assert params.symbols == ["BTC-USDT", "ETH-USDT"]
    assert params.mode == "daily"


def test_cli_failed_pipeline_exit_1(capsys):
    failed_report = BackfillReport("BTC-USDT", "trades", [
        DownloadResult(None, DownloadStatus.FAILED, error="Download timeout")
    ])
    fake_result = PipelineResult(ingest=[failed_report])

    with patch("backtest_strat.cli.run_pipeline", return_value=fake_result):
        code = main([
            "--exchange", "okx",
            "--symbol", "BTC-USDT",
            "--mode", "daily",
            "--quiet",
        ])

    assert code == 1
    err = capsys.readouterr().err
    assert "FAILED:" in err
    assert "Download timeout" in err
