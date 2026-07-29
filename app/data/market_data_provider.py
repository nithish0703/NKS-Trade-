"""
Provides market data (candles) from external or internal sources.

This module implements an OKX public-market-data REST client. It only
fetches, validates, and normalizes raw candle data into the immutable
Candle model. It does not perform any indicator calculation or trading
strategy analysis.
"""

import asyncio
import logging
import ssl
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx

from app.config.pairs import validate_pair_symbol
from app.config.timeframes import REQUIRED_CANDLE_LIMITS, get_exchange_timeframe
from app.data.data_validator import (
    DataValidationError,
    is_completed_candle,
    validate_candle_sequence,
    validate_okx_response,
)
from app.models.candle import Candle

CANDLES_ENDPOINT = "/api/v5/market/candles"
TICKER_ENDPOINT = "/api/v5/market/ticker"
MAX_CANDLE_LIMIT = 300

# Request scheduling / retry tuning. These control only HTTP request
# timing and retry behaviour against OKX's public market-data API; they
# never affect any strategy rule, threshold, or indicator calculation.
MAX_REQUEST_ATTEMPTS = 3
RETRY_BACKOFF_SCHEDULE_SECONDS = (1.0, 2.0, 4.0)
MAX_CONCURRENT_REQUESTS = 4
MIN_REQUEST_INTERVAL_SECONDS = 0.1
INTER_TIMEFRAME_DELAY_SECONDS = 0.2

_RETRYABLE_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}

# Only network-layer failures that are typically transient (a dropped or
# reset connection, a timeout) are retried. DNS failures, SSL/certificate
# errors are treated as non-transient and surfaced immediately, without
# retry.
_TRANSIENT_EXCEPTION_TYPES = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)

_logger = logging.getLogger(__name__)


class MarketDataError(Exception):
    """Base exception for all market-data related failures."""


class MarketDataRequestError(MarketDataError):
    """Raised when the HTTP request to the exchange fails."""


class MarketDataResponseError(MarketDataError):
    """Raised when the exchange returns an invalid or non-success response."""


class MarketDataValidationError(MarketDataError):
    """Raised when fetched candle data fails structural validation."""


def _classify_transport_failure(exc: httpx.HTTPError) -> str:
    """
    Classify a transport-level httpx failure into a short, safe category
    label (no credentials, no query parameters, no full URLs).

    httpx does not expose dedicated DNS/SSL exception classes: both
    surface as `httpx.ConnectError`, wrapping the real cause (a
    `socket.gaierror` for DNS, an `ssl.SSLError`/`ssl.SSLCertVerificationError`
    for TLS failures) via `exc.__cause__`. Timeouts are distinguished by
    httpx's own exception subclasses.
    """
    cause = exc.__cause__
    cause_type_name = type(cause).__name__ if cause is not None else ""

    if isinstance(exc, httpx.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, httpx.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, httpx.WriteTimeout):
        return "write_timeout"
    if isinstance(exc, httpx.PoolTimeout):
        return "pool_timeout"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"

    if isinstance(exc, httpx.ConnectError):
        if "gaierror" in cause_type_name or "getaddrinfo" in str(cause).lower():
            return "dns_failure"
        if "SSL" in cause_type_name or "ssl" in str(cause).lower():
            return "ssl_error"
        return "connect_error"

    if isinstance(exc, httpx.ProxyError):
        return "proxy_error"
    if isinstance(exc, httpx.NetworkError):
        return "network_error"
    if isinstance(exc, httpx.ProtocolError):
        return "protocol_error"

    return "request_error"


def _build_default_ssl_context() -> ssl.SSLContext:
    """
    Build the default TLS context used for outbound OKX connections.

    Certificate validation and hostname verification remain fully
    enforced; only the OpenSSL cipher/security level is relaxed from the
    Python default (SECLEVEL=2) to SECLEVEL=1. Some environments' default
    OpenSSL 3.x security level rejects cipher/key-exchange parameters
    that OKX's TLS endpoint offers, causing every request to fail at the
    TLS handshake with a generic connection-reset error before any HTTP
    exchange occurs. SECLEVEL=1 still requires TLS 1.2+ and authenticated
    encryption; it only widens the accepted cipher/key-size set.
    """
    context = ssl.create_default_context()
    try:
        context.set_ciphers("DEFAULT@SECLEVEL=1")
    except ssl.SSLError:
        # If the platform's OpenSSL build rejects this cipher string for
        # any reason, fall back to the interpreter's default context
        # rather than failing provider construction.
        pass
    return context


