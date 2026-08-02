"""
Tests for BinanceFuturesMarketDataProvider.fetch_ticker_price, using
httpx.MockTransport.
"""

import httpx
import pytest

from app.data.binance_market_data_provider import BinanceFuturesMarketDataProvider

pytestmark = pytest.mark.asyncio

BASE_URL = "https://fapi.binance.com"


def _provider(handler) -> BinanceFuturesMarketDataProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url=BASE_URL)
    return BinanceFuturesMarketDataProvider(base_url=BASE_URL, request_timeout_seconds=10.0, client=client)


class TestFetchTickerPrice:
    async def test_successful_fetch(self):
        def handler(request):
            assert request.url.params["symbol"] == "BTCUSDT"
            return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "63045.12", "time": 0})

        provider = _provider(handler)
        price = await provider.fetch_ticker_price("BTC-USDT")
        assert price == 63045.12

    async def test_correct_endpoint(self):
        captured = {}

        def handler(request):
            captured["path"] = request.url.path
            return httpx.Response(200, json={"symbol": "ETHUSDT", "price": "3000.0"})

        provider = _provider(handler)
        await provider.fetch_ticker_price("ETH-USDT")
        assert captured["path"] == "/fapi/v1/ticker/price"

    async def test_invalid_symbol_returns_none(self):
        provider = _provider(lambda r: httpx.Response(200, json={}))
        assert await provider.fetch_ticker_price("not-a-symbol") is None

    async def test_http_error_returns_none(self):
        provider = _provider(lambda r: httpx.Response(400, json={"code": -1121}))
        assert await provider.fetch_ticker_price("BTC-USDT") is None

    async def test_network_error_returns_none(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        provider = _provider(handler)
        assert await provider.fetch_ticker_price("BTC-USDT") is None

    async def test_malformed_json_returns_none(self):
        provider = _provider(lambda r: httpx.Response(200, content=b"not json"))
        assert await provider.fetch_ticker_price("BTC-USDT") is None

    async def test_missing_price_field_returns_none(self):
        provider = _provider(lambda r: httpx.Response(200, json={"symbol": "BTCUSDT"}))
        assert await provider.fetch_ticker_price("BTC-USDT") is None

    async def test_malformed_price_returns_none(self):
        provider = _provider(lambda r: httpx.Response(200, json={"symbol": "BTCUSDT", "price": "abc"}))
        assert await provider.fetch_ticker_price("BTC-USDT") is None
