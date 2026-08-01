"""
Tests for BinanceFuturesMarketDataProvider.fetch_open_interest_history,
using httpx.MockTransport.
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


class TestFetchOpenInterestHistory:
    async def test_successful_fetch_parses_points_ascending(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "BTCUSDT",
                        "sumOpenInterestValue": "5000.0",
                        "timestamp": "1735689600000",
                    },
                    {
                        "symbol": "BTCUSDT",
                        "sumOpenInterestValue": "5100.5",
                        "timestamp": "1735693200000",
                    },
                ],
            )

        provider = _provider(handler)
        points = await provider.fetch_open_interest_history("BTC-USDT", "1h", 2)
        assert len(points) == 2
        assert points[0].open_interest == 5000.0
        assert points[1].open_interest == 5100.5
        assert points[0].timestamp < points[1].timestamp

    async def test_correct_endpoint_and_params(self):
        captured = {}

        def handler(request):
            captured["path"] = request.url.path
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json=[])

        provider = _provider(handler)
        await provider.fetch_open_interest_history("ETH-USDT", "15m", 50)
        assert captured["path"] == "/futures/data/openInterestHist"
        assert captured["params"]["symbol"] == "ETHUSDT"
        assert captured["params"]["period"] == "15m"
        assert captured["params"]["limit"] == "50"

    async def test_invalid_interval_returns_empty_list_without_request(self):
        called = {"count": 0}

        def handler(request):
            called["count"] += 1
            return httpx.Response(200, json=[])

        provider = _provider(handler)
        points = await provider.fetch_open_interest_history("BTC-USDT", "not-an-interval", 10)
        assert points == []
        assert called["count"] == 0

    async def test_non_positive_limit_returns_empty_list(self):
        provider = _provider(lambda r: httpx.Response(200, json=[]))
        assert await provider.fetch_open_interest_history("BTC-USDT", "1h", 0) == []
        assert await provider.fetch_open_interest_history("BTC-USDT", "1h", -5) == []

    async def test_limit_above_maximum_returns_empty_list(self):
        provider = _provider(lambda r: httpx.Response(200, json=[]))
        points = await provider.fetch_open_interest_history("BTC-USDT", "1h", 100_000)
        assert points == []

    async def test_http_error_returns_empty_list(self):
        provider = _provider(lambda r: httpx.Response(500))
        points = await provider.fetch_open_interest_history("BTC-USDT", "1h", 10)
        assert points == []

    async def test_network_error_returns_empty_list(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        provider = _provider(handler)
        points = await provider.fetch_open_interest_history("BTC-USDT", "1h", 10)
        assert points == []

    async def test_malformed_json_returns_empty_list(self):
        provider = _provider(lambda r: httpx.Response(200, content=b"not json"))
        points = await provider.fetch_open_interest_history("BTC-USDT", "1h", 10)
        assert points == []

    async def test_non_list_payload_returns_empty_list(self):
        provider = _provider(lambda r: httpx.Response(200, json={"not": "a list"}))
        points = await provider.fetch_open_interest_history("BTC-USDT", "1h", 10)
        assert points == []

    async def test_missing_open_interest_field_row_skipped(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[
                    {"symbol": "BTCUSDT", "timestamp": "1735689600000"},
                    {
                        "symbol": "BTCUSDT",
                        "sumOpenInterestValue": "5000.0",
                        "timestamp": "1735693200000",
                    },
                ],
            )

        provider = _provider(handler)
        points = await provider.fetch_open_interest_history("BTC-USDT", "1h", 10)
        assert len(points) == 1
        assert points[0].open_interest == 5000.0

    async def test_malformed_timestamp_row_skipped(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "BTCUSDT",
                        "sumOpenInterestValue": "5000.0",
                        "timestamp": "not-a-timestamp",
                    },
                    {
                        "symbol": "BTCUSDT",
                        "sumOpenInterestValue": "5100.0",
                        "timestamp": "1735693200000",
                    },
                ],
            )

        provider = _provider(handler)
        points = await provider.fetch_open_interest_history("BTC-USDT", "1h", 10)
        assert len(points) == 1
        assert points[0].open_interest == 5100.0

    async def test_invalid_symbol_returns_empty_list(self):
        provider = _provider(lambda r: httpx.Response(200, json=[]))
        points = await provider.fetch_open_interest_history("not-a-symbol", "1h", 10)
        assert points == []

    async def test_no_api_keys_used(self):
        captured = {}

        def handler(request):
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, json=[])

        provider = _provider(handler)
        await provider.fetch_open_interest_history("BTC-USDT", "1h", 10)
        header_keys = {key.lower() for key in captured["headers"]}
        assert "x-mbx-apikey" not in header_keys
