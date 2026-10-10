from pathlib import Path
import aiohttp
from aiohttp import web
import pytest

from src.utils.http import _parse_retry_after, download_to_file
from src.utils.retry import NotFoundError, RetryableError


def test_parse_retry_after():
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("") is None
    assert _parse_retry_after("5") == 5.0
    assert _parse_retry_after("2.5") == 2.5
    assert _parse_retry_after("-10") == 0.0
    assert _parse_retry_after("Wed, 21 Oct 2025 07:28:00 GMT") is None


@pytest.mark.asyncio
async def test_download_to_file_success(tmp_path: Path, mock_server):
    dest = tmp_path / "downloads" / "file.zip"
    content = b"PK\x03\x04test_data_content_bytes"
    mock_server.add_get_bytes("/test.zip", content, status=200, headers={"Content-Length": str(len(content))})

    url = mock_server.url_for("/test.zip")
    progress_calls = []

    def on_progress(written, total):
        progress_calls.append((written, total))

    async with aiohttp.ClientSession() as session:
        result = await download_to_file(session, url, dest, on_progress=on_progress)

    assert result == dest
    assert dest.exists()
    assert dest.read_bytes() == content
    part_file = dest.with_name(dest.name + ".part")
    assert not part_file.exists()
    assert len(progress_calls) > 0
    assert progress_calls[-1] == (len(content), len(content))


@pytest.mark.asyncio
async def test_download_to_file_404_raises_not_found(tmp_path: Path, mock_server):
    dest = tmp_path / "file.zip"
    mock_server.add_get_status("/missing.zip", status=404)
    url = mock_server.url_for("/missing.zip")

    async with aiohttp.ClientSession() as session:
        with pytest.raises(NotFoundError, match="404"):
            await download_to_file(session, url, dest)

    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


@pytest.mark.asyncio
async def test_download_to_file_429_raises_retryable_with_retry_after(tmp_path: Path, mock_server):
    dest = tmp_path / "file.zip"
    mock_server.add_get_status("/rate_limited.zip", status=429, headers={"Retry-After": "3.5"})
    url = mock_server.url_for("/rate_limited.zip")

    async with aiohttp.ClientSession() as session:
        with pytest.raises(RetryableError) as exc_info:
            await download_to_file(session, url, dest)

    assert exc_info.value.retry_after == 3.5
    assert not dest.exists()


@pytest.mark.asyncio
async def test_download_to_file_500_raises_retryable(tmp_path: Path, mock_server):
    dest = tmp_path / "file.zip"
    mock_server.add_get_status("/server_error.zip", status=500)
    url = mock_server.url_for("/server_error.zip")

    async with aiohttp.ClientSession() as session:
        with pytest.raises(RetryableError, match="HTTP 500"):
            await download_to_file(session, url, dest)

    assert not dest.exists()


@pytest.mark.asyncio
async def test_download_to_file_mismatched_content_length(tmp_path: Path, mock_server):
    dest = tmp_path / "file.zip"
    actual_content = b"short"

    # Streaming response: Content-Length: 100, но пишем 5 байт и закрываем транспорт
    async def handler(request: web.Request):
        response = web.StreamResponse(status=200, headers={"Content-Length": "100"})
        await response.prepare(request)
        await response.write(actual_content)
        if request.transport:
            request.transport.close()
        return response

    mock_server.add_get("/truncated.zip", handler)
    url = mock_server.url_for("/truncated.zip")

    async with aiohttp.ClientSession() as session:
        with pytest.raises(RetryableError):
            await download_to_file(session, url, dest)

    # Ни целевой файл, ни .part не должны остаться на диске
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


@pytest.mark.asyncio
async def test_download_to_file_other_http_error(tmp_path: Path, mock_server):
    dest = tmp_path / "file.zip"
    mock_server.add_get_status("/forbidden.zip", status=403)
    url = mock_server.url_for("/forbidden.zip")

    async with aiohttp.ClientSession() as session:
        with pytest.raises(RuntimeError, match="HTTP 403"):
            await download_to_file(session, url, dest)

    assert not dest.exists()
