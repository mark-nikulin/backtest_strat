from typing import Callable, Dict, List, Tuple
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "network: marks tests as requiring real network access (deselect with '-m \"not network\"')"
    )
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (benchmark / reference tests)"
    )


@pytest.fixture(autouse=True)
def isolate_logging(tmp_path, monkeypatch):
    """Изолирует запись логов тестов во временную директорию (ISSUE-P6)."""
    import logging
    import backtest_strat.utils.logging_setup as ls

    log_dir = tmp_path / "test_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PIPELINE_LOG_DIR", str(log_dir))
    monkeypatch.setattr(ls, "LOG_DIR", log_dir)

    for h in logging.getLogger().handlers:
        if isinstance(h, ls.DailyFileHandler):
            monkeypatch.setattr(h, "log_dir", log_dir)
            if h._stream:
                h._stream.close()
                h._stream = None
            h._day = None


class MockHttpServer:
    """Динамический локальный HTTP-сервер на aiohttp.web."""

    def __init__(self):
        self.app = web.Application()
        self.server: TestServer = None
        self.handlers: Dict[Tuple[str, str], Callable] = {}
        self.request_history: List[web.Request] = []

        # Catch-all маршрут регистрируется ДО старта сервера, поэтому роутер не блокируется
        self.app.router.add_route("*", "/{tail:.*}", self._dispatch)

    async def _dispatch(self, request: web.Request):
        self.request_history.append(request)
        path = "/" + request.match_info.get("tail", "").lstrip("/")
        key = (request.method, path)
        if key in self.handlers:
            return await self.handlers[key](request)
        # Default 404
        return web.Response(status=404, text=f"Not registered in MockServer: {key}")

    async def start(self):
        self.server = TestServer(self.app)
        await self.server.start_server()
        return self

    async def close(self):
        if self.server:
            await self.server.close()

    def url_for(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return str(self.server.make_url(path))

    def add_handler(self, method: str, path: str, handler: Callable):
        if not path.startswith("/"):
            path = "/" + path
        self.handlers[(method, path)] = handler

    def add_get(self, path: str, handler: Callable):
        self.add_handler("GET", path, handler)

    def add_get_bytes(self, path: str, content: bytes, status: int = 200, headers: Dict[str, str] = None):
        h = headers or {}

        async def _h(request: web.Request):
            return web.Response(body=content, status=status, headers=h)

        self.add_get(path, _h)

    def add_get_status(self, path: str, status: int, headers: Dict[str, str] = None):
        h = headers or {}

        async def _h(request: web.Request):
            return web.Response(status=status, headers=h)

        self.add_get(path, _h)


@pytest_asyncio.fixture(loop_scope="function")
async def mock_server():
    server = MockHttpServer()
    await server.start()
    try:
        yield server
    finally:
        await server.close()
