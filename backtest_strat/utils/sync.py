"""Запуск корутин из синхронного кода, в том числе из Jupyter (там цикл событий уже запущен)."""
import asyncio
import threading
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")


def run_sync(coro: Coroutine[Any, Any, T]) -> T:
    """asyncio.run, который работает и внутри уже запущенного цикла (ноутбуки): тогда корутина идет в отдельном потоке."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    box: dict = {}

    def _runner() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001 — пробрасываем в вызывающий поток
            box["error"] = e

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box["value"]
