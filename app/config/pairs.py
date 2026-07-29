"""
Configuration of tradable pairs/symbols monitored by the engine.
"""

import re
from typing import Final, List

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


def get_configured_pairs() -> List[str]:
    """
    Return a copy of the configured trading pairs, ensuring BTC is always
    included for market-alignment analysis.
    """
    pairs = list(DEFAULT_PAIRS)
    if BTC_SYMBOL not in pairs:
        pairs.insert(0, BTC_SYMBOL)
    return pairs


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
    normalized = symbol.strip().upper()

    if not _SYMBOL_PATTERN.match(normalized):
        raise ValueError(
            f"Invalid symbol format '{symbol}'. Expected SYMBOL-QUOTE format, "
            "e.g. 'BTC-USDT'."
        )

    configured_pairs = get_configured_pairs()
    if normalized not in configured_pairs:
        raise ValueError(
            f"Unsupported trading pair '{normalized}'. "
            f"Configured pairs: {configured_pairs}."
        )

    return normalized
