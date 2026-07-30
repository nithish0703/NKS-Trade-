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

# Minimum number of historical candles required per timeframe
REQUIRED_CANDLE_LIMITS: Final[MappingProxyType[str, int]] = MappingProxyType(
    {
        HTF_PRIMARY: 300,
        HTF_SECONDARY: 300,
        ENTRY_TIMEFRAME: 500,
    }
)

# Mapping between internal timeframe identifiers and Bybit v5 kline
# "interval" values. Bybit's interval parameter uses bare minute-number
# strings (not "15m"/"1H"/"4H" style suffixes).
EXCHANGE_TIMEFRAME_MAP: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        ENTRY_TIMEFRAME: "15",
        HTF_SECONDARY: "60",
        HTF_PRIMARY: "240",
    }
)

# Duration of one internal timeframe's bar, in seconds. Used to infer
# whether the most recent kline row from Bybit (which carries no
# explicit "closed" flag) represents a fully completed candle.
TIMEFRAME_DURATION_SECONDS: Final[MappingProxyType[str, int]] = MappingProxyType(
    {
        ENTRY_TIMEFRAME: 15 * 60,
        HTF_SECONDARY: 60 * 60,
        HTF_PRIMARY: 4 * 60 * 60,
    }
)


def get_exchange_timeframe(timeframe: str) -> str:
    """
    Convert an internal timeframe identifier into its Bybit v5 kline
    "interval" value.

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
