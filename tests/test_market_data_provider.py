"""
Unit tests for app.data.market_data_provider.OKXMarketDataProvider.

All HTTP interactions use httpx.MockTransport; no real network requests
are made.
"""

import asyncio
import json
from datetime import timezone
from unittest.mock import patch

import httpx
import pytest

import socket
import ssl

from app.data.market_data_provider import (
    MarketDataError,
    MarketDataRequestError,
    MarketDataResponseError,
    OKXMarketDataProvider,
)

BASE_URL = "https://www.okx.com"


def _row(ts_ms: str, confirm: str = "1") -> list[str]:
    return [ts_ms, "100", "110", "95", "105", "1000", "1000", "1000", confirm]


def _make_provider(handler, **kwargs) -> OKXMarketDataProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url=BASE_URL, transport=transport)
    kwargs.setdefault("retry_backoff_schedule_seconds", (0.0, 0.0, 0.0))
    kwargs.setdefault("min_request_interval_seconds", 0.0)
    kwargs.setdefault("inter_timeframe_delay_seconds", 0.0)
    return OKXMarketDataProvider(
        base_url=BASE_URL, request_timeout_seconds=10, client=client, **kwargs
    )


@pytest.mark.asyncio
async def test_successful_candle_fetch():
    rows = [_row("1700000060000"), _row("1700000000000")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})

    async with _make_provider(handler) as provider:
        candles = await provider.fetch_candles("BTC-USDT", "15m", 100)

    assert len(candles) == 2
    assert candles[0].timestamp < candles[1].timestamp


@pytest.mark.asyncio
async def test_correct_endpoint_path():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    async with _make_provider(handler) as provider:
        await provider.fetch_candles("BTC-USDT", "15m", 10)

    assert captured["path"] == "/api/v5/market/candles"


@pytest.mark.asyncio
async def test_correct_inst_id_parameter():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["instId"] = request.url.params.get("instId")
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    async with _make_provider(handler) as provider:
        await provider.fetch_candles("ETH-USDT", "15m", 10)

    assert captured["instId"] == "ETH-USDT"


@pytest.mark.asyncio
async def test_correct_timeframe_conversion():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["bar"] = request.url.params.get("bar")
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    async with _make_provider(handler) as provider:
        await provider.fetch_candles("BTC-USDT", "4h", 10)

    assert captured["bar"] == "4H"


@pytest.mark.asyncio
async def test_correct_limit_parameter():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["limit"] = request.url.params.get("limit")
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    async with _make_provider(handler) as provider:
        await provider.fetch_candles("BTC-USDT", "15m", 42)

    assert captured["limit"] == "42"


@pytest.mark.asyncio
async def test_newest_first_response_converted_to_ascending_order():
    rows = [_row("1700000120000"), _row("1700000060000"), _row("1700000000000")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})

    async with _make_provider(handler) as provider:
        candles = await provider.fetch_candles("BTC-USDT", "15m", 100)

    timestamps = [c.timestamp for c in candles]
    assert timestamps == sorted(timestamps)


@pytest.mark.asyncio
async def test_incomplete_candle_excluded():
    rows = [_row("1700000060000", confirm="0"), _row("1700000000000", confirm="1")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})

    async with _make_provider(handler) as provider:
        candles = await provider.fetch_candles("BTC-USDT", "15m", 100)

    assert len(candles) == 1
    assert candles[0].timestamp.timestamp() == 1700000000


@pytest.mark.asyncio
async def test_timestamp_converted_to_utc():
    rows = [_row("1700000000000")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "msg": "", "data": rows})

    async with _make_provider(handler) as provider:
        candles = await provider.fetch_candles("BTC-USDT", "15m", 10)

    assert candles[0].timestamp.tzinfo is not None
    assert candles[0].timestamp.utcoffset() == timezone.utc.utcoffset(None)


@pytest.mark.asyncio
async def test_http_error_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error")

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataResponseError):
            await provider.fetch_candles("BTC-USDT", "15m", 10)


