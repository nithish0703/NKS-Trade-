"""
Data model representing a trade zone (order block, FVG, breaker, etc.).
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class ZoneType(str, Enum):
    """Type of institutional trade zone."""

    ORDER_BLOCK = "ORDER_BLOCK"
    BREAKER_BLOCK = "BREAKER_BLOCK"
    FAIR_VALUE_GAP = "FAIR_VALUE_GAP"


class ZoneStatus(str, Enum):
    """Lifecycle status of a trade zone."""

    FRESH = "FRESH"
    MITIGATED = "MITIGATED"
    INVALIDATED = "INVALIDATED"


class ZoneDirection(str, Enum):
    """Directional bias of a trade zone."""

    BUY = "BUY"
    SELL = "SELL"


class TradeZone(BaseModel):
    """
    Represents a price zone of interest (order block, breaker block, or
    fair value gap) used during entry-zone evaluation.

    This model only stores zone geometry and lifecycle status; it does
    not contain entry price, stop loss, take profit, confidence score,
    or signal fields.
    """

    model_config = ConfigDict(frozen=True)

    zone_id: str
    symbol: str
    timeframe: str
    zone_type: ZoneType
    direction: str
    lower_price: float
    upper_price: float
    created_at: datetime
    source_candle_timestamp: datetime
    source_candle_index: int = 0
    status: ZoneStatus = ZoneStatus.FRESH
    mitigation_timestamp: Optional[datetime] = None
    invalidation_timestamp: Optional[datetime] = None
    originating_break_id: Optional[str] = None
    originating_sweep_id: Optional[str] = None
    touch_count: int = 0
    metadata: Optional[dict[str, Any]] = None

    @field_validator("lower_price", "upper_price")
    @classmethod
    def _prices_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("lower_price and upper_price must be positive.")
        return value

    @field_validator("source_candle_index")
    @classmethod
    def _index_cannot_be_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("source_candle_index cannot be negative.")
        return value

    @field_validator("touch_count")
    @classmethod
    def _touch_count_cannot_be_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("touch_count cannot be negative.")
        return value

    @field_validator(
        "created_at", "source_candle_timestamp", "mitigation_timestamp", "invalidation_timestamp"
    )
    @classmethod
    def _timestamps_must_be_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return value
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("timestamps must be timezone-aware UTC.")
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("timestamps must be in UTC.")
        return value

    @model_validator(mode="after")
    def _validate_price_boundaries(self) -> "TradeZone":
        if self.lower_price >= self.upper_price:
            raise ValueError("lower_price must be less than upper_price.")
        return self

    @model_validator(mode="after")
    def _validate_timestamp_ordering(self) -> "TradeZone":
        if self.mitigation_timestamp is not None and self.mitigation_timestamp < self.created_at:
            raise ValueError("mitigation_timestamp cannot be before created_at.")
        if self.invalidation_timestamp is not None and self.invalidation_timestamp < self.created_at:
            raise ValueError("invalidation_timestamp cannot be before created_at.")
        return self

    @model_validator(mode="after")
    def _validate_status_consistency(self) -> "TradeZone":
        if self.status == ZoneStatus.FRESH and self.mitigation_timestamp is not None:
            raise ValueError("FRESH zones cannot already have a mitigation_timestamp.")
        if self.status == ZoneStatus.MITIGATED and self.mitigation_timestamp is None:
            raise ValueError("MITIGATED zones must have a mitigation_timestamp.")
        if self.status == ZoneStatus.INVALIDATED and self.invalidation_timestamp is None:
            raise ValueError("INVALIDATED zones must have an invalidation_timestamp.")
        return self
