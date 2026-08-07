"""
Immutable typed result models for swing detection and trend structure
classification.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class SwingType(str, Enum):
    """Type of a detected swing point."""

    HIGH = "HIGH"
    LOW = "LOW"


class StructureLabel(str, Enum):
    """Classification label assigned to a swing relative to the prior same-type swing."""

    HIGHER_HIGH = "HIGHER_HIGH"
    HIGHER_LOW = "HIGHER_LOW"
    LOWER_HIGH = "LOWER_HIGH"
    LOWER_LOW = "LOWER_LOW"
    EQUAL_HIGH = "EQUAL_HIGH"
    EQUAL_LOW = "EQUAL_LOW"
    UNCLASSIFIED = "UNCLASSIFIED"


class TrendDirection(str, Enum):
    """Overall structural trend direction for a single timeframe."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"


class SwingPoint(BaseModel):
    """
    A detected swing high or swing low on a single symbol and timeframe.
    """

    model_config = ConfigDict(frozen=True)

    swing_id: str
    symbol: str
    timeframe: str
    timestamp: datetime
    candle_index: int
    swing_type: SwingType
    price: float
    left_strength: int
    right_strength: int
    confirmed: bool
    metadata: Optional[dict[str, Any]] = None

    @field_validator("price")
    @classmethod
    def _price_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("price must be positive.")
        return value

    @field_validator("candle_index")
    @classmethod
    def _candle_index_cannot_be_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("candle_index cannot be negative.")
        return value

    @field_validator("left_strength", "right_strength")
    @classmethod
    def _strength_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("left_strength and right_strength must be positive.")
        return value

    @field_validator("timestamp")
    @classmethod
    def _timestamp_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("timestamp must be timezone-aware UTC.")
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("timestamp must be in UTC.")
        return value

    @model_validator(mode="after")
    def _confirmed_requires_right_side_history(self) -> "SwingPoint":
        if self.confirmed and self.candle_index < self.right_strength:
            raise ValueError(
                "confirmed cannot be True without enough right-side candles."
            )
        return self


class ClassifiedSwing(BaseModel):
    """A swing point together with its structural classification."""

    model_config = ConfigDict(frozen=True)

    swing: SwingPoint
    structure_label: StructureLabel
    previous_same_type_swing_id: Optional[str] = None
    price_difference: Optional[float] = None
    percentage_difference: Optional[float] = None


class MarketStructureResult(BaseModel):
    """
    Result of swing detection and trend structure classification for a
    single symbol and timeframe.

    This model only reports structural analysis; it does not contain any
    trade direction, confidence score, entry price, stop loss, take
    profit, or acceptance/rejection decision.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    swings: list[SwingPoint]
    classified_swings: list[ClassifiedSwing]
    latest_swing_high: Optional[SwingPoint] = None
    latest_swing_low: Optional[SwingPoint] = None
    trend_direction: TrendDirection
    higher_high_count: int
    higher_low_count: int
    lower_high_count: int
    lower_low_count: int
    equal_high_count: int
    equal_low_count: int
    confidence_notes: Optional[str] = None
