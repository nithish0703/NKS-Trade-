"""
Tests for BybitMarketDataProvider.fetch_open_interest_history, using
httpx.MockTransport.
"""

import httpx
import pytest

from app.data.bybit_market_data_provider import BybitMarketDataProvider

pytestmark = pytest.mark.asyncio

BASE_URL = "https://api.bybit.com"


def _provider(handler) -> BybitMarketDataProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url=BASE_URL)
    return BybitMarketDataProvider(base_url=BASE_URL, request_timeout_seconds=10.0, client=client)


def _oi_body(entries: list[dict], ret_code: int = 0) -> dict:
    return {
        "retCode": ret_code,
        "retMsg": "OK",
        "result": {"category": "linear", "symbol": "BTCUSDT", "list": entries},
        "retExtInfo": {},
        "time": 0,
    }


class TestFetchOpenInterestHistory:
    async def test_successful_fetch_parses_points_ascending(self):
        # Bybit returns newest-first; the provider must return ascending.
        def handler(request):
            return httpx.Response(
                200,
                json=_oi_body(
                    [
                        {"openInterest": "5100.5", "timestamp": "1735693200000"},
                        {"openInterest": "5000.0", "timestamp": "1735689600000"},
                    ]
                ),
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
            return httpx.Response(200, json=_oi_body([]))

        provider = _provider(handler)
        await provider.fetch_open_interest_history("ETH-USDT", "15min", 50)
        assert captured["path"] == "/v5/market/open-interest"
        assert captured["params"]["category"] == "linear"
        assert captured["params"]["symbol"] == "ETHUSDT"
        assert captured["params"]["intervalTime"] == "15min"
        assert captured["params"]["limit"] == "50"

    async def test_invalid_interval_returns_empty_list_without_request(self):
        called = {"count": 0}

        def handler(request):
            called["count"] += 1
            return httpx.Response(200, json=_oi_body([]))

        provider = _provider(handler)
        points = await provider.fetch_open_interest_history("BTC-USDT", "not-an-interval", 10)
        assert points == []
        assert called["count"] == 0

    async def test_non_positive_limit_returns_empty_list(self):
        provider = _provider(lambda r: httpx.Response(200, json=_oi_body([])))
        assert await provider.fetch_open_interest_history("BTC-USDT", "1h", 0) == []
        assert await provider.fetch_open_interest_history("BTC-USDT", "1h", -5) == []

    async def test_limit_above_maximum_returns_empty_list(self):
        provider = _provider(lambda r: httpx.Response(200, json=_oi_body([])))
        points = await provider.fetch_open_interest_history("BTC-USDT", "1h", 100_000)
        assert points == []

    async def test_http_error_returns_empty_list(self):
        def handler(request):
            return httpx.Response(500)

        provider = _provider(handler)
        points = await provider.fetch_open_interest_history("BTC-USDT", "1h", 10)
        assert points == []

    async def test_network_error_returns_empty_list(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        provider = _provider(handler)
        points = await provider.fetch_open_interest_history("BTC-USDT", "1h", 10)
        assert points == []

    async def test_malformed_json_returns_empty_list(self):
        def handler(request):
            return httpx.Response(200, content=b"not json")

        provider = _provider(handler)
        points = await provider.fetch_open_interest_history("BTC-USDT", "1h", 10)
        assert points == []

    async def test_error_ret_code_returns_empty_list(self):
        def handler(request):
            return httpx.Response(200, json=_oi_body([], ret_code=170121))

        provider = _provider(handler)
        points = await provider.fetch_open_interest_history("BTC-USDT", "1h", 10)
        assert points == []

    async def test_missing_open_interest_field_row_skipped(self):
        def handler(request):
            return httpx.Response(
                200,
                json=_oi_body(
                    [
                        {"timestamp": "1735689600000"},
                        {"openInterest": "5000.0", "timestamp": "1735693200000"},
                    ]
                ),
            )

        provider = _provider(handler)
        points = await provider.fetch_open_interest_history("BTC-USDT", "1h", 10)
        assert len(points) == 1
        assert points[0].open_interest == 5000.0

    async def test_malformed_timestamp_row_skipped(self):
        def handler(request):
            return httpx.Response(
                200,
                json=_oi_body(
                    [
                        {"openInterest": "5000.0", "timestamp": "not-a-timestamp"},
                        {"openInterest": "5100.0", "timestamp": "1735693200000"},
                    ]
                ),
            )

        provider = _provider(handler)
        points = await provider.fetch_open_interest_history("BTC-USDT", "1h", 10)
        assert len(points) == 1
        assert points[0].open_interest == 5100.0

    async def test_invalid_symbol_returns_empty_list(self):
        provider = _provider(lambda r: httpx.Response(200, json=_oi_body([])))
        points = await provider.fetch_open_interest_history("not-a-symbol", "1h", 10)
        assert points == []

    async def test_no_api_keys_used(self):
        captured = {}

        def handler(request):
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, json=_oi_body([]))

        provider = _provider(handler)
        await provider.fetch_open_interest_history("BTC-USDT", "1h", 10)
        header_keys = {key.lower() for key in captured["headers"]}
        assert "x-bapi-api-key" not in header_keys
        assert "x-bapi-sign" not in header_keys
