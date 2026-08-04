"""
Provides market data (candles) from Binance Futures' public v1 REST API
(USDT-M perpetual futures, fapi.binance.com).

This module only fetches, validates, and normalizes raw candle data into
the immutable Candle model. It does not perform any indicator
calculation or trading strategy analysis.
"""

import asyncio
import logging
import ssl
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx

from app.config.pairs import validate_pair_symbol, validate_pair_symbol_format
from app.config.timeframes import (
    REQUIRED_CANDLE_LIMITS,
    get_exchange_timeframe,
    get_timeframe_duration_seconds,
)
from app.data.candle_validator import DataValidationError, validate_candle_sequence
from app.data.market_data_errors import (
    MarketDataError,
    MarketDataRequestError,
    MarketDataResponseError,
    MarketDataValidationError,
)
from app.data.open_interest_point import OpenInterestPoint
from app.data.provider_base import MarketDataProvider
from app.data.ticker_snapshot import TickerSnapshot
from app.models.candle import Candle

KLINE_ENDPOINT = "/fapi/v1/klines"
TICKER_24HR_ENDPOINT = "/fapi/v1/ticker/24hr"
TICKER_PRICE_ENDPOINT = "/fapi/v1/ticker/price"
OPEN_INTEREST_ENDPOINT = "/fapi/v1/openInterest"
OPEN_INTEREST_HISTORY_ENDPOINT = "/futures/data/openInterestHist"
MAX_CANDLE_LIMIT = 1500
MAX_OPEN_INTEREST_LIMIT = 500

# Binance Futures' supported Open Interest statistics granularities
# (GET /futures/data/openInterestHist `period` parameter).
_VALID_OPEN_INTEREST_INTERVALS = frozenset(
    {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}
)

# Binance Futures kline row layout (a list of 12 fields):
# [openTime, open, high, low, close, volume, closeTime, quoteVolume,
#  numTrades, takerBuyBaseVolume, takerBuyQuoteVolume, ignore]
_MIN_KLINE_ROW_FIELDS = 7

# Request scheduling / retry tuning. These control only HTTP request
# timing and retry behaviour against Binance Futures' public market-data
# API; they never affect any strategy rule, threshold, or indicator
# calculation. Binance Futures rate-limits by request *weight* (1200/min
# on most /fapi/v1 endpoints) rather than raw concurrency, but a modest
# concurrency + spacing gate keeps this well under that ceiling without
# needing per-endpoint weight accounting.
MAX_REQUEST_ATTEMPTS = 3
RETRY_BACKOFF_SCHEDULE_SECONDS = (1.0, 2.0, 4.0)
MAX_CONCURRENT_REQUESTS = 8
MIN_REQUEST_INTERVAL_SECONDS = 0.05
INTER_TIMEFRAME_DELAY_SECONDS = 0.1
# Bounds per-symbol Open Interest fetches during dynamic pair discovery
# (one request per turnover-qualified candidate, run concurrently),
# separate from the per-request rate limiter above.
MAX_CONCURRENT_OPEN_INTEREST_REQUESTS = 10

_RETRYABLE_HTTP_STATUS_CODES = {418, 429, 500, 502, 503, 504}

# Binance signals a rate-limit/ban violation via HTTP 429 (rate limit)
# or 418 (IP auto-banned for repeated 429s), and application errors via
# a JSON body {"code": int, "msg": str} on non-2xx responses. There is
# no body-level retryable-error-code concept analogous to Bybit's
# retCode on an HTTP-200 response; a 200 response is always success.
_TRANSIENT_EXCEPTION_TYPES = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)

_logger = logging.getLogger(__name__)


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
    Build the default TLS context used for outbound Binance connections.

    Certificate validation and hostname verification remain fully
    enforced; only the OpenSSL cipher/security level is relaxed from the
    Python default (SECLEVEL=2) to SECLEVEL=1. Some environments' default
    OpenSSL 3.x security level rejects cipher/key-exchange parameters
    that an exchange's TLS endpoint offers, causing every request to
    fail at the TLS handshake with a generic connection-reset error
    before any HTTP exchange occurs. SECLEVEL=1 still requires TLS 1.2+
    and authenticated encryption; it only widens the accepted
    cipher/key-size set.
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
    Parse a `Retry-After` value from a Binance HTTP response: prefer a
    numeric header value; otherwise fall back to None so the caller uses
    bounded exponential backoff instead.
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
    Shared async gate for all outbound Binance Futures market-data
    requests.

    Combines two independent controls:
      - a concurrency semaphore, bounding how many requests may be
        in flight at once, independent of pair-scan concurrency;
      - a minimum-interval gate, ensuring consecutive requests (even
        sequential ones) are spaced at least `min_interval_seconds`
        apart, to smooth request bursts that would otherwise trigger
        Binance's rate limiting.

    A single instance is shared by one BinanceFuturesMarketDataProvider
    across all symbols and timeframes in a scan cycle.
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


