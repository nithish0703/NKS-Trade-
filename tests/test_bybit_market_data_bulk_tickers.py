"""
Tests for BybitMarketDataProvider.fetch_all_linear_tickers, using
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


class TestFetchAllLinearTickers:
    async def test_successful_fetch_parses_oi_and_turnover(self):
        def handler(request):
            return httpx.Response(
                200,
                json=_ticker_body(
                    [
                        {
                            "symbol": "BTCUSDT",
                            "openInterestValue": "50000000.5",
                            "turnover24h": "120000000.25",
                        }
                    ]
                ),
            )

        provider = _provider(handler)
        tickers = await provider.fetch_all_linear_tickers()
        assert len(tickers) == 1
        assert tickers[0].symbol == "BTC-USDT"
        assert tickers[0].open_interest_usdt == 50000000.5
        assert tickers[0].turnover_24h_usdt == 120000000.25

    async def test_no_symbol_filter_used_in_request(self):
        captured = {}

        def handler(request):
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json=_ticker_body([]))

        provider = _provider(handler)
        await provider.fetch_all_linear_tickers()
        assert captured["params"]["category"] == "linear"
        assert "symbol" not in captured["params"]

    async def test_multiple_symbols_all_returned(self):
        def handler(request):
            return httpx.Response(
                200,
                json=_ticker_body(
                    [
                        {"symbol": "BTCUSDT", "openInterestValue": "1", "turnover24h": "2"},
                        {"symbol": "ETHUSDT", "openInterestValue": "3", "turnover24h": "4"},
                        {"symbol": "SOLUSDT", "openInterestValue": "5", "turnover24h": "6"},
                    ]
                ),
            )

        provider = _provider(handler)
        tickers = await provider.fetch_all_linear_tickers()
        symbols = {t.symbol for t in tickers}
        assert symbols == {"BTC-USDT", "ETH-USDT", "SOL-USDT"}

    async def test_http_error_returns_empty_list(self):
        def handler(request):
            return httpx.Response(500)

        provider = _provider(handler)
        tickers = await provider.fetch_all_linear_tickers()
        assert tickers == []

    async def test_network_error_returns_empty_list(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        provider = _provider(handler)
        tickers = await provider.fetch_all_linear_tickers()
        assert tickers == []

    async def test_malformed_json_returns_empty_list(self):
        def handler(request):
            return httpx.Response(200, content=b"not json")

        provider = _provider(handler)
        tickers = await provider.fetch_all_linear_tickers()
        assert tickers == []

    async def test_error_ret_code_returns_empty_list(self):
        def handler(request):
            return httpx.Response(200, json=_ticker_body([], ret_code=170121))

        provider = _provider(handler)
        tickers = await provider.fetch_all_linear_tickers()
        assert tickers == []

    async def test_non_usdt_symbol_skipped(self):
        def handler(request):
            return httpx.Response(
                200,
                json=_ticker_body(
                    [
                        {"symbol": "BTCUSDC", "openInterestValue": "1", "turnover24h": "2"},
                        {"symbol": "ETHUSDT", "openInterestValue": "3", "turnover24h": "4"},
                    ]
                ),
            )

        provider = _provider(handler)
        tickers = await provider.fetch_all_linear_tickers()
        symbols = {t.symbol for t in tickers}
        assert symbols == {"ETH-USDT"}

    async def test_missing_open_interest_field_yields_none_not_skip(self):
        def handler(request):
            return httpx.Response(
                200,
                json=_ticker_body([{"symbol": "BTCUSDT", "turnover24h": "4"}]),
            )

        provider = _provider(handler)
        tickers = await provider.fetch_all_linear_tickers()
        assert len(tickers) == 1
        assert tickers[0].open_interest_usdt is None
        assert tickers[0].turnover_24h_usdt == 4.0

    async def test_missing_turnover_field_yields_none_not_skip(self):
        def handler(request):
            return httpx.Response(
                200,
                json=_ticker_body([{"symbol": "BTCUSDT", "openInterestValue": "4"}]),
            )

        provider = _provider(handler)
        tickers = await provider.fetch_all_linear_tickers()
        assert len(tickers) == 1
        assert tickers[0].open_interest_usdt == 4.0
        assert tickers[0].turnover_24h_usdt is None

    async def test_malformed_numeric_field_yields_none_not_crash(self):
        def handler(request):
            return httpx.Response(
                200,
                json=_ticker_body(
                    [{"symbol": "BTCUSDT", "openInterestValue": "not-a-number", "turnover24h": "4"}]
                ),
            )

        provider = _provider(handler)
        tickers = await provider.fetch_all_linear_tickers()
        assert len(tickers) == 1
        assert tickers[0].open_interest_usdt is None
        assert tickers[0].turnover_24h_usdt == 4.0

    async def test_row_missing_symbol_skipped(self):
        def handler(request):
            return httpx.Response(
                200,
                json=_ticker_body(
                    [
                        {"openInterestValue": "1", "turnover24h": "2"},
                        {"symbol": "ETHUSDT", "openInterestValue": "3", "turnover24h": "4"},
                    ]
                ),
            )

        provider = _provider(handler)
        tickers = await provider.fetch_all_linear_tickers()
        assert len(tickers) == 1
        assert tickers[0].symbol == "ETH-USDT"

    async def test_non_dict_row_skipped(self):
        def handler(request):
            return httpx.Response(
                200,
                json=_ticker_body(["not-a-dict", {"symbol": "ETHUSDT", "openInterestValue": "3", "turnover24h": "4"}]),
            )

        provider = _provider(handler)
        tickers = await provider.fetch_all_linear_tickers()
        assert len(tickers) == 1

    async def test_missing_result_list_returns_empty_list(self):
        def handler(request):
            return httpx.Response(
                200,
                json={"retCode": 0, "retMsg": "OK", "result": {}, "retExtInfo": {}, "time": 0},
            )

        provider = _provider(handler)
        tickers = await provider.fetch_all_linear_tickers()
        assert tickers == []

    async def test_no_api_keys_used(self):
        captured = {}

        def handler(request):
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, json=_ticker_body([]))

        provider = _provider(handler)
        await provider.fetch_all_linear_tickers()
        header_keys = {key.lower() for key in captured["headers"]}
        assert "x-bapi-api-key" not in header_keys
        assert "x-bapi-sign" not in header_keys
