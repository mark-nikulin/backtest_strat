from datetime import date
from unittest.mock import MagicMock, patch
import pytest

from backtest_strat.fetchers.base import BackfillReport, DownloadResult, DownloadStatus
from backtest_strat.normalizers.base import NormalizeReport, StepResult, StepStatus
from backtest_strat.pipeline import PipelineParams, PipelineResult, run_pipeline, run_pipeline_async


def test_pipeline_params_validation():
    # Valid params
    p = PipelineParams(
        exchange="okx",
        symbols=["BTC-USDT"],
        mode="backfill",
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
    )
    p.validate()

    # Invalid mode
    with pytest.raises(ValueError, match="mode должен быть одним из"):
        PipelineParams(exchange="okx", symbols=["BTC-USDT"], mode="invalid").validate()

    # Invalid stage
    with pytest.raises(ValueError, match="stage должен быть одним из"):
        PipelineParams(exchange="okx", symbols=["BTC-USDT"], stage="invalid").validate()

    # Empty symbols
    with pytest.raises(ValueError, match="Нужен хотя бы один symbol"):
        PipelineParams(exchange="okx", symbols=[]).validate()

    # Bad data types
    with pytest.raises(ValueError, match="data_types: допустимо"):
        PipelineParams(exchange="okx", symbols=["BTC-USDT"], data_types=["invalid"]).validate()

    with pytest.raises(ValueError, match="data_types: допустимо"):
        PipelineParams(exchange="okx", symbols=["BTC-USDT"], data_types=[]).validate()

    # Backfill missing dates
    with pytest.raises(ValueError, match="Для backfill нужны start и end"):
        PipelineParams(exchange="okx", symbols=["BTC-USDT"], mode="backfill", start=None, end=date(2024, 1, 1)).validate()

    with pytest.raises(ValueError, match="Для backfill нужны start и end"):
        PipelineParams(exchange="okx", symbols=["BTC-USDT"], mode="backfill", start=date(2024, 1, 1), end=None).validate()

    # End < start
    with pytest.raises(ValueError, match="end раньше start"):
        PipelineParams(
            exchange="okx",
            symbols=["BTC-USDT"],
            mode="backfill",
            start=date(2024, 1, 5),
            end=date(2024, 1, 1),
        ).validate()

    # max_concurrent < 1
    with pytest.raises(ValueError, match="max_concurrent >= 1"):
        PipelineParams(
            exchange="okx",
            symbols=["BTC-USDT"],
            mode="daily",
            max_concurrent=0,
        ).validate()


def test_pipeline_date_bounds():
    p_backfill = PipelineParams(
        exchange="okx",
        symbols=["BTC-USDT"],
        mode="backfill",
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
    )
    assert p_backfill.date_bounds() == (date(2024, 1, 1), date(2024, 1, 2))

    p_daily_custom = PipelineParams(
        exchange="okx",
        symbols=["BTC-USDT"],
        mode="daily",
        end=date(2024, 1, 5),
    )
    assert p_daily_custom.date_bounds() == (date(2024, 1, 5), date(2024, 1, 5))


@pytest.mark.asyncio
async def test_run_pipeline_stages_and_progress():
    params = PipelineParams(
        exchange="okx",
        symbols=["BTC-USDT"],
        mode="backfill",
        start=date(2024, 1, 1),
        end=date(2024, 1, 1),
        data_types=["trades"],
        stage="all",
    )

    mock_fetcher = MagicMock()
    mock_ingest_report = BackfillReport("BTC-USDT", "trades", [
        DownloadResult(date(2024, 1, 1), DownloadStatus.DOWNLOADED)
    ])

    async def fake_download(*args, on_partial=None, **kwargs):
        if on_partial:
            on_partial(0.5)
            on_partial(1.0)
        return mock_ingest_report

    mock_fetcher.download_historical_range = fake_download

    mock_normalizer = MagicMock()
    mock_norm_report = NormalizeReport("BTC-USDT", [
        StepResult("trades:file.zip", StepStatus.PROCESSED)
    ])

    def fake_norm_run(*args, **kwargs):
        cb = args[4] if len(args) > 4 else kwargs.get("on_progress")
        if cb:
            cb(1, 1)
        return mock_norm_report

    mock_normalizer.run = fake_norm_run

    progress_history = []

    def progress_cb(frac: float, text: str):
        progress_history.append((frac, text))

    with patch("backtest_strat.pipeline.get_fetcher", return_value=mock_fetcher) as pf, \
         patch("backtest_strat.pipeline.get_normalizer", return_value=mock_normalizer) as pn:
        result = await run_pipeline_async(params, on_progress=progress_cb)

    assert result.ok
    assert len(result.ingest) == 1
    assert len(result.normalize) == 1
    assert pf.called
    assert pn.called

    assert len(progress_history) > 0
    # Final progress is 1.0 with "Готово"
    assert progress_history[-1] == (1.0, "Готово")

    # Check summary and dict
    lines = result.summary_lines()
    assert any("ingest" in line for line in lines)
    assert any("normalize" in line for line in lines)
    d = result.to_dict()
    assert d["ok"] is True
    assert d["failed"] == []