def _is_transient_transport_failure(exc: httpx.HTTPError) -> bool:
    """
    Return True for network-layer failures that are typically transient
    and safe to retry a bounded number of times: connection resets,
    dropped connections, and timeouts. DNS failures and SSL/certificate
    errors are treated as non-transient, since retrying them without a
    fixed underlying cause rarely helps and would only mask a
    configuration problem.
    """
    if isinstance(exc, _TRANSIENT_EXCEPTION_TYPES):
        return True

    if isinstance(exc, httpx.ConnectError):
        classification = _classify_transport_failure(exc)
        return classification == "connect_error"

    return False


def _describe_transport_failure(exc: httpx.HTTPError, *, endpoint_host: str) -> str:
    """
    Build a safe, structured diagnostic string for a transport-level
    httpx failure: exception class, timeout/connect/DNS/SSL
    classification, and the endpoint host only (no query string, no
    credentials, no full response body).
    """
    classification = _classify_transport_failure(exc)
    return (
        f"exception={type(exc).__name__} "
        f"classification={classification} "
        f"host={endpoint_host}"
    )


def _resolve_retry_after_seconds(response: httpx.Response) -> Optional[float]:
    """
    Parse a `Retry-After` value from an OKX HTTP response, Telegram-client
    style: prefer a numeric header value; otherwise fall back to None so
    the caller uses bounded exponential backoff instead. OKX's public
    market-data API returns Retry-After as a plain integer/float number
    of seconds (not an HTTP-date), so no date parsing is attempted.
    """
    header_value = response.headers.get("Retry-After")
    if header_value is None:
        return None
    try:
        return float(header_value)
    except ValueError:
        return None


class _AsyncRequestRateLimiter:
    """
    Shared async gate for all outbound OKX market-data requests.

    Combines two independent controls:
      - a concurrency semaphore, bounding how many requests may be
        in flight at once, independent of pair-scan concurrency;
      - a minimum-interval gate, ensuring consecutive requests (even
        sequential ones) are spaced at least `min_interval_seconds`
        apart, to smooth request bursts that would otherwise trigger
        OKX's rate limiting.

    A single instance is shared by one OKXMarketDataProvider across all
    symbols and timeframes in a scan cycle.
    """

    def __init__(self, *, max_concurrent_requests: int, min_interval_seconds: float) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._min_interval_seconds = min_interval_seconds
        self._lock = asyncio.Lock()
        self._last_request_monotonic: Optional[float] = None

    async def __aenter__(self) -> "_AsyncRequestRateLimiter":
        await self._semaphore.acquire()
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            if self._last_request_monotonic is not None:
                elapsed = now - self._last_request_monotonic
                wait_seconds = self._min_interval_seconds - elapsed
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)
                    now = loop.time()
            self._last_request_monotonic = now
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self._semaphore.release()