def _to_decimal(value: object, field_name: str) -> float:
    """Safely convert an exchange numeric field to a float."""
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MarketDataValidationError(
            f"Unable to convert field '{field_name}' value '{value}' to a number."
        ) from exc


def _to_optional_float(value: object) -> Optional[float]:
    """Best-effort numeric coercion: returns None for missing/malformed values, never raises."""
    if value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def validate_binance_klines_response(payload: object) -> list[list[object]]:
    """
    Validate the structure of a raw Binance Futures klines response
    payload.

    Requirements:
        - The payload must be a list (Binance returns the row array
          directly, with no success/error envelope on a 2xx response).
        - Every row must be a list with at least seven fields (openTime,
          open, high, low, close, volume, closeTime).

    Returns:
        The validated list of raw kline rows.

    Raises:
        DataValidationError: If any structural requirement is not met.
    """
    if not isinstance(payload, list):
        raise DataValidationError(
            f"Binance klines response payload must be a list, got {type(payload).__name__}."
        )

    validated_rows: list[list[object]] = []
    for index, row in enumerate(payload):
        if not isinstance(row, list):
            raise DataValidationError(
                f"Binance kline row at index {index} must be a list, got {type(row).__name__}."
            )
        if len(row) < _MIN_KLINE_ROW_FIELDS:
            raise DataValidationError(
                f"Binance kline row at index {index} has {len(row)} fields; "
                f"expected at least {_MIN_KLINE_ROW_FIELDS}."
            )
        validated_rows.append(row)

    return validated_rows


def is_completed_kline(raw_row: list[object], *, interval_seconds: int, now_utc: datetime) -> bool:
    """
    Determine whether a raw Binance Futures kline row represents a fully
    closed candle.

    Binance's klines endpoint includes an explicit `closeTime` (field
    index 6, milliseconds), so completeness is a direct comparison
    rather than an inferred one: a candle is only fully closed once its
    `closeTime <= now_utc`.

    Raises:
        DataValidationError: If the row's closeTime field is missing or
            not a valid integer millisecond timestamp.
    """
    if len(raw_row) < _MIN_KLINE_ROW_FIELDS:
        raise DataValidationError(
            f"Kline row has {len(raw_row)} fields; expected at least {_MIN_KLINE_ROW_FIELDS}."
        )

    try:
        close_time_ms = int(raw_row[6])
    except (TypeError, ValueError) as exc:
        raise DataValidationError(
            f"Unable to convert kline closeTime '{raw_row[6]}' to an integer."
        ) from exc

    return close_time_ms / 1000 <= now_utc.timestamp()


def _raw_row_to_candle(row: list[object], symbol: str, timeframe: str) -> Candle:
    """Convert a single validated, completed Binance Futures kline row into a Candle."""
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


def _to_binance_symbol(validated_symbol: str) -> str:
    """
    Convert an internal hyphenated pair symbol ("BTC-USDT") into
    Binance's USDT-M perpetual instrument naming convention ("BTCUSDT").
    Internal pair strings stay hyphenated everywhere else in the
    application (config, signals, dashboard API, frontend); only
    outbound Binance HTTP requests use the no-hyphen form.
    """
    return validated_symbol.replace("-", "")


def _from_binance_symbol(binance_symbol: str) -> Optional[str]:
    """
    Convert a Binance USDT-M perpetual instrument name ("BTCUSDT") back
    into the application's internal hyphenated form ("BTC-USDT").

    Returns None for any symbol that isn't a USDT-quoted perpetual (e.g.
    BUSD- or other quote-currency instruments Binance's bulk tickers
    endpoint may also return), since this application only trades USDT
    perpetuals.
    """
    if not binance_symbol.endswith("USDT") or binance_symbol == "USDT":
        return None
    base = binance_symbol[: -len("USDT")]
    if not base:
        return None
    return f"{base}-USDT"


