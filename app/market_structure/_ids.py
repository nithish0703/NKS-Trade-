"""
Shared deterministic ID generation helpers for structure-break detection.
"""

import hashlib

from app.market_structure.shift_results import StructureBreakType


def make_break_id(
    symbol: str,
    timeframe: str,
    break_type: StructureBreakType,
    swing_id: str,
    source_key: str,
) -> str:
    """
    Generate a stable, deterministic structure-break ID.

    `source_key` should be the break candle's timestamp, not its
    position/index within whatever candle window this scan happened to
    fetch -- that position shifts every scan cycle as the window rolls
    forward, which would otherwise make the same real structure break
    hash to a different ID each time it's rescanned.
    """
    raw = f"{symbol}|{timeframe}|{break_type.value}|{swing_id}|{source_key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
