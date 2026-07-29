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
    symbol: str, timeframe: str, liquidity_id: str, candle_index: int
) -> str:
    """Generate a stable, deterministic liquidity-sweep ID."""
    raw = f"{symbol}|{timeframe}|{liquidity_id}|{candle_index}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
