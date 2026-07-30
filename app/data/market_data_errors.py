"""
Exchange-agnostic exceptions raised by market-data provider implementations.
"""


class MarketDataError(Exception):
    """Base exception for all market-data related failures."""


class MarketDataRequestError(MarketDataError):
    """Raised when the HTTP request to the exchange fails."""


class MarketDataResponseError(MarketDataError):
    """Raised when the exchange returns an invalid or non-success response."""


class MarketDataValidationError(MarketDataError):
    """Raised when fetched candle data fails structural validation."""
