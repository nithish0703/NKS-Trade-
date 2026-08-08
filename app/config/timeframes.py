"""
Configuration of timeframes used across market structure and analysis.
"""

from types import MappingProxyType
from typing import Final

# Core timeframe identifiers
HTF_PRIMARY: Final[str] = "4h"
HTF_SECONDARY: Final[str] = "1h"
ENTRY_TIMEFRAME: Final[str] = "15m"

# Timeframes used for BTC market-alignment analysis
BTC_ALIGNMENT_TIMEFRAMES: Final[tuple[str, ...]] = (HTF_PRIMARY, HTF_SECONDARY)

# Minimum number of historical candles required per timeframe. HTF_PRIMARY
# ("4h") is intentionally absent: the strategy pipeline (see
# app/strategy_pipeline/engine.py's _REQUIRED_TIMEFRAMES) does not read it
# today, so fetching 300 candles of it per symbol per scan cycle was pure
# wasted request weight. Kept out until Phase 5 reintroduces 4h for
# BTC-only regime alignment; HTF_PRIMARY and its EXCHANGE_TIMEFRAME_MAP
# entry below are kept defined for that reason.
REQUIRED_CANDLE_LIMITS: Final[MappingProxyType[str, int]] = MappingProxyType(
    {
        HTF_SECONDARY: 300,
        ENTRY_TIMEFRAME: 500,
    }
)

# Mapping between internal timeframe identifiers and Binance Futures
# kline "interval" values. Binance's interval parameter already uses the
# same "15m"/"1h"/"4h" suffix style as this application's internal
# vocabulary, unlike Bybit's bare minute-number strings.
EXCHANGE_TIMEFRAME_MAP: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        ENTRY_TIMEFRAME: "15m",
        HTF_SECONDARY: "1h",
        HTF_PRIMARY: "4h",
    }
)

# Duration of one internal timeframe's bar, in seconds. Used for
# candle-timeframe-duration calculations elsewhere in the application
# (Binance's kline rows carry an explicit closeTime, so this is no
# longer needed to infer candle completeness the way Bybit's rows did).
TIMEFRAME_DURATION_SECONDS: Final[MappingProxyType[str, int]] = MappingProxyType(
    {
        ENTRY_TIMEFRAME: 15 * 60,
        HTF_SECONDARY: 60 * 60,
        HTF_PRIMARY: 4 * 60 * 60,
    }
)


def get_exchange_timeframe(timeframe: str) -> str:
    """
    Convert an internal timeframe identifier into its Binance Futures
    kline "interval" value.

    Raises:
        ValueError: If the timeframe is not a supported internal timeframe.
    """
    try:
        return EXCHANGE_TIMEFRAME_MAP[timeframe]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported timeframe '{timeframe}'. "
            f"Supported timeframes: {sorted(EXCHANGE_TIMEFRAME_MAP)}."
        ) from exc


def get_timeframe_duration_seconds(timeframe: str) -> int:
    """
    Return the duration, in seconds, of one bar of `timeframe`.

    Raises:
        ValueError: If the timeframe is not a supported internal timeframe.
    """
    try:
        return TIMEFRAME_DURATION_SECONDS[timeframe]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported timeframe '{timeframe}'. "
            f"Supported timeframes: {sorted(TIMEFRAME_DURATION_SECONDS)}."
        ) from exc
