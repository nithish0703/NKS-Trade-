"""
Shared deterministic ID generation helpers for the zones package.
"""

import hashlib

from app.models.trade_zone import ZoneType


def make_zone_id(
    symbol: str, timeframe: str, zone_type: ZoneType, source_key: str
) -> str:
    """Generate a stable, deterministic zone ID."""
    raw = f"{symbol}|{timeframe}|{zone_type.value}|{source_key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
