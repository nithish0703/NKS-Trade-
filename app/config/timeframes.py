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

# Mapping between internal timeframe identifiers and OKX "bar" values
EXCHANGE_TIMEFRAME_MAP: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        ENTRY_TIMEFRAME: "15m",
        HTF_SECONDARY: "1H",
        HTF_PRIMARY: "4H",
    }
)


def get_exchange_timeframe(timeframe: str) -> str:
    """
    Convert an internal timeframe identifier into its OKX "bar" value.

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