def test_run_pipeline_reports_failure():
    params = PipelineParams(
        exchange="okx",
        symbols=["BTC-USDT"],
        mode="backfill",
        start=date(2024, 1, 1),
        end=date(2024, 1, 1),
        data_types=["trades"],
        stage="ingest",
    )

    mock_fetcher = MagicMock()
    mock_ingest_report = BackfillReport("BTC-USDT", "trades", [
        DownloadResult(date(2024, 1, 1), DownloadStatus.FAILED, error="Network error")
    ])

    async def fake_download(*args, **kwargs):
        return mock_ingest_report

    mock_fetcher.download_historical_range = fake_download

    with patch("backtest_strat.pipeline.get_fetcher", return_value=mock_fetcher):
        result = run_pipeline(params)

    assert not result.ok
    d = result.to_dict()
    assert d["ok"] is False
    assert len(d["failed"]) == 1
    assert d["failed"][0]["target_date"] == "2024-01-01"
    assert d["failed"][0]["error"] == "Network error"


def test_run_pipeline_stage_validate_only():
    params = PipelineParams(
        exchange="okx",
        symbols=["BTC-USDT"],
        mode="backfill",
        start=date(2024, 1, 1),
        end=date(2024, 1, 1),
        stage="validate",
    )

    from backtest_strat.validation import ValidationSummary

    fake_summary = ValidationSummary(
        symbol="BTC-USDT",
        days=[{
            "day": "2024-01-01",
            "status": "OK",
            "events": 100,
            "book_messages": 50,
            "trades": 50,
            "snapshots": 1,
            "checks": [{"name": "crossed_book", "status": "OK", "detail": "Перевернутой книги нет"}],
            "stats": {},
        }],
        missing_days=[],
    )

    with patch("backtest_strat.pipeline.get_fetcher") as mock_fetcher, \
         patch("backtest_strat.pipeline.get_normalizer") as mock_normalizer, \
         patch("backtest_strat.pipeline.validate_symbol", return_value=fake_summary) as mock_val:
        result = run_pipeline(params)

    assert not mock_fetcher.called
    assert not mock_normalizer.called
    assert mock_val.called
    assert result.ok is True
    assert len(result.ingest) == 0
    assert len(result.normalize) == 0
    assert len(result.validate) == 1
    assert result.validate[0].symbol == "BTC-USDT"