def _parse_ticker_24hr_row(row: object) -> Optional[tuple[str, Optional[float], Optional[float]]]:
    """
    Parse a single row from Binance's bulk 24hr ticker response into
    (internal_symbol, turnover_24h_usdt, last_price), or None if the row
    isn't a USDT-perpetual pair this application tracks. Never raises;
    individually malformed rows are skipped rather than failing the
    whole batch.

    `quoteVolume` is Binance's 24h turnover already denominated in the
    quote asset (USDT for a USDT-M pair), matching this application's
    `turnover_24h_usdt` field directly.
    """
    if not isinstance(row, dict):
        return None

    binance_symbol = row.get("symbol")
    if not isinstance(binance_symbol, str):
        return None

    symbol = _from_binance_symbol(binance_symbol)
    if symbol is None:
        return None

    try:
        validated_symbol = validate_pair_symbol_format(symbol)
    except ValueError:
        return None

    turnover_24h_usdt = _to_optional_float(row.get("quoteVolume"))
    last_price = _to_optional_float(row.get("lastPrice"))
    return validated_symbol, turnover_24h_usdt, last_price


class BinanceFuturesMarketDataProvider(MarketDataProvider):
    """
    Async client for fetching public candle and Open Interest market
    data from Binance Futures' v1 API, for USDT-M perpetual futures
    (fapi.binance.com).

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
        max_concurrent_open_interest_requests: int = MAX_CONCURRENT_OPEN_INTEREST_REQUESTS,
        validate_symbol_against_allow_list: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._request_timeout_seconds = request_timeout_seconds
        self._owns_client = client is None
        self._max_request_attempts = max_request_attempts
        self._retry_backoff_schedule_seconds = retry_backoff_schedule_seconds
        self._inter_timeframe_delay_seconds = inter_timeframe_delay_seconds
        self._open_interest_semaphore = asyncio.Semaphore(max_concurrent_open_interest_requests)
        # Symbol validation for outbound requests. Format-only validation
        # (no allow-list check) is used by the dynamic-pair-discovery
        # warm-up provider (app.scanner.pair_discovery.PairWarmUpTracker):
        # a warm-up fetch's whole purpose is to test a symbol *before* it
        # is added to get_configured_pairs(), so checking it against
        # that same list here would be circular and would always reject
        # it. The live-scan provider (used for symbols already in
        # rotation) keeps the full allow-list check.
        self._validate_symbol = (
            validate_pair_symbol if validate_symbol_against_allow_list else validate_pair_symbol_format
        )
        self._rate_limiter = _AsyncRequestRateLimiter(
            max_concurrent_requests=max_concurrent_requests,
            min_interval_seconds=min_request_interval_seconds,
        )
        # Process-lifetime counter of extra attempts beyond the first for
        # any request (i.e. total retries, not total requests). Read-only
        # observability for cycle-level logging (see
        # MultiPairScanScheduler.run_cycle); never read by any retry or
        # rate-limit decision itself.
        self._total_retry_count = 0

        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=request_timeout_seconds,
            verify=_build_default_ssl_context(),
        )

    def _backoff_seconds_for_attempt(self, attempt: int) -> float:
        """attempt is 1-based; the schedule covers the wait before retry N."""
        index = min(attempt - 1, len(self._retry_backoff_schedule_seconds) - 1)
        return self._retry_backoff_schedule_seconds[index]

    def _is_retryable_response(self, response: httpx.Response) -> bool:
        """
        Binance signals rate-limiting/bans via HTTP status codes only
        (429 rate-limited, 418 IP auto-banned) alongside the standard
        5xx codes; unlike Bybit there is no body-level retryable-error
        concept on an HTTP-200 response.
        """
        return response.status_code in _RETRYABLE_HTTP_STATUS_CODES

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
          - HTTP 418 (IP auto-ban signal) and 500/502/503/504.

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
                        "Binance request failed symbol=%s timeframe=%s attempt=%d "
                        "exception=%s wait_seconds=0",
                        symbol,
                        timeframe,
                        attempt,
                        type(exc).__name__,
                    )
                    raise MarketDataRequestError(
                        f"HTTP request to Binance failed for {symbol} {timeframe} "
                        f"(endpoint={endpoint}, attempts={attempt}): {diagnostics}"
                    ) from exc

                wait_seconds = self._backoff_seconds_for_attempt(attempt)
                _logger.warning(
                    "Binance request failed symbol=%s timeframe=%s attempt=%d "
                    "exception=%s wait_seconds=%.1f",
                    symbol,
                    timeframe,
                    attempt,
                    type(exc).__name__,
                    wait_seconds,
                )
                self._total_retry_count += 1
                await asyncio.sleep(wait_seconds)
                continue

            if not self._is_retryable_response(response):
                return response, attempt

            if is_last_attempt:
                _logger.warning(
                    "Binance request failed symbol=%s timeframe=%s attempt=%d "
                    "status=%d wait_seconds=0",
                    symbol,
                    timeframe,
                    attempt,
                    response.status_code,
                )
                return response, attempt

            if response.status_code in (429, 418):
                wait_seconds = _resolve_retry_after_seconds(response)
                if wait_seconds is None:
                    wait_seconds = self._backoff_seconds_for_attempt(attempt)
            else:
                wait_seconds = self._backoff_seconds_for_attempt(attempt)

            _logger.warning(
                "Binance request failed symbol=%s timeframe=%s attempt=%d "
                "status=%d wait_seconds=%.1f",
                symbol,
                timeframe,
                attempt,
                response.status_code,
                wait_seconds,
            )
            self._total_retry_count += 1
            await asyncio.sleep(wait_seconds)

        # Unreachable: the loop above always returns or raises.
        raise MarketDataRequestError(
            f"HTTP request to Binance failed for {symbol} {timeframe} (endpoint={endpoint})."
        ) from last_exc

    @property
    def total_retry_count(self) -> int:
        """Process-lifetime count of extra request attempts beyond the first, across all requests."""
        return self._total_retry_count

    async def __aenter__(self) -> "BinanceFuturesMarketDataProvider":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        """
        Fetch completed OHLCV candles for a symbol and internal timeframe.

        Returns candles in ascending chronological order, containing only
        fully closed candles (Binance's `closeTime` field makes this a
        direct comparison rather than an inferred one).

        Raises:
            ValueError: If the symbol, timeframe, or limit are invalid.
            MarketDataRequestError: If the HTTP request fails.
            MarketDataResponseError: If the HTTP status or JSON body is invalid.
            MarketDataValidationError: If the response or resulting candle
                sequence fails structural validation.
        """
        validated_symbol = self._validate_symbol(symbol)
        binance_symbol = _to_binance_symbol(validated_symbol)
        interval = get_exchange_timeframe(timeframe)
        interval_seconds = get_timeframe_duration_seconds(timeframe)

        if limit <= 0:
            raise ValueError(f"limit must be greater than zero, got {limit}.")
        if limit > MAX_CANDLE_LIMIT:
            raise ValueError(f"limit must not exceed {MAX_CANDLE_LIMIT}, got {limit}.")

        params = {
            "symbol": binance_symbol,
            "interval": interval,
            "limit": str(limit),
        }

        endpoint_host = httpx.URL(self._base_url).host
        response, attempts = await self._request_with_retry(
            KLINE_ENDPOINT,
            params=params,
            symbol=validated_symbol,
            timeframe=timeframe,
            endpoint_host=endpoint_host,
        )

        if response.status_code != 200:
            raise MarketDataResponseError(
                f"Binance returned HTTP status {response.status_code} for "
                f"{validated_symbol} {timeframe} (endpoint={KLINE_ENDPOINT}, "
                f"host={endpoint_host}, attempts={attempts})."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise MarketDataResponseError(
                f"Binance response for {validated_symbol} {timeframe} is not valid JSON."
            ) from exc

        try:
            raw_rows = validate_binance_klines_response(payload)
        except DataValidationError as exc:
            raise MarketDataValidationError(str(exc)) from exc

        now_utc = datetime.now(timezone.utc)
        completed_candles: list[Candle] = []
        for row in raw_rows:
            try:
                if not is_completed_kline(row, interval_seconds=interval_seconds, now_utc=now_utc):
                    continue
            except DataValidationError as exc:
                raise MarketDataValidationError(str(exc)) from exc

            completed_candles.append(_raw_row_to_candle(row, validated_symbol, timeframe))

        # Binance already returns klines oldest-first (ascending by
        # openTime), but sort defensively rather than assuming ordering
        # is guaranteed to never change.
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
        Fetch the latest traded price for a symbol from Binance Futures'
        public ticker endpoint. Uses only public market data (no API keys).

        Returns None (rather than raising) on any request, response, or
        validation failure, so callers displaying a "current price" can
        safely fall back to an unavailable/placeholder state instead of
        fabricating a price.
        """
        try:
            validated_symbol = self._validate_symbol(symbol)
        except ValueError:
            return None

        binance_symbol = _to_binance_symbol(validated_symbol)

        try:
            response = await self._client.get(
                TICKER_PRICE_ENDPOINT, params={"symbol": binance_symbol}
            )
        except httpx.HTTPError:
            return None

        if response.status_code != 200:
            return None

        try:
            payload = response.json()
        except ValueError:
            return None

        if not isinstance(payload, dict):
            return None

        return _to_optional_float(payload.get("price"))

    async def fetch_all_linear_tickers(self) -> list[TickerSnapshot]:
        """
        Fetch 24h turnover for every USDT-M perpetual futures pair from
        Binance's public bulk 24hr ticker endpoint (one call returns
        every instrument at once), then fetch Open Interest individually
        for each turnover-qualifying symbol and combine the two into
        Open Interest expressed in USDT, for dynamic liquidity/OI-based
        coin discovery.

        Unlike Bybit, Binance Futures has no single bulk endpoint that
        returns Open Interest value for every symbol; Open Interest is
        only available per-symbol (in contracts, not USDT) via
        `/fapi/v1/openInterest`. This method fetches Open Interest only
        for symbols whose 24h turnover is already non-None (the OI
        filter never passes a symbol whose turnover is unknown anyway),
        bounding the number of extra per-symbol requests to the
        candidate set rather than the full exchange symbol universe.

        Uses only public market data (no API keys). Never raises: on
        any request, response, or validation failure this returns an
        empty list so callers can safely fall back to a previously
        known-good pair list. Rows for symbols that aren't USDT-quoted
        perpetuals, or that are missing/malformed turnover fields, are
        skipped individually rather than failing the whole batch.
        """
        try:
            response = await self._client.get(TICKER_24HR_ENDPOINT)
        except httpx.HTTPError:
            return []

        if response.status_code != 200:
            return []

        try:
            payload = response.json()
        except ValueError:
            return []

        if not isinstance(payload, list):
            return []

        candidates: list[tuple[str, float, Optional[float]]] = []
        for row in payload:
            parsed = _parse_ticker_24hr_row(row)
            if parsed is None:
                continue
            symbol, turnover_24h_usdt, last_price = parsed
            if turnover_24h_usdt is None:
                continue
            candidates.append((symbol, turnover_24h_usdt, last_price))

        open_interest_by_symbol = await self._fetch_open_interest_usdt_for_symbols(candidates)

        snapshots: list[TickerSnapshot] = []
        for symbol, turnover_24h_usdt, _ in candidates:
            snapshots.append(
                TickerSnapshot(
                    symbol=symbol,
                    open_interest_usdt=open_interest_by_symbol.get(symbol),
                    turnover_24h_usdt=turnover_24h_usdt,
                )
            )
        return snapshots

    async def fetch_all_ticker_prices(self) -> dict[str, float]:
        """
        Fetch the latest traded price for every symbol from Binance's
        public bulk price-ticker endpoint (calling it with no `symbol`
        query parameter returns every instrument in one response), for
        cheap dashboard-only "live price" display.

        Never raises: on any request, response, or validation failure
        this returns an empty dict. Individually malformed rows, or
        rows for a non-USDT-perpetual instrument, are skipped rather
        than failing the whole batch.
        """
        try:
            response = await self._client.get(TICKER_PRICE_ENDPOINT)
        except httpx.HTTPError:
            return {}

        if response.status_code != 200:
            return {}

        try:
            payload = response.json()
        except ValueError:
            return {}

        if not isinstance(payload, list):
            return {}

        prices: dict[str, float] = {}
        for row in payload:
            if not isinstance(row, dict):
                continue
            binance_symbol = row.get("symbol")
            if not isinstance(binance_symbol, str):
                continue
            symbol = _from_binance_symbol(binance_symbol)
            if symbol is None:
                continue
            try:
                validated_symbol = validate_pair_symbol_format(symbol)
            except ValueError:
                continue
            price = _to_optional_float(row.get("price"))
            if price is None:
                continue
            prices[validated_symbol] = price
        return prices

    async def _fetch_open_interest_usdt_for_symbols(
        self, candidates: list[tuple[str, float, Optional[float]]]
    ) -> dict[str, float]:
        """
        Fetch Open Interest (in contracts) for each candidate symbol
        concurrently (bounded by `_open_interest_semaphore`), and convert
        to an approximate USDT value using each symbol's last traded
        price from the same 24hr ticker batch. A symbol whose Open
        Interest fetch fails, or whose last price is unavailable, is
        simply omitted from the returned mapping rather than failing the
        whole batch.
        """

        async def _fetch_one(symbol: str, last_price: Optional[float]) -> tuple[str, Optional[float]]:
            if last_price is None:
                return symbol, None
            async with self._open_interest_semaphore:
                open_interest_contracts = await self._fetch_open_interest_contracts(symbol)
            if open_interest_contracts is None:
                return symbol, None
            return symbol, open_interest_contracts * last_price

        results = await asyncio.gather(
            *(_fetch_one(symbol, last_price) for symbol, _, last_price in candidates)
        )
        return {symbol: value for symbol, value in results if value is not None}

    async def _fetch_open_interest_contracts(self, validated_symbol: str) -> Optional[float]:
        """
        Fetch the current Open Interest (in contracts, not USDT) for a
        single symbol from `/fapi/v1/openInterest`. Never raises;
        returns None on any request/response/validation failure.
        """
        binance_symbol = _to_binance_symbol(validated_symbol)
        try:
            response = await self._client.get(
                OPEN_INTEREST_ENDPOINT, params={"symbol": binance_symbol}
            )
        except httpx.HTTPError:
            return None

        if response.status_code != 200:
            return None

        try:
            payload = response.json()
        except ValueError:
            return None

        if not isinstance(payload, dict):
            return None

        return _to_optional_float(payload.get("openInterest"))

    async def fetch_open_interest_history(
        self, symbol: str, interval: str, limit: int
    ) -> list[OpenInterestPoint]:
        """
        Fetch a recent Open Interest time series for a single symbol
        from Binance Futures' public Open Interest statistics endpoint,
        for Open Interest Confirmation.

        `interval` must be one of Binance's supported granularities
        ("5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"). Uses
        only public market data (no API keys). Never raises: on any
        request, response, or validation failure -- including an
        unsupported `interval` or non-positive `limit` -- this returns
        an empty list so callers can treat OI confirmation as
        unavailable rather than fabricating a rising/falling signal.
        Returned points are in ascending chronological order (Binance
        already returns oldest-first).
        """
        if interval not in _VALID_OPEN_INTEREST_INTERVALS:
            return []
        if limit <= 0 or limit > MAX_OPEN_INTEREST_LIMIT:
            return []

        try:
            validated_symbol = self._validate_symbol(symbol)
        except ValueError:
            return []

        binance_symbol = _to_binance_symbol(validated_symbol)

        try:
            response = await self._client.get(
                OPEN_INTEREST_HISTORY_ENDPOINT,
                params={"symbol": binance_symbol, "period": interval, "limit": str(limit)},
            )
        except httpx.HTTPError:
            return []

        if response.status_code != 200:
            return []

        try:
            payload = response.json()
        except ValueError:
            return []

        if not isinstance(payload, list):
            return []

        points: list[OpenInterestPoint] = []
        for row in payload:
            point = _parse_open_interest_history_row(row, validated_symbol)
            if point is not None:
                points.append(point)

        points.sort(key=lambda p: p.timestamp)
        return points


def _parse_open_interest_history_row(row: object, symbol: str) -> Optional[OpenInterestPoint]:
    """
    Parse a single row from Binance's Open Interest history response
    into an OpenInterestPoint. Never raises; a malformed row is skipped
    rather than failing the whole batch.

    Binance's `sumOpenInterestValue` field is already denominated in the
    quote asset (USDT for a USDT-M pair), matching this application's
    `open_interest` field (expected in USDT) directly -- no
    contracts-to-USDT conversion is needed here, unlike the current
    per-symbol snapshot used by `fetch_all_linear_tickers`.
    """
    if not isinstance(row, dict):
        return None

    open_interest = _to_optional_float(row.get("sumOpenInterestValue"))
    if open_interest is None:
        return None

    timestamp_ms = row.get("timestamp")
    try:
        timestamp = datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None

    try:
        return OpenInterestPoint(symbol=symbol, timestamp=timestamp, open_interest=open_interest)
    except ValueError:
        return None
