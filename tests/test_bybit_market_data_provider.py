"""
Unit tests for app.data.bybit_market_data_provider.BybitMarketDataProvider.

All HTTP interactions use httpx.MockTransport; no real network requests
are made.
"""

import asyncio
import socket
import ssl
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest

from app.data.bybit_market_data_provider import BybitMarketDataProvider
from app.data.market_data_errors import (
    MarketDataError,
    MarketDataRequestError,
    MarketDataResponseError,
)

BASE_URL = "https://api.bybit.com"
UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _row(start_time: datetime) -> list[str]:
    ts_ms = str(int(start_time.timestamp() * 1000))
    return [ts_ms, "100", "110", "95", "105", "1000", "100000"]


def _closed_row(minutes_ago: int, reference: datetime = UTC_NOW) -> list[str]:
    """A kline row guaranteed complete against any 15m/1h/4h interval."""
    return _row(reference - timedelta(hours=24, minutes=minutes_ago))


def _bybit_body(rows: list[list[str]], ret_code: int = 0) -> dict:
    return {
        "retCode": ret_code,
        "retMsg": "OK",
        "result": {"category": "linear", "symbol": "BTCUSDT", "list": rows},
        "retExtInfo": {},
        "time": int(UTC_NOW.timestamp() * 1000),
    }


def _make_provider(handler, **kwargs) -> BybitMarketDataProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url=BASE_URL, transport=transport)
    kwargs.setdefault("retry_backoff_schedule_seconds", (0.0, 0.0, 0.0))
    kwargs.setdefault("min_request_interval_seconds", 0.0)
    kwargs.setdefault("inter_timeframe_delay_seconds", 0.0)
    return BybitMarketDataProvider(
        base_url=BASE_URL, request_timeout_seconds=10, client=client, **kwargs
    )


@pytest.mark.asyncio
async def test_successful_candle_fetch():
    rows = [_closed_row(60), _closed_row(75)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_bybit_body(rows))

    async with _make_provider(handler) as provider:
        candles = await provider.fetch_candles("BTC-USDT", "15m", 100)

    assert len(candles) == 2
    assert candles[0].timestamp < candles[1].timestamp


@pytest.mark.asyncio
async def test_correct_endpoint_path():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json=_bybit_body([]))

    async with _make_provider(handler) as provider:
        await provider.fetch_candles("BTC-USDT", "15m", 10)

    assert captured["path"] == "/v5/market/kline"


@pytest.mark.asyncio
async def test_symbol_hyphen_stripped_for_bybit():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["symbol"] = request.url.params.get("symbol")
        return httpx.Response(200, json=_bybit_body([]))

    async with _make_provider(handler) as provider:
        await provider.fetch_candles("ETH-USDT", "15m", 10)

    assert captured["symbol"] == "ETHUSDT"


@pytest.mark.asyncio
async def test_correct_category_parameter():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["category"] = request.url.params.get("category")
        return httpx.Response(200, json=_bybit_body([]))

    async with _make_provider(handler) as provider:
        await provider.fetch_candles("BTC-USDT", "15m", 10)

    assert captured["category"] == "linear"


@pytest.mark.asyncio
async def test_correct_interval_conversion():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["interval"] = request.url.params.get("interval")
        return httpx.Response(200, json=_bybit_body([]))

    async with _make_provider(handler) as provider:
        await provider.fetch_candles("BTC-USDT", "4h", 10)

    assert captured["interval"] == "240"


@pytest.mark.asyncio
async def test_correct_limit_parameter():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["limit"] = request.url.params.get("limit")
        return httpx.Response(200, json=_bybit_body([]))

    async with _make_provider(handler) as provider:
        await provider.fetch_candles("BTC-USDT", "15m", 42)

    assert captured["limit"] == "42"


@pytest.mark.asyncio
async def test_newest_first_response_converted_to_ascending_order():
    rows = [_closed_row(0), _closed_row(30), _closed_row(60)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_bybit_body(rows))

    async with _make_provider(handler) as provider:
        candles = await provider.fetch_candles("BTC-USDT", "15m", 100)

    timestamps = [c.timestamp for c in candles]
    assert timestamps == sorted(timestamps)


@pytest.mark.asyncio
async def test_still_forming_candle_excluded():
    # The most recent row (now-2 minutes) is still within a 15m window
    # and has no explicit "closed" flag from Bybit; completeness is
    # inferred from startTime + interval duration <= now (real wall-clock
    # time, since the provider itself calls datetime.now(timezone.utc)).
    forming = _row(datetime.now(timezone.utc) - timedelta(minutes=2))
    completed = _closed_row(60)
    rows = [forming, completed]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_bybit_body(rows))

    async with _make_provider(handler) as provider:
        candles = await provider.fetch_candles("BTC-USDT", "15m", 100)

    assert len(candles) == 1


@pytest.mark.asyncio
async def test_timestamp_converted_to_utc():
    rows = [_closed_row(60)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_bybit_body(rows))

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
async def test_bybit_non_zero_ret_code_permanent_not_retried():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_bybit_body([], ret_code=10001))

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataError):
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    assert call_count == 1


@pytest.mark.asyncio
async def test_malformed_candle_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {"list": [["too", "short"]]},
            },
        )

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataError):
            await provider.fetch_candles("BTC-USDT", "15m", 10)


@pytest.mark.asyncio
async def test_unsupported_symbol():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_bybit_body([]))

    async with _make_provider(handler) as provider:
        with pytest.raises(ValueError):
            await provider.fetch_candles("FAKE-COIN", "15m", 10)


