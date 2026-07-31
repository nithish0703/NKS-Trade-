"""
Provides market data (candles) from Bybit's public v5 REST API.

This module implements a Bybit public-market-data REST client for USDT
perpetual futures (category=linear). It only fetches, validates, and
normalizes raw candle data into the immutable Candle model. It does not
perform any indicator calculation or trading strategy analysis.
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
from app.data.bybit_data_validator import (
    DataValidationError,
    is_completed_kline,
    validate_bybit_response,
    validate_candle_sequence,
)
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

KLINE_ENDPOINT = "/v5/market/kline"
TICKER_ENDPOINT = "/v5/market/tickers"
OPEN_INTEREST_ENDPOINT = "/v5/market/open-interest"
MAX_CANDLE_LIMIT = 1000
MAX_OPEN_INTEREST_LIMIT = 200
LINEAR_CATEGORY = "linear"

# Bybit's supported Open Interest history granularities
# (GET /v5/market/open-interest `intervalTime` parameter).
_VALID_OPEN_INTEREST_INTERVALS = frozenset({"5min", "15min", "30min", "1h", "4h", "1d"})

# Request scheduling / retry tuning. These control only HTTP request
# timing and retry behaviour against Bybit's public market-data API;
# they never affect any strategy rule, threshold, or indicator
# calculation.
MAX_REQUEST_ATTEMPTS = 3
RETRY_BACKOFF_SCHEDULE_SECONDS = (1.0, 2.0, 4.0)
MAX_CONCURRENT_REQUESTS = 4
MIN_REQUEST_INTERVAL_SECONDS = 0.1
INTER_TIMEFRAME_DELAY_SECONDS = 0.2

_RETRYABLE_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}

# Bybit signals rate-limiting and some application errors via a non-zero
# retCode in an HTTP-200 body, in addition to (sometimes instead of) an
# HTTP-level status code. These retCodes are treated exactly like the
# retryable HTTP statuses above.
_RETRYABLE_RET_CODES = {
    10002,  # request not received within recvWindow
    10006,  # too many visits, exceeded the API rate limit
    10018,  # exceeded the IP rate limit
}
_SUCCESS_RET_CODE = 0

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
    Build the default TLS context used for outbound Bybit connections.

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
    Parse a `Retry-After` value from a Bybit HTTP response: prefer a
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


def _extract_ret_code(response: httpx.Response) -> Optional[int]:
    """
    Best-effort extraction of Bybit's body-level `retCode`, without
    raising: returns None if the body isn't JSON or lacks the field.
    """
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    ret_code = payload.get("retCode")
    return ret_code if isinstance(ret_code, int) else None


class _AsyncRequestRateLimiter:
    """
    Shared async gate for all outbound Bybit market-data requests.

    Combines two independent controls:
      - a concurrency semaphore, bounding how many requests may be
        in flight at once, independent of pair-scan concurrency;
      - a minimum-interval gate, ensuring consecutive requests (even
        sequential ones) are spaced at least `min_interval_seconds`
        apart, to smooth request bursts that would otherwise trigger
        Bybit's rate limiting.

    A single instance is shared by one BybitMarketDataProvider across
    all symbols and timeframes in a scan cycle.
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
    """Convert a single validated, completed Bybit kline row into a Candle."""
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


def _to_bybit_symbol(validated_symbol: str) -> str:
    """
    Convert an internal hyphenated pair symbol ("BTC-USDT") into Bybit's
    USDT-perpetual instrument naming convention ("BTCUSDT"). Internal
    pair strings stay hyphenated everywhere else in the application
    (config, signals, dashboard API, frontend); only outbound Bybit HTTP
    requests use the no-hyphen form.
    """
    return validated_symbol.replace("-", "")


def _from_bybit_symbol(bybit_symbol: str) -> Optional[str]:
    """
    Convert a Bybit USDT-perpetual instrument name ("BTCUSDT") back into
    the application's internal hyphenated form ("BTC-USDT").

    Returns None for any symbol that isn't a USDT-quoted linear
    perpetual (e.g. USDC- or other quote-currency instruments Bybit's
    bulk tickers endpoint may also return), since this application only
    trades USDT perpetuals.
    """
    if not bybit_symbol.endswith("USDT") or bybit_symbol == "USDT":
        return None
    base = bybit_symbol[: -len("USDT")]
    if not base:
        return None
    return f"{base}-USDT"


def _to_optional_float(value: object) -> Optional[float]:
    """Best-effort numeric coercion: returns None for missing/malformed values, never raises."""
    if value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _parse_open_interest_row(row: object, symbol: str) -> Optional[OpenInterestPoint]:
    """
    Parse a single row from Bybit's Open Interest history response into
    an OpenInterestPoint. Never raises; a malformed row is skipped
    rather than failing the whole batch.
    """
    if not isinstance(row, dict):
        return None

    open_interest = _to_optional_float(row.get("openInterest"))
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