def _to_decimal(value: str, field_name: str) -> float:
    """Safely convert an exchange numeric string field to a float."""
    try:
        return float(Decimal(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MarketDataValidationError(
            f"Unable to convert field '{field_name}' value '{value}' to a number."
        ) from exc


def _raw_row_to_candle(row: list[str], symbol: str, timeframe: str) -> Candle:
    """Convert a single validated, completed OKX candle row into a Candle."""
    try:
        timestamp_ms = int(row[0])
    except (TypeError, ValueError) as exc:
        raise MarketDataValidationError(
            f"Unable to convert candle timestamp '{row[0]}' to an integer."
        ) from exc

    timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)

    return Candle(
        timestamp=timestamp,
        open=_to_decimal(row[1], "open"),
        high=_to_decimal(row[2], "high"),
        low=_to_decimal(row[3], "low"),
        close=_to_decimal(row[4], "close"),
        volume=_to_decimal(row[5], "volume"),
        symbol=symbol,
        timeframe=timeframe,
    )


class OKXMarketDataProvider:
    """
    Async client for fetching public candle market data from OKX.

    Supports dependency injection of an `httpx.AsyncClient` for testing.
    When no client is injected, an internal client is created and owned
    by this provider (and closed on context-manager exit).
    """

    def __init__(
        self,
        base_url: str,
        request_timeout_seconds: float,
        client: Optional[httpx.AsyncClient] = None,
        max_request_attempts: int = MAX_REQUEST_ATTEMPTS,
        retry_backoff_schedule_seconds: tuple[float, ...] = RETRY_BACKOFF_SCHEDULE_SECONDS,
        max_concurrent_requests: int = MAX_CONCURRENT_REQUESTS,
        min_request_interval_seconds: float = MIN_REQUEST_INTERVAL_SECONDS,
        inter_timeframe_delay_seconds: float = INTER_TIMEFRAME_DELAY_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._request_timeout_seconds = request_timeout_seconds
        self._owns_client = client is None
        self._max_request_attempts = max_request_attempts
        self._retry_backoff_schedule_seconds = retry_backoff_schedule_seconds
        self._inter_timeframe_delay_seconds = inter_timeframe_delay_seconds
        self._rate_limiter = _AsyncRequestRateLimiter(
            max_concurrent_requests=max_concurrent_requests,
            min_interval_seconds=min_request_interval_seconds,
        )
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=request_timeout_seconds,
            verify=_build_default_ssl_context(),
        )

    def _backoff_seconds_for_attempt(self, attempt: int) -> float:
        """attempt is 1-based; the schedule covers the wait before retry N."""
        index = min(attempt - 1, len(self._retry_backoff_schedule_seconds) - 1)
        return self._retry_backoff_schedule_seconds[index]

    async def _request_with_retry(
        self,
        endpoint: str,
        *,
        params: dict[str, str],
        symbol: str,
        timeframe: str,
        endpoint_host: str,
    ) -> tuple[httpx.Response, int]:
        """
        Perform a rate-limited GET request, retrying up to
        `max_request_attempts` total attempts for:
          - transient network-layer failures (connection resets, drops,
            connect/read timeouts);
          - HTTP 429 (using Retry-After when present, else bounded
            exponential backoff);
          - HTTP 500/502/503/504.

        Non-transient failures (DNS, SSL, malformed request) and
        permanent HTTP errors (400/401/403 and any other non-retryable
        status) are surfaced immediately, without retry.
        """
        last_exc: Optional[httpx.HTTPError] = None

        for attempt in range(1, self._max_request_attempts + 1):
            is_last_attempt = attempt == self._max_request_attempts

            try:
                async with self._rate_limiter:
                    response = await self._client.get(endpoint, params=params)
            except httpx.HTTPError as exc:
                last_exc = exc
                if is_last_attempt or not _is_transient_transport_failure(exc):
                    diagnostics = _describe_transport_failure(exc, endpoint_host=endpoint_host)
                    _logger.warning(
                        "OKX request failed symbol=%s timeframe=%s attempt=%d "
                        "exception=%s wait_seconds=0",
                        symbol,
                        timeframe,
                        attempt,
                        type(exc).__name__,
                    )
                    raise MarketDataRequestError(
                        f"HTTP request to OKX failed for {symbol} {timeframe} "
                        f"(endpoint={endpoint}, attempts={attempt}): {diagnostics}"
                    ) from exc

                wait_seconds = self._backoff_seconds_for_attempt(attempt)
                _logger.warning(
                    "OKX request failed symbol=%s timeframe=%s attempt=%d "
                    "exception=%s wait_seconds=%.1f",
                    symbol,
                    timeframe,
                    attempt,
                    type(exc).__name__,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)
                continue

            if response.status_code not in _RETRYABLE_HTTP_STATUS_CODES:
                return response, attempt

            if is_last_attempt:
                _logger.warning(
                    "OKX request failed symbol=%s timeframe=%s attempt=%d "
                    "status=%d wait_seconds=0",
                    symbol,
                    timeframe,
                    attempt,
                    response.status_code,
                )
                return response, attempt

            if response.status_code == 429:
                wait_seconds = _resolve_retry_after_seconds(response)
                if wait_seconds is None:
                    wait_seconds = self._backoff_seconds_for_attempt(attempt)
            else:
                wait_seconds = self._backoff_seconds_for_attempt(attempt)

            _logger.warning(
                "OKX request failed symbol=%s timeframe=%s attempt=%d "
                "status=%d wait_seconds=%.1f",
                symbol,
                timeframe,
                attempt,
                response.status_code,
                wait_seconds,
            )
            await asyncio.sleep(wait_seconds)

        # Unreachable: the loop above always returns or raises.
        raise MarketDataRequestError(
            f"HTTP request to OKX failed for {symbol} {timeframe} (endpoint={endpoint})."
        ) from last_exc

    async def __aenter__(self) -> "OKXMarketDataProvider":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_candles(
        self, symbol: str, timeframe: str, limit: int
    ) -> list[Candle]:
        """
        Fetch completed OHLCV candles for a symbol and internal timeframe.

        Returns candles in ascending chronological order, containing only
        completed candles (OKX confirm == "1").

        Raises:
            ValueError: If the symbol, timeframe, or limit are invalid.
            MarketDataRequestError: If the HTTP request fails.
            MarketDataResponseError: If the HTTP status or JSON body is invalid.
            MarketDataValidationError: If the response or resulting candle
                sequence fails structural validation.
        """
        validated_symbol = validate_pair_symbol(symbol)
        exchange_bar = get_exchange_timeframe(timeframe)

        if limit <= 0:
            raise ValueError(f"limit must be greater than zero, got {limit}.")
        if limit > MAX_CANDLE_LIMIT:
            raise ValueError(
                f"limit must not exceed {MAX_CANDLE_LIMIT}, got {limit}."
            )

        params = {"instId": validated_symbol, "bar": exchange_bar, "limit": str(limit)}

        endpoint_host = httpx.URL(self._base_url).host
        response, attempts = await self._request_with_retry(
            CANDLES_ENDPOINT,
            params=params,
            symbol=validated_symbol,
            timeframe=timeframe,
            endpoint_host=endpoint_host,
        )

        if response.status_code != 200:
            raise MarketDataResponseError(
                f"OKX returned HTTP status {response.status_code} for "
                f"{validated_symbol} {timeframe} (endpoint={CANDLES_ENDPOINT}, "
                f"host={endpoint_host}, attempts={attempts})."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise MarketDataResponseError(
                f"OKX response for {validated_symbol} {timeframe} is not valid JSON."
            ) from exc

        try:
            raw_rows = validate_okx_response(payload)
        except DataValidationError as exc:
            raise MarketDataValidationError(str(exc)) from exc

        completed_candles: list[Candle] = []
        for row in raw_rows:
            try:
                if not is_completed_candle(row):
                    continue
            except DataValidationError as exc:
                raise MarketDataValidationError(str(exc)) from exc

            completed_candles.append(
                _raw_row_to_candle(row, validated_symbol, timeframe)
            )

        # OKX returns newest-first; the project requires oldest-to-newest.
        completed_candles.sort(key=lambda candle: candle.timestamp)

        if completed_candles:
            try:
                validate_candle_sequence(completed_candles)
            except DataValidationError as exc:
                raise MarketDataValidationError(str(exc)) from exc

        return completed_candles

    async def fetch_multiple_timeframes(
        self, symbol: str, timeframes: list[str]
    ) -> dict[str, list[Candle]]:
        """
        Fetch candles for multiple internal timeframes concurrently.

        Required candle counts are read from REQUIRED_CANDLE_LIMITS. If any
        requested timeframe fails, an aggregated MarketDataError is raised
        and no partial result is returned.
        """

        async def _fetch(tf: str, start_delay_seconds: float) -> tuple[str, list[Candle]]:
            if start_delay_seconds > 0:
                await asyncio.sleep(start_delay_seconds)
            required_limit = REQUIRED_CANDLE_LIMITS.get(tf, MAX_CANDLE_LIMIT)
            request_limit = min(required_limit, MAX_CANDLE_LIMIT)
            candles = await self.fetch_candles(symbol, tf, request_limit)
            return tf, candles

        results = await asyncio.gather(
            *(
                _fetch(tf, index * self._inter_timeframe_delay_seconds)
                for index, tf in enumerate(timeframes)
            ),
            return_exceptions=True,
        )

        errors: list[str] = []
        candles_by_timeframe: dict[str, list[Candle]] = {}
        for tf, result in zip(timeframes, results):
            if isinstance(result, Exception):
                errors.append(f"{tf}: {result}")
            else:
                _, candles = result
                candles_by_timeframe[tf] = candles

        if errors:
            raise MarketDataError(
                f"Failed to fetch market data for symbol '{symbol}' on "
                f"{len(errors)} timeframe(s): {'; '.join(errors)}"
            )

        return candles_by_timeframe

    async def fetch_symbol_market_data(self, symbol: str) -> dict[str, list[Candle]]:
        """
        Fetch candles for the standard set of required timeframes
        (15m, 1h, 4h) for a single symbol.
        """
        return await self.fetch_multiple_timeframes(symbol, ["15m", "1h", "4h"])

    async def fetch_ticker_price(self, symbol: str) -> Optional[float]:
        """
        Fetch the latest traded price for a symbol from OKX's public
        ticker endpoint. Uses only public market data (no API keys).

        Returns None (rather than raising) on any request, response, or
        validation failure, so callers displaying a "current price" can
        safely fall back to an unavailable/placeholder state instead of
        fabricating a price.
        """
        try:
            validated_symbol = validate_pair_symbol(symbol)
        except ValueError:
            return None

        try:
            response = await self._client.get(
                TICKER_ENDPOINT, params={"instId": validated_symbol}
            )
        except httpx.HTTPError:
            return None

        if response.status_code != 200:
            return None

        try:
            payload = response.json()
        except ValueError:
            return None

        if not isinstance(payload, dict) or payload.get("code") != "0":
            return None

        data = payload.get("data")
        if not isinstance(data, list) or not data:
            return None

        last_price = data[0].get("last") if isinstance(data[0], dict) else None
        if last_price is None:
            return None

        try:
            return float(Decimal(str(last_price)))
        except (InvalidOperation, TypeError, ValueError):
            return None
