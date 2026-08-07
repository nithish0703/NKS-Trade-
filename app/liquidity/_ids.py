"""
Shared deterministic ID generation helpers for the liquidity package.
"""

import hashlib

from app.liquidity.results import LiquidityType


def make_liquidity_id(
    symbol: str, timeframe: str, liquidity_type: LiquidityType, source_key: str
) -> str:
    """Generate a stable, deterministic liquidity level ID."""
    raw = f"{symbol}|{timeframe}|{liquidity_type.value}|{source_key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def make_sweep_id(
    symbol: str, timeframe: str, liquidity_id: str, source_key: str
) -> str:
    """
    Generate a stable, deterministic liquidity-sweep ID.

    `source_key` should be the sweep (penetration) candle's timestamp,
    not its position/index within whatever candle window this scan
    happened to fetch -- that position shifts every scan cycle as the
    window rolls forward, which would otherwise make the same real
    sweep hash to a different ID each time it's rescanned.
    """
    raw = f"{symbol}|{timeframe}|{liquidity_id}|{source_key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
