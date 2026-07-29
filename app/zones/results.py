"""
Immutable typed result models for zone detection and zone validation.
"""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from app.models.trade_zone import TradeZone


class ZoneDetectionResult(BaseModel):
    """
    Aggregated result of order-block, breaker-block, and fair-value-gap
    detection, plus mitigation/invalidation evaluation, for a single
    symbol and timeframe.

    This model only reports zone geometry and lifecycle status; it does
    not contain entry price, stop loss, take profit, confidence score,
    or signal fields.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    order_blocks: list[TradeZone]
    breaker_blocks: list[TradeZone]
    fair_value_gaps: list[TradeZone]
    all_zones: list[TradeZone]
    fresh_zones: list[TradeZone]
    mitigated_zones: list[TradeZone]
    invalidated_zones: list[TradeZone]
    selected_zone: Optional[TradeZone] = None
    metadata: Optional[dict[str, Any]] = None


class ZoneValidationResult(BaseModel):
    """
    Result of validating a single TradeZone as a candidate entry zone.
    """

    model_config = ConfigDict(frozen=True)

    zone: TradeZone
    fresh: bool
    unmitigated: bool
    direction_aligned: bool
    valid: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None
