from pathlib import Path
from unittest.mock import MagicMock, patch
import httpx
import pytest
from streamlit.testing.v1 import AppTest


def _mock_httpx_request(method: str, url: str, **kwargs):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.headers = {"content-type": "application/json"}

    if "/meta" in url:
        resp.json.return_value = {
            "exchanges": ["okx"],
            "data_types": ["trades", "orderbook"],
            "raw_dir": "data/raw",
            "processed_dir": "data/processed",
        }
    elif "/exchanges/okx/symbols" in url:
        resp.json.return_value = {"symbols": ["BTC-USDT", "BTC-USDT-SWAP"]}
    elif "/jobs" in url:
        resp.json.return_value = [
            {
                "id": "job123",
                "status": "done",
                "progress": 1.0,
                "message": "Готово",
                "params": {
                    "exchange": "okx",
                    "symbols": ["BTC-USDT"],
                    "data_types": ["trades"],
                    "mode": "daily",
                    "start": None,
                    "end": None,
                },
                "result": {
                    "summary": ["ingest: ok", "normalize: ok"],
                    "failed": [],
                },
            }
        ]
    elif "/coverage" in url:
        resp.json.return_value = {
            "raw": {"trades": ["2024-01-01"], "orderbook": ["2024-01-01"]},
            "events": ["2024-01-01"],
        }
    elif "/schedule" in url:
        resp.json.return_value = {
            "enabled": True,
            "cron_installed": True,
            "will_run": True,
            "config": {
                "enabled": True,
                "time": "02:00",
                "symbols": ["BTC-USDT"],
                "data_types": ["trades"],
                "lookback_days": 3,
            },
        }
    elif "/logs" in url:
        resp.json.return_value = [
            {
                "time": "2024-01-01 12:00:00",
                "level": "INFO",
                "source": "cli",
                "logger": "pipeline",
                "message": "Sample log line",
            }
        ]
    elif "/validation" in url:
        resp.json.return_value = {
            "days": [
                {
                    "day": "2024-01-01",
                    "status": "WARN",
                    "events": 1000,
                    "snapshots": 10,
                    "checks": [
                        {
                            "name": "book_gap",
                            "status": "WARN",
                            "detail": "Макс. пауза 35s",
                            "value": 35.0,
                            "examples": ["12:00:00 -> 12:00:35"],
                        }
                    ],
                }
            ],
            "missing_days": ["2024-01-02"],
            "not_validated": ["2024-01-03"],
        }
    else:
        resp.json.return_value = {}

    return resp


def test_dashboard_smoke():
    import sys
    orig_main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    dashboard_path = Path(__file__).resolve().parents[2] / "src" / "ui" / "dashboard.py"
    try:
        with patch("httpx.request", side_effect=_mock_httpx_request):
            at = AppTest.from_file(dashboard_path)
            at.run()
            # Verify that no exceptions occurred during execution
            assert not at.exception
            # Verify title is displayed
            assert any("Data Pipeline" in str(title.value) for title in at.title)
    finally:
        if orig_main_file is not None and "__main__" in sys.modules:
            sys.modules["__main__"].__file__ = orig_main_file