@pytest.mark.asyncio
async def test_invalid_json_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataResponseError):
            await provider.fetch_candles("BTC-USDT", "15m", 10)


@pytest.mark.asyncio
async def test_okx_non_zero_response_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"code": "1", "msg": "bad request", "data": []}
        )

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataError):
            await provider.fetch_candles("BTC-USDT", "15m", 10)


@pytest.mark.asyncio
async def test_malformed_candle_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"code": "0", "msg": "", "data": [["too", "short"]]}
        )

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataError):
            await provider.fetch_candles("BTC-USDT", "15m", 10)


@pytest.mark.asyncio
async def test_unsupported_symbol():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    async with _make_provider(handler) as provider:
        with pytest.raises(ValueError):
            await provider.fetch_candles("FAKE-COIN", "15m", 10)


@pytest.mark.asyncio
async def test_unsupported_timeframe():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    async with _make_provider(handler) as provider:
        with pytest.raises(ValueError):
            await provider.fetch_candles("BTC-USDT", "1m", 10)


@pytest.mark.asyncio
async def test_invalid_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    async with _make_provider(handler) as provider:
        with pytest.raises(ValueError):
            await provider.fetch_candles("BTC-USDT", "15m", 0)

        with pytest.raises(ValueError):
            await provider.fetch_candles("BTC-USDT", "15m", 10_000)


@pytest.mark.asyncio
async def test_multiple_timeframe_fetching():
    def handler(request: httpx.Request) -> httpx.Response:
        bar = request.url.params.get("bar")
        ts_map = {"15m": "1700000000000", "1H": "1700000060000", "4H": "1700000120000"}
        return httpx.Response(
            200, json={"code": "0", "msg": "", "data": [_row(ts_map[bar])]}
        )

    async with _make_provider(handler) as provider:
        result = await provider.fetch_multiple_timeframes(
            "BTC-USDT", ["15m", "1h", "4h"]
        )

    assert set(result.keys()) == {"15m", "1h", "4h"}
    for candles in result.values():
        assert len(candles) == 1


@pytest.mark.asyncio
async def test_one_timeframe_failure_fails_entire_operation():
    def handler(request: httpx.Request) -> httpx.Response:
        bar = request.url.params.get("bar")
        if bar == "1H":
            return httpx.Response(500, text="server error")
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataError):
            await provider.fetch_multiple_timeframes("BTC-USDT", ["15m", "1h", "4h"])


@pytest.mark.asyncio
async def test_injected_client_is_not_closed_automatically():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url=BASE_URL, transport=transport)
    provider = OKXMarketDataProvider(
        base_url=BASE_URL, request_timeout_seconds=10, client=client
    )

    async with provider:
        await provider.fetch_candles("BTC-USDT", "15m", 10)

    assert not client.is_closed
    await client.aclose()


# ---------------------------------------------------------------------------
# Transport-level failure diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dns_failure_diagnostics():
    def handler(request: httpx.Request) -> httpx.Response:
        exc = httpx.ConnectError("getaddrinfo failed")
        exc.__cause__ = socket.gaierror("Name or service not known")
        raise exc

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataRequestError) as exc_info:
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    message = str(exc_info.value)
    assert "ConnectError" in message
    assert "dns_failure" in message
    assert "www.okx.com" in message
    # No query parameters (symbol/limit/bar values) or secrets in the message.
    assert "instId" not in message


@pytest.mark.asyncio
async def test_connect_timeout_diagnostics():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataRequestError) as exc_info:
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    message = str(exc_info.value)
    assert "ConnectTimeout" in message
    assert "connect_timeout" in message
    assert "www.okx.com" in message


@pytest.mark.asyncio
async def test_read_timeout_diagnostics():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataRequestError) as exc_info:
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    message = str(exc_info.value)
    assert "ReadTimeout" in message
    assert "read_timeout" in message
    assert "www.okx.com" in message


