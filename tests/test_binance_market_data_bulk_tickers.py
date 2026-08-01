"""
Tests for BinanceFuturesMarketDataProvider.fetch_all_linear_tickers,
covering the two-stage fetch: bulk 24hr turnover first, then per-symbol
Open Interest only for turnover-qualifying candidates, combined into an
approximate USDT Open Interest value. Uses httpx.MockTransport.
"""

import httpx
import pytest

from app.data.binance_market_data_provider import BinanceFuturesMarketDataProvider

pytestmark = pytest.mark.asyncio

BASE_URL = "https://fapi.binance.com"


def _ticker_row(symbol: str, *, quote_volume: str, last_price: str) -> dict:
    return {"symbol": symbol, "quoteVolume": quote_volume, "lastPrice": last_price}


def _provider(handler) -> BinanceFuturesMarketDataProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url=BASE_URL)
    return BinanceFuturesMarketDataProvider(base_url=BASE_URL, request_timeout_seconds=10.0, client=client)


class TestFetchAllLinearTickers:
    async def test_bulk_endpoint_called_once_then_oi_per_symbol(self):
        calls = {"ticker_24hr": 0, "open_interest": []}

        def handler(request):
            if request.url.path == "/fapi/v1/ticker/24hr":
                calls["ticker_24hr"] += 1
                return httpx.Response(
                    200,
                    json=[
                        _ticker_row("BTCUSDT", quote_volume="20000000", last_price="60000.0"),
                        _ticker_row("ETHUSDT", quote_volume="15000000", last_price="3000.0"),
                    ],
                )
            if request.url.path == "/fapi/v1/openInterest":
                symbol = request.url.params["symbol"]
                calls["open_interest"].append(symbol)
                contracts = {"BTCUSDT": "100.0", "ETHUSDT": "500.0"}[symbol]
                return httpx.Response(200, json={"symbol": symbol, "openInterest": contracts})
            raise AssertionError(f"unexpected path {request.url.path}")

        provider = _provider(handler)
        snapshots = await provider.fetch_all_linear_tickers()

        assert calls["ticker_24hr"] == 1
        assert sorted(calls["open_interest"]) == ["BTCUSDT", "ETHUSDT"]

        by_symbol = {s.symbol: s for s in snapshots}
        assert by_symbol["BTC-USDT"].turnover_24h_usdt == 20_000_000.0
        assert by_symbol["BTC-USDT"].open_interest_usdt == 100.0 * 60000.0
        assert by_symbol["ETH-USDT"].turnover_24h_usdt == 15_000_000.0
        assert by_symbol["ETH-USDT"].open_interest_usdt == 500.0 * 3000.0

    async def test_symbol_missing_turnover_skips_open_interest_fetch(self):
        oi_calls = []

        def handler(request):
            if request.url.path == "/fapi/v1/ticker/24hr":
                return httpx.Response(
                    200,
                    json=[{"symbol": "BTCUSDT", "lastPrice": "60000.0"}],  # no quoteVolume
                )
            if request.url.path == "/fapi/v1/openInterest":
                oi_calls.append(request.url.params["symbol"])
                return httpx.Response(200, json={"openInterest": "100.0"})
            raise AssertionError

        provider = _provider(handler)
        snapshots = await provider.fetch_all_linear_tickers()
        assert snapshots == []
        assert oi_calls == []

    async def test_non_usdt_symbol_excluded(self):
        def handler(request):
            if request.url.path == "/fapi/v1/ticker/24hr":
                return httpx.Response(
                    200,
                    json=[
                        _ticker_row("BTCUSDT", quote_volume="20000000", last_price="60000.0"),
                        _ticker_row("BTCBUSD", quote_volume="5000000", last_price="60000.0"),
                    ],
                )
            return httpx.Response(200, json={"openInterest": "1.0"})

        provider = _provider(handler)
        snapshots = await provider.fetch_all_linear_tickers()
        assert [s.symbol for s in snapshots] == ["BTC-USDT"]

    async def test_open_interest_fetch_failure_leaves_it_none(self):
        def handler(request):
            if request.url.path == "/fapi/v1/ticker/24hr":
                return httpx.Response(
                    200, json=[_ticker_row("BTCUSDT", quote_volume="20000000", last_price="60000.0")]
                )
            if request.url.path == "/fapi/v1/openInterest":
                return httpx.Response(500)
            raise AssertionError

        provider = _provider(handler)
        snapshots = await provider.fetch_all_linear_tickers()
        assert len(snapshots) == 1
        assert snapshots[0].open_interest_usdt is None
        assert snapshots[0].turnover_24h_usdt == 20_000_000.0

    async def test_missing_last_price_skips_open_interest_fetch(self):
        oi_calls = []

        def handler(request):
            if request.url.path == "/fapi/v1/ticker/24hr":
                return httpx.Response(
                    200, json=[{"symbol": "BTCUSDT", "quoteVolume": "20000000"}]  # no lastPrice
                )
            if request.url.path == "/fapi/v1/openInterest":
                oi_calls.append(request.url.params["symbol"])
                return httpx.Response(200, json={"openInterest": "1.0"})
            raise AssertionError

        provider = _provider(handler)
        snapshots = await provider.fetch_all_linear_tickers()
        assert snapshots[0].open_interest_usdt is None
        assert oi_calls == []

    async def test_http_error_on_bulk_endpoint_returns_empty_list(self):
        provider = _provider(lambda r: httpx.Response(500))
        assert await provider.fetch_all_linear_tickers() == []

    async def test_network_error_on_bulk_endpoint_returns_empty_list(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        provider = _provider(handler)
        assert await provider.fetch_all_linear_tickers() == []

    async def test_malformed_json_returns_empty_list(self):
        provider = _provider(lambda r: httpx.Response(200, content=b"not json"))
        assert await provider.fetch_all_linear_tickers() == []

    async def test_non_list_payload_returns_empty_list(self):
        provider = _provider(lambda r: httpx.Response(200, json={"not": "a list"}))
        assert await provider.fetch_all_linear_tickers() == []

    async def test_malformed_row_skipped(self):
        def handler(request):
            if request.url.path == "/fapi/v1/ticker/24hr":
                return httpx.Response(
                    200,
                    json=[
                        "not-a-dict",
                        _ticker_row("BTCUSDT", quote_volume="20000000", last_price="60000.0"),
                    ],
                )
            return httpx.Response(200, json={"openInterest": "1.0"})

        provider = _provider(handler)
        snapshots = await provider.fetch_all_linear_tickers()
        assert [s.symbol for s in snapshots] == ["BTC-USDT"]

    async def test_no_api_keys_used(self):
        captured = {}

        def handler(request):
            if request.url.path == "/fapi/v1/ticker/24hr":
                captured["headers"] = dict(request.headers)
                return httpx.Response(
                    200, json=[_ticker_row("BTCUSDT", quote_volume="20000000", last_price="60000.0")]
                )
            return httpx.Response(200, json={"openInterest": "1.0"})

        provider = _provider(handler)
        await provider.fetch_all_linear_tickers()
        header_keys = {key.lower() for key in captured["headers"]}
        assert "x-mbx-apikey" not in header_keys
