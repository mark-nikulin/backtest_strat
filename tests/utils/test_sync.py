import asyncio
import pytest

from src.utils.sync import run_sync


async def _sample_coro(val: int) -> int:
    await asyncio.sleep(0.001)
    return val * 2


async def _failing_coro() -> None:
    await asyncio.sleep(0.001)
    raise ValueError("intentional error")


def test_run_sync_from_normal_code():
    """run_sync вызывается из обычного синхронного кода без запущенного event loop."""
    result = run_sync(_sample_coro(21))
    assert result == 42

    with pytest.raises(ValueError, match="intentional error"):
        run_sync(_failing_coro())


def test_run_sync_from_running_event_loop():
    """run_sync вызывается из контекста с уже запущенным event loop (аналог Jupyter)."""
    async def _in_loop():
        res = run_sync(_sample_coro(10))
        assert res == 20

        with pytest.raises(ValueError, match="intentional error"):
            run_sync(_failing_coro())

    asyncio.run(_in_loop())
