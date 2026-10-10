import asyncio
from datetime import date
from pathlib import Path
from typing import List
import aiohttp
from aiohttp import web
import pytest

from src.fetchers.base import (
    BackfillReport,
    BaseExchangeFetcher,
    DownloadResult,
    DownloadStatus,
)


class DummyFetcher(BaseExchangeFetcher):
    supported_data_types = ("trades", "book")

    def __init__(self, *args, server_base_url: str = "https://dummy.exchange", **kwargs):
        super().__init__(*args, **kwargs)
        self.server_base_url = server_base_url.rstrip("/")

    def candidate_urls(self, symbol: str, target_date: date, data_type: str) -> List[str]:
        d = target_date.isoformat()
        if data_type == "book":
            return [
                f"{self.server_base_url}/book/pro/{symbol}/{d}.tar.gz",
                f"{self.server_base_url}/book/classic/{symbol}/{d}.tar.gz",
            ]
        return [f"{self.server_base_url}/trades/{symbol}/{d}.zip"]

    def archive_filename(self, symbol: str, target_date: date, data_type: str) -> str:
        ext = "tar.gz" if data_type == "book" else "zip"
        return f"{symbol}-{data_type}-{target_date.isoformat()}.{ext}"


def test_validate_data_type():
    fetcher = DummyFetcher("dummy")
    with pytest.raises(ValueError, match="не поддерживает data_type='unknown'"):
        fetcher._validate_data_type("unknown")


@pytest.mark.asyncio
async def test_idempotence_skipped(tmp_path: Path, mock_server):
    fetcher = DummyFetcher("dummy", base_dir=str(tmp_path), server_base_url=mock_server.url_for("/"))
    target_d = date(2024, 1, 1)
    target_path = fetcher.get_target_path("BTC-USDT", target_d, "trades")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(b"existing_data")

    async with aiohttp.ClientSession() as session:
        result = await fetcher.download_daily_archive(session, "BTC-USDT", target_d, "trades")

    assert result.status == DownloadStatus.SKIPPED
    assert result.path == target_path
    # No HTTP requests were made to mock server
    assert len(mock_server.request_history) == 0


@pytest.mark.asyncio
async def test_download_success(tmp_path: Path, mock_server):
    fetcher = DummyFetcher("dummy", base_dir=str(tmp_path), server_base_url=mock_server.url_for("/"))
    target_d = date(2024, 1, 1)
    content = b"trades_binary_archive"
    path = "/trades/BTC-USDT/2024-01-01.zip"
    mock_server.add_get_bytes(path, content, status=200, headers={"Content-Length": str(len(content))})

    async with aiohttp.ClientSession() as session:
        result = await fetcher.download_daily_archive(session, "BTC-USDT", target_d, "trades")

    assert result.status == DownloadStatus.DOWNLOADED
    assert result.path.exists()
    assert result.path.read_bytes() == content


@pytest.mark.asyncio
async def test_429_retries_and_fails_after_max_attempts(tmp_path: Path, mock_server):
    fetcher = DummyFetcher(
        "dummy",
        base_dir=str(tmp_path),
        server_base_url=mock_server.url_for("/"),
        max_attempts=3,
        base_delay=0.001,
    )
    target_d = date(2024, 1, 1)
    path = "/trades/BTC-USDT/2024-01-01.zip"
    mock_server.add_get_status(path, status=429)

    async with aiohttp.ClientSession() as session:
        result = await fetcher.download_daily_archive(session, "BTC-USDT", target_d, "trades")

    assert result.status == DownloadStatus.FAILED
    assert "HTTP 429" in result.error
    assert len(mock_server.request_history) == 3
    assert not fetcher.get_target_path("BTC-USDT", target_d, "trades").exists()


@pytest.mark.asyncio
async def test_404_tries_fallback_and_reports_missing_if_both_fail(tmp_path: Path, mock_server):
    fetcher = DummyFetcher("dummy", base_dir=str(tmp_path), server_base_url=mock_server.url_for("/"))
    target_d = date(2024, 1, 1)
    # Neither pro nor classic is registered -> both return 404 from mock_server

    async with aiohttp.ClientSession() as session:
        result = await fetcher.download_daily_archive(session, "BTC-USDT", target_d, "book")

    assert result.status == DownloadStatus.MISSING
    assert len(mock_server.request_history) == 2
    assert not fetcher.get_target_path("BTC-USDT", target_d, "book").exists()


@pytest.mark.asyncio
async def test_404_tries_fallback_and_succeeds_on_second_candidate(tmp_path: Path, mock_server):
    fetcher = DummyFetcher("dummy", base_dir=str(tmp_path), server_base_url=mock_server.url_for("/"))
    target_d = date(2024, 1, 1)
    path_pro = "/book/pro/BTC-USDT/2024-01-01.tar.gz"
    path_classic = "/book/classic/BTC-USDT/2024-01-01.tar.gz"
    content = b"book_classic_content"

    mock_server.add_get_status(path_pro, status=404)
    mock_server.add_get_bytes(path_classic, content, status=200, headers={"Content-Length": str(len(content))})

    async with aiohttp.ClientSession() as session:
        result = await fetcher.download_daily_archive(session, "BTC-USDT", target_d, "book")

    assert result.status == DownloadStatus.DOWNLOADED
    assert result.path.read_bytes() == content
    assert len(mock_server.request_history) == 2


