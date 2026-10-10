from datetime import date
from pathlib import Path
from unittest.mock import patch
import pytest

from src.fetchers import FETCHERS, OkxFetcher, get_fetcher
from src.fetchers.base import DownloadStatus


def test_registry_get_fetcher():
    fetcher = get_fetcher("okx")
    assert isinstance(fetcher, OkxFetcher)
    assert fetcher.exchange_name == "okx"

    fetcher_upper = get_fetcher("OKX")
    assert isinstance(fetcher_upper, OkxFetcher)

    with pytest.raises(ValueError, match="Неизвестная биржа 'binance'"):
        get_fetcher("binance")


def test_okx_archive_filenames():
    fetcher = OkxFetcher()
    d = date(2024, 1, 1)
    assert fetcher.archive_filename("BTC-USDT", d, "trades") == "BTC-USDT-trades-2024-01-01.zip"
    assert (
        fetcher.archive_filename("BTC-USDT-SWAP", d, "orderbook")
        == "BTC-USDT-SWAP-L2orderbook-400lv-2024-01-01.tar.gz"
    )


def test_okx_target_path():
    fetcher = OkxFetcher(base_dir="test_data")
    d = date(2024, 1, 1)
    expected = Path("test_data/okx/BTC-USDT/trades/BTC-USDT-trades-2024-01-01.zip")
    assert fetcher.get_target_path("BTC-USDT", d, "trades") == expected


def test_okx_candidate_urls():
    fetcher = OkxFetcher()
    d = date(2024, 1, 1)

    # Trades -> single candidate
    trade_urls = fetcher.candidate_urls("BTC-USDT", d, "trades")
    assert len(trade_urls) == 1
    assert trade_urls[0] == "https://static.okx.com/cdn/okex/traderecords/trades/daily/20240101/BTC-USDT-trades-2024-01-01.zip"

    # Orderbook -> pro candidate first, classic candidate second
    ob_urls = fetcher.candidate_urls("BTC-USDT", d, "orderbook")
    assert len(ob_urls) == 2
    assert "match/orderbook/pro/L2/400lv/daily/20240101" in ob_urls[0]
    assert "match/orderbook/L2/400lv/daily/20240101" in ob_urls[1]


@pytest.mark.asyncio
async def test_okx_download_daily_mock_server(tmp_path: Path, mock_server):
    fetcher = OkxFetcher(base_dir=str(tmp_path))
    target_d = date(2024, 1, 1)

    mock_server.add_get_bytes(
        "/mock_trades.zip",
        b"okx_trades_mock",
        status=200,
        headers={"Content-Length": "15"},
    )
    mock_url = mock_server.url_for("/mock_trades.zip")

    with patch.object(fetcher, "candidate_urls", return_value=[mock_url]):
        report = await fetcher.download_daily("BTC-USDT", "trades", target_date=target_d)

    assert report.ok
    assert len(report.results) == 1
    res = report.results[0]
    assert res.status == DownloadStatus.DOWNLOADED
    assert res.path.exists()
    assert res.path.read_bytes() == b"okx_trades_mock"


@pytest.mark.network
@pytest.mark.asyncio
async def test_okx_real_network_download_trades(tmp_path: Path):
    """
    Интеграционный тест: реальный запрос к CDN OKX.
    Запускается только при явном указании флага (по умолчанию deselect с помощью '-m \"not network\"').
    """
    fetcher = OkxFetcher(base_dir=str(tmp_path))
    target_d = date(2024, 1, 1)

    report = await fetcher.download_daily("BTC-USDT", "trades", target_date=target_d)
    assert report.ok
    assert len(report.results) == 1
    res = report.results[0]
    assert res.status == DownloadStatus.DOWNLOADED
    assert res.path.exists()
    assert res.path.stat().st_size > 1_000_000  # реальный zip ~1.8 MB


@pytest.mark.asyncio
async def test_okx_list_symbols_success(mock_server):
    import json
    from aiohttp import web

    payload = {
        "code": "0",
        "data": [
            {"instId": "ETH-USDT", "state": "live"},
            {"instId": "BTC-USDT", "state": "live"},
            {"instId": "OLD-USDT", "state": "suspend"},
        ],
    }

    async def instruments_handler(request: web.Request):
        assert request.query.get("instType") == "SPOT"
        return web.json_response(payload)

    mock_server.add_get("/api/v5/public/instruments", instruments_handler)

    fetcher = OkxFetcher()
    with patch("src.fetchers.okx.REST", mock_server.url_for("/api/v5")):
        symbols = await fetcher.list_symbols("SPOT")

    assert symbols == ["BTC-USDT", "ETH-USDT"]


@pytest.mark.asyncio
async def test_okx_list_symbols_invalid_type():
    fetcher = OkxFetcher()
    with pytest.raises(ValueError, match="inst_type: допустимо"):
        await fetcher.list_symbols("INVALID_TYPE")
