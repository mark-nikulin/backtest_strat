from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import httpx
import numpy as np
import pandas as pd
import pytest

from src.external.weather import _fetch, load_city, load_daily


def _fake_api_response(dates_list, temp_values):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = {
        "daily": {
            "time": [d.isoformat() if isinstance(d, date) else d for d in dates_list],
            "temperature_2m_mean": temp_values,
        }
    }
    return resp


def test_weather_load_daily_lag_days(tmp_path: Path, monkeypatch):
    # Patch load_city's default cache_dir to tmp_path so it doesn't read existing cache
    from src.external import weather
    monkeypatch.setattr(weather.load_city, "__defaults__", (weather.VARIABLES, tmp_path))

    # API provides dates: 2024-01-01 to 2024-01-04
    dates = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    temps = [10.0, 12.0, 15.0, 18.0]
    fake_resp = _fake_api_response(dates, temps)

    with patch("httpx.get", return_value=fake_resp):
        df = load_daily(
            date(2024, 1, 2),
            date(2024, 1, 3),
            cities=["london"],
            lag_days=1,
        )

    # In row 2024-01-02, value should be from 2024-01-01 (10.0)
    assert df.loc[pd.Timestamp("2024-01-02"), "london__temperature_2m_mean"] == pytest.approx(10.0)
    # In row 2024-01-03, value should be from 2024-01-02 (12.0)
    assert df.loc[pd.Timestamp("2024-01-03"), "london__temperature_2m_mean"] == pytest.approx(12.0)


def test_weather_cache_incremental(tmp_path: Path):
    d1 = date(2024, 1, 1)
    d2 = date(2024, 1, 2)
    d3 = date(2024, 1, 3)

    resp1 = _fake_api_response([d1, d2], [10.0, 11.0])
    resp2 = _fake_api_response([d3], [12.0])

    call_params = []

    def mock_get(url, params=None, **kwargs):
        call_params.append(dict(params or {}))
        if len(call_params) == 1:
            return resp1
        return resp2

    with patch("httpx.get", side_effect=mock_get):
        # 1. Fetch [d1, d2]
        df1 = load_city("tokyo", d1, d2, cache_dir=tmp_path)
        assert len(df1) == 2
        assert call_params[0]["start_date"] == d1.isoformat()
        assert call_params[0]["end_date"] == d2.isoformat()

        # Check cached parquet exists on disk
        cache_file = tmp_path / "tokyo.parquet"
        assert cache_file.exists()

        # 2. Fetch [d1, d3]: should only request missing d3
        df2 = load_city("tokyo", d1, d3, cache_dir=tmp_path)
        assert len(df2) == 3
        assert len(call_params) == 2
        assert call_params[1]["start_date"] == d3.isoformat()
        assert call_params[1]["end_date"] == d3.isoformat()

        # Cache file preserved old dates and added d3
        cached = pd.read_parquet(cache_file)
        assert len(cached) == 3
        assert list(cached["temperature_2m_mean"]) == [10.0, 11.0, 12.0]


def test_weather_retry_on_429_and_5xx(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *args: None)

    r_429 = MagicMock(spec=httpx.Response, status_code=429)
    r_503 = MagicMock(spec=httpx.Response, status_code=503)
    r_200 = _fake_api_response([date(2024, 1, 1)], [15.0])

    responses = [r_429, r_503, r_200]

    with patch("httpx.get", side_effect=lambda *a, **k: responses.pop(0)):
        res = _fetch(40.0, -74.0, date(2024, 1, 1), date(2024, 1, 1), ["temperature_2m_mean"], retries=5)
        assert len(res) == 1
        assert res["temperature_2m_mean"].iloc[0] == 15.0

    # If all attempts fail, raises RuntimeError
    with patch("httpx.get", return_value=r_503):
        with pytest.raises(RuntimeError, match="Open-Meteo недоступен после"):
            _fetch(40.0, -74.0, date(2024, 1, 1), date(2024, 1, 1), ["temperature_2m_mean"], retries=3)
