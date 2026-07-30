"""
Tests for BybitMarketDataProvider.fetch_ticker_price, using httpx.MockTransport.
"""

import httpx
import pytest

from app.data.bybit_market_data_provider import BybitMarketDataProvider

pytestmark = pytest.mark.asyncio

BASE_URL = "https://api.bybit.com"


def _provider(handler) -> BybitMarketDataProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url=BASE_URL)
    return BybitMarketDataProvider(
        base_url=BASE_URL, request_timeout_seconds=10.0, client=client
    )


def _ticker_body(entries: list[dict], ret_code: int = 0) -> dict:
    return {
        "retCode": ret_code,
        "retMsg": "OK",
        "result": {"category": "linear", "list": entries},
        "retExtInfo": {},
        "time": 0,
    }


class TestFetchTickerPrice:
    async def test_successful_fetch(self):
        def handler(request):
            return httpx.Response(
                200, json=_ticker_body([{"symbol": "BTCUSDT", "lastPrice": "67250.5"}])
            )

        provider = _provider(handler)
        price = await provider.fetch_ticker_price("BTC-USDT")
        assert price == 67250.5

    async def test_correct_endpoint_and_params(self):
        captured = {}

        def handler(request):
            captured["path"] = request.url.path
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json=_ticker_body([{"lastPrice": "100.0"}]))

        provider = _provider(handler)
        await provider.fetch_ticker_price("ETH-USDT")
        assert captured["path"] == "/v5/market/tickers"
        assert captured["params"]["symbol"] == "ETHUSDT"
        assert captured["params"]["category"] == "linear"

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

    async def test_error_ret_code_returns_none(self):
        def handler(request):
            return httpx.Response(200, json=_ticker_body([], ret_code=170121))

        provider = _provider(handler)
        price = await provider.fetch_ticker_price("BTC-USDT")
        assert price is None

    async def test_empty_list_returns_none(self):
        def handler(request):
            return httpx.Response(200, json=_ticker_body([]))

        provider = _provider(handler)
        price = await provider.fetch_ticker_price("BTC-USDT")
        assert price is None

    async def test_missing_last_price_field_returns_none(self):
        def handler(request):
            return httpx.Response(200, json=_ticker_body([{"symbol": "BTCUSDT"}]))

        provider = _provider(handler)
        price = await provider.fetch_ticker_price("BTC-USDT")
        assert price is None

    async def test_invalid_symbol_returns_none(self):
        def handler(request):
            return httpx.Response(200, json=_ticker_body([{"lastPrice": "100.0"}]))

        provider = _provider(handler)
        price = await provider.fetch_ticker_price("not-a-symbol")
        assert price is None

    async def test_no_api_keys_used(self):
        captured = {}

        def handler(request):
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, json=_ticker_body([{"lastPrice": "100.0"}]))

        provider = _provider(handler)
        await provider.fetch_ticker_price("BTC-USDT")
        header_keys = {key.lower() for key in captured["headers"]}
        assert "x-bapi-api-key" not in header_keys
        assert "x-bapi-sign" not in header_keys
