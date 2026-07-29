"""
Immutable typed result models for retest confirmation.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.models.trade_zone import TradeZone


class RetestStatus(str, Enum):
    """Confirmation status of a retest candidate."""

    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    PENDING = "PENDING"


class RejectionCandleDirection(str, Enum):
    """Direction of a candidate rejection candle."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class RetestResult(BaseModel):
    """
    Evaluation of a candidate retest of a single TradeZone.

    This model only reports retest confirmation mechanics; it does not
    contain entry price, stop loss, take profit, confidence score, or
    signal type.
    """

    model_config = ConfigDict(frozen=True)

    retest_id: str
    symbol: str
    timeframe: str
    zone: TradeZone
    retest_candle_timestamp: datetime
    retest_candle_index: int
    interaction_price: float
    rejection_direction: Optional[RejectionCandleDirection] = None
    body_ratio: float
    upper_wick_ratio: float
    lower_wick_ratio: float
    close_location_value: float
    rejection_strength: float
    volume: float
    volume_ema: Optional[float] = None
    volume_ratio: Optional[float] = None
    volume_confirmed: bool
    zone_interaction_confirmed: bool
    rejection_candle_confirmed: bool
    status: RetestStatus
    reason: str
    metadata: Optional[dict[str, Any]] = None

    @field_validator("retest_candle_index")
    @classmethod
    def _index_cannot_be_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("retest_candle_index cannot be negative.")
        return value

    @field_validator("interaction_price")
    @classmethod
    def _interaction_price_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("interaction_price must be positive.")
        return value

    @field_validator(
        "body_ratio", "upper_wick_ratio", "lower_wick_ratio", "close_location_value"
    )
    @classmethod
    def _ratio_between_zero_and_one(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("value must be between 0 and 1.")
        return value

    @field_validator("volume")
    @classmethod
    def _volume_cannot_be_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("volume cannot be negative.")
        return value

    @field_validator("volume_ema")
    @classmethod
    def _volume_ema_must_be_positive_when_present(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and value <= 0:
            raise ValueError("volume_ema must be positive when present.")
        return value

    @field_validator("volume_ratio")
    @classmethod
    def _volume_ratio_cannot_be_negative_when_present(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and value < 0:
            raise ValueError("volume_ratio cannot be negative when present.")
        return value

    @field_validator("retest_candle_timestamp")
    @classmethod
    def _timestamp_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("retest_candle_timestamp must be timezone-aware UTC.")
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("retest_candle_timestamp must be in UTC.")
        return value

    @model_validator(mode="after")
    def _confirmed_requires_all_checks(self) -> "RetestResult":
        if self.status == RetestStatus.CONFIRMED:
            if not self.zone_interaction_confirmed:
                raise ValueError("CONFIRMED retest requires zone_interaction_confirmed=True.")
            if not self.rejection_candle_confirmed:
                raise ValueError("CONFIRMED retest requires rejection_candle_confirmed=True.")
            if not self.volume_confirmed:
                raise ValueError("CONFIRMED retest requires volume_confirmed=True.")
        return self