@pytest.mark.asyncio
async def test_ssl_error_diagnostics():
    def handler(request: httpx.Request) -> httpx.Response:
        exc = httpx.ConnectError("SSL handshake failed")
        exc.__cause__ = ssl.SSLError("certificate verify failed")
        raise exc

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataRequestError) as exc_info:
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    message = str(exc_info.value)
    assert "ConnectError" in message
    assert "ssl_error" in message
    assert "www.okx.com" in message


@pytest.mark.asyncio
async def test_http_403_diagnostics():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(403, text="forbidden")

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataResponseError) as exc_info:
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    message = str(exc_info.value)
    assert "403" in message
    assert "www.okx.com" in message
    assert "forbidden" not in message  # never logs the full response body
    # Permanent client errors (400/401/403) are never retried.
    assert call_count == 1


@pytest.mark.asyncio
async def test_http_429_diagnostics():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataResponseError) as exc_info:
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    message = str(exc_info.value)
    assert "429" in message
    assert "www.okx.com" in message
    assert "attempts=3" in message
    assert "rate limited" not in message


@pytest.mark.asyncio
async def test_http_500_diagnostics():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error")

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataResponseError) as exc_info:
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    message = str(exc_info.value)
    assert "500" in message
    assert "www.okx.com" in message
    assert "attempts=3" in message


@pytest.mark.asyncio
async def test_transport_failure_never_includes_query_secrets():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataRequestError) as exc_info:
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    message = str(exc_info.value)
    assert "?" not in message
    assert "limit=10" not in message
    assert "bar=" not in message


@pytest.mark.asyncio
async def test_generic_transport_error_still_raises_market_data_request_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataRequestError) as exc_info:
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    assert "connect_error" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Rate-limit (HTTP 429) handling and request scheduling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_429_then_success():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    async with _make_provider(handler) as provider:
        candles = await provider.fetch_candles("BTC-USDT", "15m", 10)

    assert candles == []
    assert call_count == 2


@pytest.mark.asyncio
async def test_retry_after_header_used_when_present():
    call_count = 0
    sleep_calls: list[float] = []

    real_sleep = asyncio.sleep

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        await real_sleep(0)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, text="rate limited")
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    async with _make_provider(handler) as provider:
        with patch("asyncio.sleep", _fake_sleep):
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    assert call_count == 2
    assert 7.0 in sleep_calls


@pytest.mark.asyncio
async def test_429_exhausted_retries_raises_with_three_attempts():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, text="rate limited")

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataResponseError) as exc_info:
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    assert call_count == 3
    assert "429" in str(exc_info.value)
    assert "attempts=3" in str(exc_info.value)


@pytest.mark.asyncio
async def test_concurrent_requests_respect_concurrency_limiter():
    in_flight = 0
    max_observed_in_flight = 0
    lock = asyncio.Lock()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, max_observed_in_flight
        async with lock:
            in_flight += 1
            max_observed_in_flight = max(max_observed_in_flight, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    async with _make_provider(
        handler, max_concurrent_requests=2, min_request_interval_seconds=0.0
    ) as provider:
        await asyncio.gather(
            *(provider.fetch_candles("BTC-USDT", "15m", 10) for _ in range(6))
        )

    assert max_observed_in_flight <= 2


@pytest.mark.asyncio
async def test_permanent_error_not_retried():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(400, text="bad request")

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataResponseError):
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    assert call_count == 1


@pytest.mark.asyncio
async def test_complete_symbol_failure_when_one_timeframe_exhausts_retries():
    call_counts: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        bar = request.url.params.get("bar")
        call_counts[bar] = call_counts.get(bar, 0) + 1
        if bar == "1H":
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json={"code": "0", "msg": "", "data": []})

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataError) as exc_info:
            await provider.fetch_multiple_timeframes("BTC-USDT", ["15m", "1h", "4h"])

    # No partial timeframe result is returned; the whole symbol fails.
    assert "1h" in str(exc_info.value)
    assert call_counts["1H"] == 3
