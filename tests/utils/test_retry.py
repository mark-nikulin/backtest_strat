import asyncio
from unittest.mock import AsyncMock, patch
import pytest

from src.utils.retry import (
    NotFoundError,
    RetryableError,
    compute_backoff,
    retry_async,
)


@pytest.mark.asyncio
async def test_retry_async_success_first_try():
    func = AsyncMock(return_value="ok")
    result = await retry_async(func, max_attempts=3, base_delay=0.01)
    assert result == "ok"
    assert func.await_count == 1


@pytest.mark.asyncio
async def test_retry_async_succeeds_after_retries():
    func = AsyncMock(side_effect=[RetryableError("500"), RetryableError("502"), "recovered"])
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await retry_async(func, max_attempts=4, base_delay=0.01)
        assert result == "recovered"
        assert func.await_count == 3
        assert mock_sleep.await_count == 2


@pytest.mark.asyncio
async def test_retry_async_respects_retry_after():
    func = AsyncMock(side_effect=[RetryableError("429", retry_after=2.5), "done"])
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await retry_async(func, max_attempts=3, base_delay=0.01)
        assert result == "done"
        assert func.await_count == 2
        mock_sleep.assert_awaited_once_with(2.5)


@pytest.mark.asyncio
async def test_retry_async_exhausts_attempts_raises():
    func = AsyncMock(side_effect=RetryableError("503 Service Unavailable"))
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(RetryableError, match="503 Service Unavailable"):
            await retry_async(func, max_attempts=3, base_delay=0.01)
        assert func.await_count == 3
        assert mock_sleep.await_count == 2


@pytest.mark.asyncio
async def test_retry_async_not_found_raises_without_retry():
    func = AsyncMock(side_effect=NotFoundError("404"))
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(NotFoundError, match="404"):
            await retry_async(func, max_attempts=5, base_delay=0.01)
        assert func.await_count == 1
        assert mock_sleep.await_count == 0


@pytest.mark.asyncio
async def test_retry_async_other_exception_raises_immediately():
    func = AsyncMock(side_effect=ValueError("bad data"))
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(ValueError, match="bad data"):
            await retry_async(func, max_attempts=5, base_delay=0.01)
        assert func.await_count == 1
        assert mock_sleep.await_count == 0


def test_compute_backoff_bounds():
    for attempt in range(5):
        val = compute_backoff(attempt, base_delay=1.0, max_delay=10.0)
        assert 0.0 <= val <= min(10.0, 1.0 * (2 ** attempt))