@pytest.mark.asyncio
async def test_download_historical_range_failure_does_not_abort_other_days(tmp_path: Path, mock_server):
    fetcher = DummyFetcher(
        "dummy",
        base_dir=str(tmp_path),
        server_base_url=mock_server.url_for("/"),
        max_attempts=2,
        base_delay=0.001,
    )
    start_d = date(2024, 1, 1)
    end_d = date(2024, 1, 3)

    path_day1 = "/trades/BTC-USDT/2024-01-01.zip"
    path_day2 = "/trades/BTC-USDT/2024-01-02.zip"
    path_day3 = "/trades/BTC-USDT/2024-01-03.zip"

    mock_server.add_get_bytes(path_day1, b"day1", status=200, headers={"Content-Length": "4"})
    mock_server.add_get_status(path_day2, status=500)
    mock_server.add_get_bytes(path_day3, b"day3", status=200, headers={"Content-Length": "4"})

    report = await fetcher.download_historical_range(
        "BTC-USDT", start_d, end_d, "trades", max_concurrent=2
    )

    assert len(report.results) == 3
    assert not report.ok
    assert len(report.by_status(DownloadStatus.DOWNLOADED)) == 2
    assert len(report.by_status(DownloadStatus.FAILED)) == 1

    r_day1 = report.results[0]
    r_day2 = report.results[1]
    r_day3 = report.results[2]

    assert r_day1.target_date == date(2024, 1, 1) and r_day1.status == DownloadStatus.DOWNLOADED
    assert r_day2.target_date == date(2024, 1, 2) and r_day2.status == DownloadStatus.FAILED
    assert r_day3.target_date == date(2024, 1, 3) and r_day3.status == DownloadStatus.DOWNLOADED


@pytest.mark.asyncio
async def test_on_progress_callback(tmp_path: Path, mock_server):
    fetcher = DummyFetcher("dummy", base_dir=str(tmp_path), server_base_url=mock_server.url_for("/"))
    start_d = date(2024, 1, 1)
    end_d = date(2024, 1, 3)

    progress_events = []

    def on_progress(done, total):
        progress_events.append((done, total))

    for d in [1, 2, 3]:
        path = f"/trades/BTC-USDT/2024-01-0{d}.zip"
        mock_server.add_get_bytes(path, b"data", status=200, headers={"Content-Length": "4"})

    report = await fetcher.download_historical_range(
        "BTC-USDT", start_d, end_d, "trades", max_concurrent=1, on_progress=on_progress
    )

    assert len(progress_events) == 3
    assert progress_events[-1] == (3, 3)
    assert report.ok


@pytest.mark.asyncio
async def test_on_partial_callback(tmp_path: Path, mock_server):
    fetcher = DummyFetcher("dummy", base_dir=str(tmp_path), server_base_url=mock_server.url_for("/"))
    start_d = date(2024, 1, 1)
    end_d = date(2024, 1, 2)

    partial_calls = []

    def on_partial(frac: float):
        partial_calls.append(frac)

    for d in [1, 2]:
        path = f"/trades/BTC-USDT/2024-01-0{d}.zip"
        mock_server.add_get_bytes(path, b"x" * 200, status=200, headers={"Content-Length": "200"})

    report = await fetcher.download_historical_range(
        "BTC-USDT", start_d, end_d, "trades", max_concurrent=1, on_partial=on_partial
    )

    assert report.ok
    assert len(partial_calls) > 0
    # Every value between 0.0 and 1.0
    for val in partial_calls:
        assert 0.0 <= val <= 1.0
    assert partial_calls[-1] == 1.0


@pytest.mark.asyncio
async def test_cleanup_stale_parts(tmp_path: Path):
    import os
    import time
    fetcher = DummyFetcher("dummy", base_dir=str(tmp_path))
    target_dir = tmp_path / "dummy" / "BTC-USDT" / "trades"
    target_dir.mkdir(parents=True, exist_ok=True)

    stale_part = target_dir / "old_download.12345678.part"
    stale_part.write_bytes(b"old")
    # Set mtime to 3600 seconds ago (> 30 mins)
    old_time = time.time() - 3600
    os.utime(stale_part, (old_time, old_time))

    fresh_part = target_dir / "new_download.87654321.part"
    fresh_part.write_bytes(b"fresh")

    # Run cleanup
    fetcher._cleanup_stale_parts("BTC-USDT", "trades")

    assert not stale_part.exists()
    assert fresh_part.exists()


@pytest.mark.asyncio
async def test_base_fetcher_list_symbols_default():
    fetcher = DummyFetcher("dummy")
    symbols = await fetcher.list_symbols("SPOT")
    assert symbols == []
