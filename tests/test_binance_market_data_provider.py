"""
Tests for BinanceFuturesMarketDataProvider: kline fetch/parse, retry,
backoff, rate-limiting, and transport-failure diagnostics, using
httpx.MockTransport (no real network calls).
"""

import asyncio
from unittest.mock import patch

import httpx
import pytest

from app.data.binance_market_data_provider import (
    BinanceFuturesMarketDataProvider,
    KLINE_ENDPOINT,
)
from app.data.market_data_errors import (
    MarketDataRequestError,
    MarketDataResponseError,
    MarketDataValidationError,
)

pytestmark = pytest.mark.asyncio

BASE_URL = "https://fapi.binance.com"


def _kline_row(
    open_time_ms: int,
    *,
    open_price: str = "100.0",
    high: str = "101.0",
    low: str = "99.0",
    close: str = "100.5",
    volume: str = "10.0",
    close_time_ms: int,
) -> list:
    return [
        open_time_ms,
        open_price,
        high,
        low,
        close,
        volume,
        close_time_ms,
        "1000.0",  # quoteVolume
        100,  # numTrades
        "5.0",  # takerBuyBaseVolume
        "500.0",  # takerBuyQuoteVolume
        "0",  # ignore
    ]


def _make_provider(handler, **kwargs) -> BinanceFuturesMarketDataProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url=BASE_URL)
    return BinanceFuturesMarketDataProvider(
        base_url=BASE_URL, request_timeout_seconds=10.0, client=client, **kwargs
    )


# A candle far enough in the past to always be "completed" relative to now.
_OLD_OPEN_MS = 1_600_000_000_000
_OLD_CLOSE_MS = _OLD_OPEN_MS + 15 * 60 * 1000 - 1


class TestFetchCandles:
    async def test_correct_endpoint_and_params(self):
        captured = {}

        def handler(request):
            captured["path"] = request.url.path
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json=[])

        provider = _make_provider(handler)
        await provider.fetch_candles("BTC-USDT", "15m", 10)
        assert captured["path"] == KLINE_ENDPOINT
        assert captured["params"]["symbol"] == "BTCUSDT"
        assert captured["params"]["interval"] == "15m"
        assert captured["params"]["limit"] == "10"
        # No category param (Binance doesn't use one, unlike Bybit).
        assert "category" not in captured["params"]

    async def test_htf_timeframes_map_to_binance_intervals(self):
        captured = {}

        def handler(request):
            captured["interval"] = request.url.params["interval"]
            return httpx.Response(200, json=[])

        provider = _make_provider(handler)
        await provider.fetch_candles("BTC-USDT", "1h", 10)
        assert captured["interval"] == "1h"

        await provider.fetch_candles("BTC-USDT", "4h", 10)
        assert captured["interval"] == "4h"

    async def test_parses_completed_candles_ascending(self):
        rows = [
            _kline_row(_OLD_OPEN_MS, close_time_ms=_OLD_CLOSE_MS),
            _kline_row(
                _OLD_OPEN_MS + 15 * 60 * 1000,
                close_time_ms=_OLD_CLOSE_MS + 15 * 60 * 1000,
            ),
        ]
        provider = _make_provider(lambda r: httpx.Response(200, json=rows))
        candles = await provider.fetch_candles("BTC-USDT", "15m", 10)
        assert len(candles) == 2
        assert candles[0].timestamp < candles[1].timestamp
        assert candles[0].symbol == "BTC-USDT"
        assert candles[0].timeframe == "15m"
        assert candles[0].open == 100.0
        assert candles[0].close == 100.5

    async def test_incomplete_candle_excluded(self):
        import time

        now_ms = int(time.time() * 1000)
        incomplete_row = _kline_row(now_ms, close_time_ms=now_ms + 15 * 60 * 1000)
        provider = _make_provider(lambda r: httpx.Response(200, json=[incomplete_row]))
        candles = await provider.fetch_candles("BTC-USDT", "15m", 10)
        assert candles == []

    async def test_non_list_payload_raises_validation_error(self):
        provider = _make_provider(lambda r: httpx.Response(200, json={"not": "a list"}))
        with pytest.raises(MarketDataValidationError):
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    async def test_malformed_row_raises_validation_error(self):
        provider = _make_provider(lambda r: httpx.Response(200, json=[["too", "short"]]))
        with pytest.raises(MarketDataValidationError):
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    async def test_http_error_status_raises_response_error(self):
        provider = _make_provider(lambda r: httpx.Response(400, json={"code": -1121, "msg": "bad"}))
        with pytest.raises(MarketDataResponseError):
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    async def test_malformed_json_raises_response_error(self):
        provider = _make_provider(lambda r: httpx.Response(200, content=b"not json"))
        with pytest.raises(MarketDataResponseError):
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    async def test_invalid_symbol_raises_value_error(self):
        provider = _make_provider(lambda r: httpx.Response(200, json=[]))
        with pytest.raises(ValueError):
            await provider.fetch_candles("not-a-symbol", "15m", 10)

    async def test_zero_limit_raises_value_error(self):
        provider = _make_provider(lambda r: httpx.Response(200, json=[]))
        with pytest.raises(ValueError):
            await provider.fetch_candles("BTC-USDT", "15m", 0)

    async def test_limit_above_maximum_raises_value_error(self):
        from app.data.binance_market_data_provider import MAX_CANDLE_LIMIT

        provider = _make_provider(lambda r: httpx.Response(200, json=[]))
        with pytest.raises(ValueError):
            await provider.fetch_candles("BTC-USDT", "15m", MAX_CANDLE_LIMIT + 1)

    async def test_network_error_raises_request_error(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        provider = _make_provider(handler, max_request_attempts=1)
        with pytest.raises(MarketDataRequestError):
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    async def test_no_api_keys_used(self):
        captured = {}

        def handler(request):
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, json=[])

        provider = _make_provider(handler)
        await provider.fetch_candles("BTC-USDT", "15m", 10)
        header_keys = {key.lower() for key in captured["headers"]}
        assert "x-mbx-apikey" not in header_keys


