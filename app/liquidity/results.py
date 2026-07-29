"""
Immutable typed result models for liquidity-level detection and
liquidity-sweep detection.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class LiquidityType(str, Enum):
    """Type of institutional liquidity level."""

    EQUAL_HIGH = "EQUAL_HIGH"
    EQUAL_LOW = "EQUAL_LOW"
    PREVIOUS_DAY_HIGH = "PREVIOUS_DAY_HIGH"
    PREVIOUS_DAY_LOW = "PREVIOUS_DAY_LOW"
    PREVIOUS_WEEK_HIGH = "PREVIOUS_WEEK_HIGH"
    PREVIOUS_WEEK_LOW = "PREVIOUS_WEEK_LOW"
    MAJOR_SWING_HIGH = "MAJOR_SWING_HIGH"
    MAJOR_SWING_LOW = "MAJOR_SWING_LOW"
    SESSION_HIGH = "SESSION_HIGH"
    SESSION_LOW = "SESSION_LOW"


class LiquiditySide(str, Enum):
    """Side of the market on which a liquidity level rests."""

    BUY_SIDE = "BUY_SIDE"
    SELL_SIDE = "SELL_SIDE"


class LiquidityStrength(str, Enum):
    """Relative institutional strength of a liquidity level."""

    INSTITUTIONAL = "INSTITUTIONAL"
    STRONG = "STRONG"
    WEAK = "WEAK"


class SweepDirection(str, Enum):
    """Direction implied by a confirmed liquidity sweep."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class SessionName(str, Enum):
    """Named UTC trading session."""

    ASIA = "ASIA"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"


_BUY_SIDE_TYPES = frozenset(
    {
        LiquidityType.EQUAL_HIGH,
        LiquidityType.PREVIOUS_DAY_HIGH,
        LiquidityType.PREVIOUS_WEEK_HIGH,
        LiquidityType.MAJOR_SWING_HIGH,
        LiquidityType.SESSION_HIGH,
    }
)
_SELL_SIDE_TYPES = frozenset(
    {
        LiquidityType.EQUAL_LOW,
        LiquidityType.PREVIOUS_DAY_LOW,
        LiquidityType.PREVIOUS_WEEK_LOW,
        LiquidityType.MAJOR_SWING_LOW,
        LiquidityType.SESSION_LOW,
    }
)


class LiquidityLevel(BaseModel):
    """
    A single institutional liquidity level (resting orders above or below
    price) on a single symbol and timeframe.
    """

    model_config = ConfigDict(frozen=True)

    liquidity_id: str
    symbol: str
    timeframe: str
    liquidity_type: LiquidityType
    liquidity_side: LiquiditySide
    price: float
    start_timestamp: datetime
    end_timestamp: datetime
    source_timestamps: list[datetime]
    touch_count: int
    strength: LiquidityStrength
    active: bool
    metadata: Optional[dict[str, Any]] = None

    @field_validator("price")
    @classmethod
    def _price_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("price must be positive.")
        return value

    @field_validator("start_timestamp", "end_timestamp")
    @classmethod
    def _timestamps_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("timestamps must be timezone-aware UTC.")
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("timestamps must be in UTC.")
        return value

    @field_validator("source_timestamps")
    @classmethod
    def _source_timestamps_cannot_be_empty(
        cls, value: list[datetime]
    ) -> list[datetime]:
        if not value:
            raise ValueError("source_timestamps cannot be empty.")
        for timestamp in value:
            if timestamp.tzinfo is None or timestamp.tzinfo.utcoffset(timestamp) is None:
                raise ValueError("source_timestamps must be timezone-aware UTC.")
        return value

    @field_validator("touch_count")
    @classmethod
    def _touch_count_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("touch_count must be positive.")
        return value

    @model_validator(mode="after")
    def _end_not_before_start(self) -> "LiquidityLevel":
        if self.end_timestamp < self.start_timestamp:
            raise ValueError("end_timestamp cannot be before start_timestamp.")
        return self

    @model_validator(mode="after")
    def _side_matches_type(self) -> "LiquidityLevel":
        if self.liquidity_type in _BUY_SIDE_TYPES and self.liquidity_side != LiquiditySide.BUY_SIDE:
            raise ValueError(
                f"{self.liquidity_type.value} liquidity must be BUY_SIDE."
            )
        if self.liquidity_type in _SELL_SIDE_TYPES and self.liquidity_side != LiquiditySide.SELL_SIDE:
            raise ValueError(
                f"{self.liquidity_type.value} liquidity must be SELL_SIDE."
            )
        return self


class LiquiditySweepResult(BaseModel):
    """
    Result of a confirmed (or rejected) liquidity sweep evaluation against
    a single liquidity level.
    """

    model_config = ConfigDict(frozen=True)

    sweep_id: str
    symbol: str
    timeframe: str
    direction: SweepDirection
    liquidity_level: LiquidityLevel
    sweep_candle_timestamp: datetime
    sweep_candle_index: int
    sweep_price: float
    close_price: float
    penetration_distance: float
    penetration_ratio: float
    reclaimed_level: bool
    confirmed: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None

    @field_validator("sweep_price", "close_price")
    @classmethod
    def _prices_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("sweep_price and close_price must be positive.")
        return value

    @field_validator("sweep_candle_index")
    @classmethod
    def _index_cannot_be_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("sweep_candle_index cannot be negative.")
        return value

    @field_validator("penetration_distance", "penetration_ratio")
    @classmethod
    def _cannot_be_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("penetration_distance and penetration_ratio cannot be negative.")
        return value

    @field_validator("sweep_candle_timestamp")
    @classmethod
    def _timestamp_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("sweep_candle_timestamp must be timezone-aware UTC.")
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("sweep_candle_timestamp must be in UTC.")
        return value

    @model_validator(mode="after")
    def _confirmed_requires_reclaim(self) -> "LiquiditySweepResult":
        if self.confirmed and not self.reclaimed_level:
            raise ValueError("confirmed cannot be True without reclaimed_level being True.")
        return self


class LiquidityDetectionResult(BaseModel):
    """
    Aggregated result of institutional liquidity-level detection for a
    single symbol and timeframe.

    This model only reports detected liquidity levels; it does not
    contain trade direction, entry price, stop loss, take profit,
    confidence score, or signal acceptance/rejection.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    equal_highs: list[LiquidityLevel]
    equal_lows: list[LiquidityLevel]
    previous_day_levels: list[LiquidityLevel]
    previous_week_levels: list[LiquidityLevel]
    major_swing_levels: list[LiquidityLevel]
    session_levels: list[LiquidityLevel]
    all_levels: list[LiquidityLevel]
    active_levels: list[LiquidityLevel]
    metadata: Optional[dict[str, Any]] = None
