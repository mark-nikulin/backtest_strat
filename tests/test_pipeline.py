from datetime import date
from unittest.mock import MagicMock, patch
import pytest

from src.fetchers.base import BackfillReport, DownloadResult, DownloadStatus
from src.normalizers.base import NormalizeReport, StepResult, StepStatus
from src.pipeline import PipelineParams, PipelineResult, run_pipeline, run_pipeline_async


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

    with patch("src.pipeline.get_fetcher", return_value=mock_fetcher) as pf, \
         patch("src.pipeline.get_normalizer", return_value=mock_normalizer) as pn:
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

    with patch("src.pipeline.get_fetcher", return_value=mock_fetcher):
        result = run_pipeline(params)

    assert not result.ok
    d = result.to_dict()
    assert d["ok"] is False
    assert len(d["failed"]) == 1
    assert d["failed"][0]["target_date"] == "2024-01-01"
    assert d["failed"][0]["error"] == "Network error"