def _parse_ticker_row(row: object) -> Optional[TickerSnapshot]:
    """
    Parse a single row from Bybit's bulk tickers response into a
    TickerSnapshot, or None if the row isn't a USDT-perpetual pair this
    application tracks. Never raises; individually malformed rows are
    skipped rather than failing the whole batch.
    """
    if not isinstance(row, dict):
        return None

    bybit_symbol = row.get("symbol")
    if not isinstance(bybit_symbol, str):
        return None

    symbol = _from_bybit_symbol(bybit_symbol)
    if symbol is None:
        return None

    try:
        validated_symbol = validate_pair_symbol_format(symbol)
    except ValueError:
        return None

    return TickerSnapshot(
        symbol=validated_symbol,
        open_interest_usdt=_to_optional_float(row.get("openInterestValue")),
        turnover_24h_usdt=_to_optional_float(row.get("turnover24h")),
    )


class BybitMarketDataProvider(MarketDataProvider):
    """
    Async client for fetching public candle market data from Bybit's v5
    API, for USDT perpetual futures (category=linear).

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
        validate_symbol_against_allow_list: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._request_timeout_seconds = request_timeout_seconds
        self._owns_client = client is None
        self._max_request_attempts = max_request_attempts
        self._retry_backoff_schedule_seconds = retry_backoff_schedule_seconds
        self._inter_timeframe_delay_seconds = inter_timeframe_delay_seconds
        # Symbol validation for outbound requests. Format-only validation
        # (no allow-list check) is used by the dynamic-pair-discovery
        # warm-up provider (app.scanner.pair_discovery.PairWarmUpTracker):
        # a warm-up fetch's whole purpose is to test a symbol *before* it
        # is added to the configured pair list, so checking it against
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
        Bybit signals rate-limiting and some application errors via
        either an HTTP status code (429/5xx) or, with an HTTP-200
        response, a non-zero body-level `retCode`. Both layers must be
        checked; HTTP status alone is not sufficient.
        """
        if response.status_code in _RETRYABLE_HTTP_STATUS_CODES:
            return True
        if response.status_code == 200:
            ret_code = _extract_ret_code(response)
            return ret_code is not None and ret_code in _RETRYABLE_RET_CODES
        return False

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
          - HTTP 500/502/503/504;
          - HTTP 200 responses carrying a retryable body-level retCode
            (Bybit rate-limit signaling).

        Non-transient failures (DNS, SSL, malformed request) and
        permanent HTTP errors (400/401/403 and any other non-retryable
        status/retCode) are surfaced immediately, without retry.
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
                        "Bybit request failed symbol=%s timeframe=%s attempt=%d "
                        "exception=%s wait_seconds=0",
                        symbol,
                        timeframe,
                        attempt,
                        type(exc).__name__,
                    )
                    raise MarketDataRequestError(
                        f"HTTP request to Bybit failed for {symbol} {timeframe} "
                        f"(endpoint={endpoint}, attempts={attempt}): {diagnostics}"
                    ) from exc

                wait_seconds = self._backoff_seconds_for_attempt(attempt)
                _logger.warning(
                    "Bybit request failed symbol=%s timeframe=%s attempt=%d "
                    "exception=%s wait_seconds=%.1f",
                    symbol,
                    timeframe,
                    attempt,
                    type(exc).__name__,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)
                continue

            if not self._is_retryable_response(response):
                return response, attempt

            if is_last_attempt:
                _logger.warning(
                    "Bybit request failed symbol=%s timeframe=%s attempt=%d "
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
                "Bybit request failed symbol=%s timeframe=%s attempt=%d "
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
            f"HTTP request to Bybit failed for {symbol} {timeframe} (endpoint={endpoint})."
        ) from last_exc

    async def __aenter__(self) -> "BybitMarketDataProvider":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        """
        Fetch completed OHLCV candles for a symbol and internal timeframe.

        Returns candles in ascending chronological order, containing only
        fully closed candles (Bybit carries no explicit "closed" flag;
        completeness is inferred from `startTime + interval duration`).

        Raises:
            ValueError: If the symbol, timeframe, or limit are invalid.
            MarketDataRequestError: If the HTTP request fails.
            MarketDataResponseError: If the HTTP status or JSON body is invalid.
            MarketDataValidationError: If the response or resulting candle
                sequence fails structural validation.
        """
        validated_symbol = self._validate_symbol(symbol)
        bybit_symbol = _to_bybit_symbol(validated_symbol)
        interval = get_exchange_timeframe(timeframe)
        interval_seconds = get_timeframe_duration_seconds(timeframe)

        if limit <= 0:
            raise ValueError(f"limit must be greater than zero, got {limit}.")
        if limit > MAX_CANDLE_LIMIT:
            raise ValueError(f"limit must not exceed {MAX_CANDLE_LIMIT}, got {limit}.")

        params = {
            "category": LINEAR_CATEGORY,
            "symbol": bybit_symbol,
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
                f"Bybit returned HTTP status {response.status_code} for "
                f"{validated_symbol} {timeframe} (endpoint={KLINE_ENDPOINT}, "
                f"host={endpoint_host}, attempts={attempts})."
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise MarketDataResponseError(
                f"Bybit response for {validated_symbol} {timeframe} is not valid JSON."
            ) from exc

        if isinstance(payload, dict) and payload.get("retCode") != _SUCCESS_RET_CODE:
            raise MarketDataResponseError(
                f"Bybit returned retCode {payload.get('retCode')} for "
                f"{validated_symbol} {timeframe} (endpoint={KLINE_ENDPOINT}, "
                f"host={endpoint_host}, attempts={attempts})."
            )

        try:
            raw_rows = validate_bybit_response(payload)
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

        # Bybit returns klines newest-first (descending by startTime);
        # the project requires oldest-to-newest.
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
        Fetch the latest traded price for a symbol from Bybit's public
        ticker endpoint. Uses only public market data (no API keys).

        Returns None (rather than raising) on any request, response, or
        validation failure, so callers displaying a "current price" can
        safely fall back to an unavailable/placeholder state instead of
        fabricating a price.
        """
        try:
            validated_symbol = self._validate_symbol(symbol)
        except ValueError:
            return None

        bybit_symbol = _to_bybit_symbol(validated_symbol)

        try:
            response = await self._client.get(
                TICKER_ENDPOINT, params={"category": LINEAR_CATEGORY, "symbol": bybit_symbol}
            )
        except httpx.HTTPError:
            return None

        if response.status_code != 200:
            return None

        try:
            payload = response.json()
        except ValueError:
            return None

        if not isinstance(payload, dict) or payload.get("retCode") != _SUCCESS_RET_CODE:
            return None

        result = payload.get("result")
        if not isinstance(result, dict):
            return None

        ticker_list = result.get("list")
        if not isinstance(ticker_list, list) or not ticker_list:
            return None

        first = ticker_list[0]
        last_price = first.get("lastPrice") if isinstance(first, dict) else None
        if last_price is None:
            return None

        try:
            return float(Decimal(str(last_price)))
        except (InvalidOperation, TypeError, ValueError):
            return None

    async def fetch_all_linear_tickers(self) -> list[TickerSnapshot]:
        """
        Fetch Open Interest and 24h turnover for every USDT perpetual
        futures pair from Bybit's public bulk tickers endpoint
        (category=linear, no symbol filter -- one call returns every
        instrument at once).

        Uses only public market data (no API keys). Never raises: on
        any request, response, or validation failure this returns an
        empty list so callers can safely fall back to a previously
        known-good pair list. Rows for symbols that aren't USDT-quoted
        linear perpetuals, or that are missing/malformed OI or turnover
        fields, are skipped individually rather than failing the whole
        batch.
        """
        try:
            response = await self._client.get(
                TICKER_ENDPOINT, params={"category": LINEAR_CATEGORY}
            )
        except httpx.HTTPError:
            return []

        if response.status_code != 200:
            return []

        try:
            payload = response.json()
        except ValueError:
            return []

        if not isinstance(payload, dict) or payload.get("retCode") != _SUCCESS_RET_CODE:
            return []

        result = payload.get("result")
        if not isinstance(result, dict):
            return []

        ticker_list = result.get("list")
        if not isinstance(ticker_list, list):
            return []

        snapshots: list[TickerSnapshot] = []
        for row in ticker_list:
            snapshot = _parse_ticker_row(row)
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots

    async def fetch_open_interest_history(
        self, symbol: str, interval: str, limit: int
    ) -> list[OpenInterestPoint]:
        """
        Fetch a recent Open Interest time series for a single symbol
        from Bybit's public Open Interest history endpoint
        (category=linear), for Open Interest Confirmation.

        `interval` must be one of Bybit's supported granularities
        ("5min", "15min", "30min", "1h", "4h", "1d"). Uses only public
        market data (no API keys). Never raises: on any request,
        response, or validation failure -- including an unsupported
        `interval` or non-positive `limit` -- this returns an empty
        list so callers can treat OI confirmation as unavailable rather
        than fabricating a rising/falling signal. Returned points are
        in ascending chronological order (Bybit returns newest-first).
        """
        if interval not in _VALID_OPEN_INTEREST_INTERVALS:
            return []
        if limit <= 0 or limit > MAX_OPEN_INTEREST_LIMIT:
            return []

        try:
            validated_symbol = self._validate_symbol(symbol)
        except ValueError:
            return []

        bybit_symbol = _to_bybit_symbol(validated_symbol)

        try:
            response = await self._client.get(
                OPEN_INTEREST_ENDPOINT,
                params={
                    "category": LINEAR_CATEGORY,
                    "symbol": bybit_symbol,
                    "intervalTime": interval,
                    "limit": str(limit),
                },
            )
        except httpx.HTTPError:
            return []

        if response.status_code != 200:
            return []

        try:
            payload = response.json()
        except ValueError:
            return []

        if not isinstance(payload, dict) or payload.get("retCode") != _SUCCESS_RET_CODE:
            return []

        result = payload.get("result")
        if not isinstance(result, dict):
            return []

        oi_list = result.get("list")
        if not isinstance(oi_list, list):
            return []

        points: list[OpenInterestPoint] = []
        for row in oi_list:
            point = _parse_open_interest_row(row, validated_symbol)
            if point is not None:
                points.append(point)

        points.sort(key=lambda p: p.timestamp)
        return points