@pytest.mark.asyncio
async def test_well_formed_symbol_not_in_allow_list_rejected_by_default():
    # PEPE-USDT is well-formed (SYMBOL-QUOTE) but not in the static
    # configured pair list, so the default allow-list validation must
    # still reject it.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_bybit_body([]))

    async with _make_provider(handler) as provider:
        with pytest.raises(ValueError):
            await provider.fetch_candles("PEPE-USDT", "15m", 10)


@pytest.mark.asyncio
async def test_allow_list_check_can_be_bypassed_for_warm_up_fetches():
    # With validate_symbol_against_allow_list=False, a well-formed
    # symbol not yet in the configured pair list is still accepted --
    # this is what lets a newly-discovered pair's warm-up fetch
    # succeed *before* that pair is added to get_configured_pairs().
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["symbol"] = request.url.params.get("symbol")
        return httpx.Response(200, json=_bybit_body([]))

    async with _make_provider(handler, validate_symbol_against_allow_list=False) as provider:
        await provider.fetch_candles("PEPE-USDT", "15m", 10)

    assert captured["symbol"] == "PEPEUSDT"


@pytest.mark.asyncio
async def test_bypassed_allow_list_still_enforces_symbol_format():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_bybit_body([]))

    async with _make_provider(handler, validate_symbol_against_allow_list=False) as provider:
        with pytest.raises(ValueError):
            await provider.fetch_candles("not-a-valid-format!!", "15m", 10)


@pytest.mark.asyncio
async def test_unsupported_timeframe():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_bybit_body([]))

    async with _make_provider(handler) as provider:
        with pytest.raises(ValueError):
            await provider.fetch_candles("BTC-USDT", "1m", 10)


@pytest.mark.asyncio
async def test_invalid_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_bybit_body([]))

    async with _make_provider(handler) as provider:
        with pytest.raises(ValueError):
            await provider.fetch_candles("BTC-USDT", "15m", 0)

        with pytest.raises(ValueError):
            await provider.fetch_candles("BTC-USDT", "15m", 10_000)


@pytest.mark.asyncio
async def test_multiple_timeframe_fetching():
    def handler(request: httpx.Request) -> httpx.Response:
        interval = request.url.params.get("interval")
        offsets = {"15": 60, "60": 120, "240": 300}
        return httpx.Response(200, json=_bybit_body([_closed_row(offsets[interval])]))

    async with _make_provider(handler) as provider:
        result = await provider.fetch_multiple_timeframes("BTC-USDT", ["15m", "1h", "4h"])

    assert set(result.keys()) == {"15m", "1h", "4h"}
    for candles in result.values():
        assert len(candles) == 1


@pytest.mark.asyncio
async def test_one_timeframe_failure_fails_entire_operation():
    def handler(request: httpx.Request) -> httpx.Response:
        interval = request.url.params.get("interval")
        if interval == "60":
            return httpx.Response(500, text="server error")
        return httpx.Response(200, json=_bybit_body([]))

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataError):
            await provider.fetch_multiple_timeframes("BTC-USDT", ["15m", "1h", "4h"])


@pytest.mark.asyncio
async def test_injected_client_is_not_closed_automatically():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_bybit_body([]))

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url=BASE_URL, transport=transport)
    provider = BybitMarketDataProvider(
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
    assert "api.bybit.com" in message
    # No query parameters (symbol/limit/interval values) or secrets in the message.
    assert "symbol=" not in message


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
    assert "api.bybit.com" in message


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
    assert "api.bybit.com" in message


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
    assert "api.bybit.com" in message


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
    assert "api.bybit.com" in message
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
    assert "api.bybit.com" in message
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
    assert "api.bybit.com" in message
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
    assert "interval=" not in message


@pytest.mark.asyncio
async def test_generic_transport_error_still_raises_market_data_request_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataRequestError) as exc_info:
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    assert "connect_error" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Rate-limit (HTTP 429 and body-level retCode) handling and scheduling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_429_then_success():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json=_bybit_body([]))

    async with _make_provider(handler) as provider:
        candles = await provider.fetch_candles("BTC-USDT", "15m", 10)

    assert candles == []
    assert call_count == 2


@pytest.mark.asyncio
async def test_ret_code_rate_limit_then_success():
    # Bybit can signal rate-limiting via a non-zero retCode on an
    # HTTP-200 response, not just via HTTP 429.
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, json=_bybit_body([], ret_code=10006))
        return httpx.Response(200, json=_bybit_body([]))

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
        return httpx.Response(200, json=_bybit_body([]))

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
async def test_ret_code_rate_limit_exhausted_retries_raises():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_bybit_body([], ret_code=10018))

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataError):
            await provider.fetch_candles("BTC-USDT", "15m", 10)

    assert call_count == 3


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
        return httpx.Response(200, json=_bybit_body([]))

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
        interval = request.url.params.get("interval")
        call_counts[interval] = call_counts.get(interval, 0) + 1
        if interval == "60":
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json=_bybit_body([]))

    async with _make_provider(handler) as provider:
        with pytest.raises(MarketDataError) as exc_info:
            await provider.fetch_multiple_timeframes("BTC-USDT", ["15m", "1h", "4h"])

    # No partial timeframe result is returned; the whole symbol fails.
    assert "1h" in str(exc_info.value)
    assert call_counts["60"] == 3
