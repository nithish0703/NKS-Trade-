"""
Configuration of tradable pairs/symbols monitored by the engine.
"""

import re
from typing import Callable, Final, List, Optional, Sequence

_SYMBOL_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+$")

BTC_SYMBOL: Final[str] = "BTC-USDT"

DEFAULT_PAIRS: Final[List[str]] = [
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "XRP-USDT",
    "BNB-USDT",
    "ADA-USDT",
    "DOGE-USDT",
    "AVAX-USDT",
    "LINK-USDT",
    "DOT-USDT",
]

# The active pair-list source. Defaults to `None`, meaning
# `get_configured_pairs()` falls back to the static `DEFAULT_PAIRS` list
# below. When dynamic Liquidity+OI-based coin discovery is enabled,
# `engine_factory.build_scanner_service()` swaps this to the discovery
# service's `get_current_pairs` at startup via `set_pair_source()`, so
# both `get_configured_pairs()` and `validate_pair_symbol()` immediately
# and consistently recognize whatever pairs the active source returns,
# with no special-casing anywhere else in the pipeline.
_pair_source: Optional[Callable[[], Sequence[str]]] = None


def set_pair_source(source: Optional[Callable[[], Sequence[str]]]) -> None:
    """
    Install (or clear, with `None`) the active dynamic pair-list source.

    Intended to be called once at application startup (by
    `engine_factory.build_scanner_service()`), not per-request. Passing
    `None` restores the static `DEFAULT_PAIRS` fallback.
    """
    global _pair_source
    _pair_source = source


def get_configured_pairs() -> List[str]:
    """
    Return a copy of the currently configured trading pairs, ensuring
    BTC is always included for market-alignment analysis.

    Reads from the active dynamic pair source if one has been installed
    via `set_pair_source()`; otherwise falls back to the static
    `DEFAULT_PAIRS` list (existing behaviour, unchanged unless dynamic
    discovery is explicitly enabled).
    """
    if _pair_source is not None:
        pairs = list(_pair_source())
    else:
        pairs = list(DEFAULT_PAIRS)
    if BTC_SYMBOL not in pairs:
        pairs.insert(0, BTC_SYMBOL)
    return pairs


def validate_pair_symbol_format(symbol: str) -> str:
    """
    Validate and normalize a trading pair symbol's SYMBOL-QUOTE format
    only, without checking it against the configured pair allow-list.

    Used by market-data code (e.g. dynamic pair discovery) that must
    determine which symbols are well-formed before they can become part
    of the configured pair list itself -- checking against
    `get_configured_pairs()` here would be circular.

    Raises:
        ValueError: If the symbol is not in SYMBOL-QUOTE format.
    """
    normalized = symbol.strip().upper()
    if not _SYMBOL_PATTERN.match(normalized):
        raise ValueError(
            f"Invalid symbol format '{symbol}'. Expected SYMBOL-QUOTE format, "
            "e.g. 'BTC-USDT'."
        )
    return normalized


def validate_pair_symbol(symbol: str) -> str:
    """
    Validate and normalize a trading pair symbol against the configured
    pair list.

    The input is stripped of surrounding whitespace and uppercased before
    validation. Only symbols already present in the configured pair list
    are accepted; unknown symbols are rejected rather than silently
    introduced.

    Raises:
        ValueError: If the symbol is not in SYMBOL-QUOTE format, or is not
            one of the configured pairs.
    """
    normalized = validate_pair_symbol_format(symbol)

    configured_pairs = get_configured_pairs()
    if normalized not in configured_pairs:
        raise ValueError(
            f"Unsupported trading pair '{normalized}'. "
            f"Configured pairs: {configured_pairs}."
        )

    return normalized
