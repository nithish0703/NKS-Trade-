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
    async def __aenter__(self) -> "MarketDataProvider":
        raise NotImplementedError

    @abstractmethod
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        raise NotImplementedError
