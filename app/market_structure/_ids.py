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
    candle_index: int,
) -> str:
    """Generate a stable, deterministic structure-break ID."""
    raw = f"{symbol}|{timeframe}|{break_type.value}|{swing_id}|{candle_index}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