@pytest.mark.asyncio
async def test_run_pipeline_stage_all_runs_validation_after_normalize():
    params = PipelineParams(
        exchange="okx",
        symbols=["BTC-USDT"],
        mode="backfill",
        start=date(2024, 1, 1),
        end=date(2024, 1, 1),
        data_types=["trades"],
        stage="all",
    )

    from backtest_strat.validation import ValidationSummary

    mock_fetcher = MagicMock()
    mock_ingest_report = BackfillReport("BTC-USDT", "trades", [
        DownloadResult(date(2024, 1, 1), DownloadStatus.DOWNLOADED)
    ])

    async def fake_download(*args, **kwargs):
        return mock_ingest_report

    mock_fetcher.download_historical_range = fake_download

    mock_normalizer = MagicMock()
    mock_norm_report = NormalizeReport("BTC-USDT", [
        StepResult("trades:file.zip", StepStatus.PROCESSED)
    ])
    mock_normalizer.run.return_value = mock_norm_report

    mock_summary = ValidationSummary(
        symbol="BTC-USDT",
        days=[{"day": "2024-01-01", "status": "OK", "checks": []}],
        missing_days=[],
    )

    from backtest_strat.derived import DeriveReport, DeriveStep
    mock_derive = DeriveReport("BTC-USDT", [DeriveStep("2024-01-01", "processed")])

    call_order = []

    mock_fetcher.download_historical_range = MagicMock(side_effect=lambda *a, **k: fake_download(*a, **k))
    def norm_run(*args, **kwargs):
        call_order.append("normalize")
        return mock_norm_report
    mock_normalizer.run = norm_run

    def val_run(*args, **kwargs):
        call_order.append("validate")
        return mock_summary

    def derive_run(*args, **kwargs):
        call_order.append("derive")
        return mock_derive

    with patch("backtest_strat.pipeline.get_fetcher", return_value=mock_fetcher), \
         patch("backtest_strat.pipeline.get_normalizer", return_value=mock_normalizer), \
         patch("backtest_strat.pipeline.validate_symbol", side_effect=val_run), \
         patch("backtest_strat.pipeline.derive_symbol", side_effect=derive_run):
        result = await run_pipeline_async(params)

    assert result.ok is True
    assert len(result.ingest) == 1
    assert len(result.normalize) == 1
    assert len(result.validate) == 1
    assert len(result.derive) == 1
    assert call_order == ["normalize", "validate", "derive"]


def test_run_pipeline_stage_derive_only():
    params = PipelineParams(
        exchange="okx",
        symbols=["BTC-USDT"],
        mode="backfill",
        start=date(2024, 1, 1),
        end=date(2024, 1, 1),
        stage="derive",
    )

    from backtest_strat.derived import DeriveReport, DeriveStep
    fake_derive = DeriveReport("BTC-USDT", [DeriveStep("2024-01-01", "processed")])

    with patch("backtest_strat.pipeline.get_fetcher") as mock_fetcher, \
         patch("backtest_strat.pipeline.get_normalizer") as mock_norm, \
         patch("backtest_strat.pipeline.validate_symbol") as mock_val, \
         patch("backtest_strat.pipeline.derive_symbol", return_value=fake_derive) as mock_der:
        result = run_pipeline(params)

    assert not mock_fetcher.called
    assert not mock_norm.called
    assert not mock_val.called
    assert mock_der.called
    assert result.ok is True
    assert len(result.ingest) == 0
    assert len(result.normalize) == 0
    assert len(result.validate) == 0
    assert len(result.derive) == 1
    assert result.derive[0].symbol == "BTC-USDT"


def test_run_pipeline_derive_failure_marks_result_failed():
    params = PipelineParams(
        exchange="okx",
        symbols=["BTC-USDT"],
        mode="backfill",
        start=date(2024, 1, 1),
        end=date(2024, 1, 1),
        stage="derive",
    )

    from backtest_strat.derived import DeriveReport, DeriveStep
    fake_derive = DeriveReport("BTC-USDT", [DeriveStep("2024-01-01", "failed", error="ParquetWriteError")])

    with patch("backtest_strat.pipeline.derive_symbol", return_value=fake_derive):
        result = run_pipeline(params)

    assert result.ok is False
    d = result.to_dict()
    assert d["ok"] is False
    assert any(x.get("day") == "2024-01-01" and "ParquetWriteError" in x.get("error", "") for x in d["failed"])


def test_run_pipeline_validation_failure_marks_result_failed():
    params = PipelineParams(
        exchange="okx",
        symbols=["BTC-USDT"],
        mode="backfill",
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
        stage="validate",
    )

    from backtest_strat.validation import ValidationSummary

    fake_summary = ValidationSummary(
        symbol="BTC-USDT",
        days=[
            {
                "day": "2024-01-01",
                "status": "FAIL",
                "checks": [
                    {"name": "crossed_book", "status": "FAIL", "detail": "1 сообщений с bid >= ask"},
                ],
            }
        ],
        missing_days=["2024-01-02"],
    )

    with patch("backtest_strat.pipeline.validate_symbol", return_value=fake_summary):
        result = run_pipeline(params)

    assert result.ok is False
    d = result.to_dict()
    assert d["ok"] is False
    failed_items = d["failed"]
    assert any(item.get("day") == "2024-01-01" and "bid >= ask" in item.get("error", "") for item in failed_items)
    assert any(item.get("day") == "2024-01-02" and "нет событий" in item.get("error", "") for item in failed_items)
