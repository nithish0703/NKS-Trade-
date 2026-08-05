"""
Abstract market-data provider interface.

Defines the contract every exchange-specific market-data client must
implement, so the rest of the application (strategy engine, dashboard
API, scripts) depends only on this interface and never on a concrete
exchange's client class. Swapping exchanges means writing one new class
that implements this interface and updating a single factory call site
(app.scanner.engine_factory) — no other code needs to change.
"""

from abc import ABC, abstractmethod
from typing import Optional

from app.data.open_interest_point import OpenInterestPoint
from app.data.ticker_snapshot import TickerSnapshot
from app.models.candle import Candle


class MarketDataProvider(ABC):
    """
    Async client contract for fetching public candle/ticker market data
    from a single exchange. Implementations must not require API keys
    for the methods below (public market data only).
    """

    @abstractmethod
    async def fetch_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        """
        Fetch completed OHLCV candles for a symbol and internal timeframe,
        in ascending chronological order, containing only completed
        (fully closed) candles.

        Raises:
            ValueError: If the symbol, timeframe, or limit are invalid.
            MarketDataRequestError: If the HTTP request fails.
            MarketDataResponseError: If the HTTP status or response body
                is invalid.
            MarketDataValidationError: If the response or resulting
                candle sequence fails structural validation.
        """
        raise NotImplementedError

    @abstractmethod
    async def fetch_multiple_timeframes(
        self, symbol: str, timeframes: list[str]
    ) -> dict[str, list[Candle]]:
        """
        Fetch candles for multiple internal timeframes. If any requested
        timeframe fails, an aggregated MarketDataError is raised and no
        partial result is returned.
        """
        raise NotImplementedError

    @abstractmethod
    async def fetch_symbol_market_data(self, symbol: str) -> dict[str, list[Candle]]:
        """Fetch candles for the standard set of required timeframes (15m, 1h, 4h) for a single symbol."""
        raise NotImplementedError

    @abstractmethod
    async def fetch_ticker_price(self, symbol: str) -> Optional[float]:
        """
        Fetch the latest traded price for a symbol from the exchange's
        public ticker endpoint.

        Returns None (rather than raising) on any request, response, or
        validation failure, so callers displaying a "current price" can
        safely fall back to an unavailable/placeholder state instead of
        fabricating a price.
        """
        raise NotImplementedError

    @abstractmethod
    async def fetch_all_linear_tickers(self) -> list[TickerSnapshot]:
        """
        Fetch Open Interest and 24h turnover for every available USDT
        perpetual futures pair in a single call, for dynamic
        liquidity/OI-based coin discovery.

        Returns an empty list (rather than raising) on any request,
        response, or validation failure, so callers can safely fall
        back to a previously known-good pair list instead of ending up
        with an empty scan universe due to a transient API hiccup.
        """
        raise NotImplementedError

    @abstractmethod
    async def fetch_all_ticker_prices(self) -> dict[str, float]:
        """
        Fetch the latest traded price for every USDT perpetual futures
        pair in a single call, keyed by internal hyphenated symbol
        (e.g. "BTC-USDT"). Dashboard-display only: cheaper than calling
        fetch_ticker_price() once per symbol, and never used by the
        strategy pipeline, scoring, or risk logic.

        Returns an empty dict (rather than raising) on any request,
        response, or validation failure, so callers displaying a "live
        price" column can safely fall back to an unavailable/placeholder
        state instead of fabricating a price.
        """
        raise NotImplementedError

    @abstractmethod
    async def fetch_open_interest_history(
        self, symbol: str, interval: str, limit: int
    ) -> list[OpenInterestPoint]:
        """
        Fetch a recent Open Interest time series for a single symbol,
        for Open Interest Confirmation (rising/falling OI compared over
        time, not just a single current snapshot).

        Returns points in ascending chronological order. Returns an
        empty list (rather than raising) on any request, response, or
        validation failure, so callers can treat OI confirmation as
        unavailable rather than fabricating a rising/falling signal.
        """
        raise NotImplementedError

    @abstractmethod
    async def __aenter__(self) -> "MarketDataProvider":
        raise NotImplementedError

    @abstractmethod
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        raise NotImplementedError
