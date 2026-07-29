"""
Tests for OKXMarketDataProvider.fetch_ticker_price, using httpx.MockTransport.
"""

import httpx
import pytest

from app.data.market_data_provider import OKXMarketDataProvider

pytestmark = pytest.mark.asyncio


def _provider(handler) -> OKXMarketDataProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://www.okx.com")
    return OKXMarketDataProvider(
        base_url="https://www.okx.com", request_timeout_seconds=10.0, client=client
    )


class TestFetchTickerPrice:
    async def test_successful_fetch(self):
        def handler(request):
            return httpx.Response(
                200, json={"code": "0", "data": [{"instId": "BTC-USDT", "last": "67250.5"}]}
            )

        provider = _provider(handler)
        price = await provider.fetch_ticker_price("BTC-USDT")
        assert price == 67250.5

    async def test_correct_endpoint_and_params(self):
        captured = {}

        def handler(request):
            captured["path"] = request.url.path
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json={"code": "0", "data": [{"last": "100.0"}]})

        provider = _provider(handler)
        await provider.fetch_ticker_price("ETH-USDT")
        assert captured["path"] == "/api/v5/market/ticker"
        assert captured["params"]["instId"] == "ETH-USDT"

    async def test_http_error_returns_none(self):
        def handler(request):
            return httpx.Response(500)

        provider = _provider(handler)
        price = await provider.fetch_ticker_price("BTC-USDT")
        assert price is None

    async def test_network_error_returns_none(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        provider = _provider(handler)
        price = await provider.fetch_ticker_price("BTC-USDT")
        assert price is None

    async def test_malformed_json_returns_none(self):
        def handler(request):
            return httpx.Response(200, content=b"not json")

        provider = _provider(handler)
        price = await provider.fetch_ticker_price("BTC-USDT")
        assert price is None

    async def test_error_code_returns_none(self):
        def handler(request):
            return httpx.Response(200, json={"code": "51001", "msg": "Instrument ID does not exist"})

        provider = _provider(handler)
        price = await provider.fetch_ticker_price("BTC-USDT")
        assert price is None

    async def test_empty_data_returns_none(self):
        def handler(request):
            return httpx.Response(200, json={"code": "0", "data": []})

        provider = _provider(handler)
        price = await provider.fetch_ticker_price("BTC-USDT")
        assert price is None

    async def test_missing_last_field_returns_none(self):
        def handler(request):
            return httpx.Response(200, json={"code": "0", "data": [{"instId": "BTC-USDT"}]})

        provider = _provider(handler)
        price = await provider.fetch_ticker_price("BTC-USDT")
        assert price is None

    async def test_invalid_symbol_returns_none(self):
        def handler(request):
            return httpx.Response(200, json={"code": "0", "data": [{"last": "100.0"}]})

        provider = _provider(handler)
        price = await provider.fetch_ticker_price("not-a-symbol")
        assert price is None

    async def test_no_api_keys_used(self):
        captured = {}

        def handler(request):
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, json={"code": "0", "data": [{"last": "100.0"}]})

        provider = _provider(handler)
        await provider.fetch_ticker_price("BTC-USDT")
        header_keys = {key.lower() for key in captured["headers"]}
        assert "ok-access-key" not in header_keys
        assert "ok-access-sign" not in header_keys
        assert "ok-access-passphrase" not in header_keys