class TestRetryBehavior:
    async def test_429_then_success(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, text="rate limited")
            return httpx.Response(200, json=[])

        provider = _make_provider(handler)
        candles = await provider.fetch_candles("BTC-USDT", "15m", 10)
        assert candles == []
        assert call_count == 2

    async def test_418_ip_ban_is_retried(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(418, text="ip banned")
            return httpx.Response(200, json=[])

        provider = _make_provider(handler)
        await provider.fetch_candles("BTC-USDT", "15m", 10)
        assert call_count == 2

    async def test_5xx_retried(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(503)
            return httpx.Response(200, json=[])

        provider = _make_provider(handler)
        await provider.fetch_candles("BTC-USDT", "15m", 10)
        assert call_count == 2

    async def test_400_not_retried(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(400, json={"code": -1121, "msg": "bad symbol"})

        provider = _make_provider(handler)
        with pytest.raises(MarketDataResponseError):
            await provider.fetch_candles("BTC-USDT", "15m", 10)
        assert call_count == 1

    async def test_exhausted_retries_returns_last_response(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(429, text="rate limited")

        provider = _make_provider(handler, max_request_attempts=3)
        with pytest.raises(MarketDataResponseError):
            await provider.fetch_candles("BTC-USDT", "15m", 10)
        assert call_count == 3

    async def test_retry_after_header_used_when_present(self):
        call_count = 0
        sleep_calls: list[float] = []
        real_sleep = asyncio.sleep

        async def _fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            await real_sleep(0)

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, headers={"Retry-After": "7"}, text="rate limited")
            return httpx.Response(200, json=[])

        provider = _make_provider(handler)
        with patch("asyncio.sleep", _fake_sleep):
            await provider.fetch_candles("BTC-USDT", "15m", 10)
        assert 7.0 in sleep_calls

    async def test_total_retry_count_increments(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, text="rate limited")
            return httpx.Response(200, json=[])

        provider = _make_provider(handler)
        assert provider.total_retry_count == 0
        await provider.fetch_candles("BTC-USDT", "15m", 10)
        assert provider.total_retry_count == 1

    async def test_transient_network_error_retried(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ReadTimeout("timed out")
            return httpx.Response(200, json=[])

        provider = _make_provider(handler)
        await provider.fetch_candles("BTC-USDT", "15m", 10)
        assert call_count == 2

    async def test_dns_failure_not_retried(self):
        import socket

        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            error = httpx.ConnectError("connect failed")
            error.__cause__ = socket.gaierror("name resolution failed")
            raise error

        provider = _make_provider(handler, max_request_attempts=3)
        with pytest.raises(MarketDataRequestError):
            await provider.fetch_candles("BTC-USDT", "15m", 10)
        assert call_count == 1


class TestSharedRateLimiterWeightAndBans:
    """
    Covers the 2F rate-limit hardening: proactively tracking Binance's
    own X-MBX-USED-WEIGHT-1M header, and a global cooldown gate that
    applies to every request through a shared rate limiter -- not just
    the one that received a 418/429 -- once Binance actually signals a
    ban/rate-limit via a Retry-After header.
    """

    async def test_used_weight_header_recorded_after_a_request(self):
        def handler(request):
            return httpx.Response(200, json=[], headers={"X-MBX-USED-WEIGHT-1M": "1900"})

        provider = _make_provider(handler)
        await provider.fetch_candles("BTC-USDT", "15m", 10)
        assert provider._rate_limiter._used_weight_1m == 1900

    async def test_missing_weight_header_does_not_crash_or_overwrite(self):
        def handler(request):
            return httpx.Response(200, json=[])

        provider = _make_provider(handler)
        await provider.fetch_candles("BTC-USDT", "15m", 10)
        assert provider._rate_limiter._used_weight_1m == 0

    async def test_418_with_retry_after_bans_globally_for_a_different_endpoint(self):
        """
        A 418 on the klines endpoint (with a real Retry-After) must
        block a *subsequent, unrelated* request through the same shared
        rate limiter -- e.g. a ticker-price fetch -- for the full
        duration, not just further klines requests for the same symbol.
        """
        def kline_handler(request):
            return httpx.Response(418, headers={"Retry-After": "42"}, text="ip banned")

        # max_request_attempts=1: the klines call itself never retries
        # (it returns/raises on the one and only attempt), so the ban it
        # records is still pending -- not yet "waited out" by its own
        # retry loop -- when the next, unrelated call comes in.
        provider = _make_provider(kline_handler, max_request_attempts=1)

        sleep_calls: list[float] = []
        real_sleep = asyncio.sleep

        async def _fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            await real_sleep(0)

        with patch("asyncio.sleep", _fake_sleep):
            with pytest.raises(MarketDataResponseError):
                await provider.fetch_candles("BTC-USDT", "15m", 10)
            assert provider._rate_limiter._banned_until_monotonic is not None

            await provider.fetch_ticker_price("ETH-USDT")

        assert any(seconds > 40.0 for seconds in sleep_calls)

    async def test_notify_rate_limited_only_extends_never_shrinks(self):
        from app.data.binance_market_data_provider import _AsyncRequestRateLimiter

        limiter = _AsyncRequestRateLimiter(max_concurrent_requests=8, min_interval_seconds=0.0)
        limiter.notify_rate_limited(100.0)
        first_deadline = limiter._banned_until_monotonic
        limiter.notify_rate_limited(1.0)
        assert limiter._banned_until_monotonic == first_deadline

    async def test_fallback_ban_only_applied_on_last_attempt_without_retry_after(self):
        """
        A bare 429 with no Retry-After header must not trigger the
        (long, conservative) fallback global ban on a non-final retry
        attempt -- only once the retry loop is about to give up
        entirely. Otherwise a single transient, unlabeled 429 would
        stall every other in-flight request for a full minute even
        though the very next local retry succeeds moments later.
        """
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, text="rate limited")  # no Retry-After
            return httpx.Response(200, json=[])

        provider = _make_provider(handler, max_request_attempts=3)
        await provider.fetch_candles("BTC-USDT", "15m", 10)
        assert call_count == 2
        assert provider._rate_limiter._banned_until_monotonic is None

    async def test_fallback_ban_applied_when_retries_exhausted_without_retry_after(self):
        def handler(request):
            return httpx.Response(429, text="rate limited")  # no Retry-After, every attempt

        provider = _make_provider(handler, max_request_attempts=2)
        with pytest.raises(MarketDataResponseError):
            await provider.fetch_candles("BTC-USDT", "15m", 10)
        assert provider._rate_limiter._banned_until_monotonic is not None
