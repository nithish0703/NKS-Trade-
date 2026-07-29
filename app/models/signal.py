"""
Data model representing a trading signal.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.config.thresholds import MIN_RISK_REWARD_RATIO


class Direction(str, Enum):
    """Trade direction."""

    BUY = "BUY"
    SELL = "SELL"


class SignalType(str, Enum):
    """Classification tier assigned to a signal by the scoring engine."""

    PREMIUM = "PREMIUM"
    STRONG = "STRONG"
    MEDIUM = "MEDIUM"
    IGNORE = "IGNORE"


class MarketRegime(str, Enum):
    """Broad classification of the current market regime."""

    TRENDING = "TRENDING"
    RANGING = "RANGING"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    UNKNOWN = "UNKNOWN"


class Signal(BaseModel):
    """
    Fully-formed institutional trade signal produced by the signal
    builder after all validation layers have passed.
    """

    model_config = ConfigDict(frozen=True)

    trade_id: str
    coin: str
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward_ratio: float
    confidence_score: float
    signal_type: SignalType
    market_regime: MarketRegime
    higher_timeframe_bias: str
    liquidity_type: str
    entry_zone_type: str
    structure_confirmation: str
    volume_confirmation: bool
    atr_status: str
    trading_session: str
    btc_market_alignment: bool
    detection_time_utc: datetime
    institutional_reason: str

    setup_key: str
    liquidity_sweep_id: str
    structure_break_id: str
    entry_zone_id: str
    retest_id: str
    created_at_utc: datetime

    @field_validator("entry_price", "stop_loss", "take_profit")
    @classmethod
    def _must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("entry_price, stop_loss and take_profit must be positive.")
        return value

    @field_validator("confidence_score")
    @classmethod
    def _confidence_score_in_range(cls, value: float) -> float:
        if value < 0 or value > 100:
            raise ValueError("confidence_score must be between 0 and 100.")
        return value

    @field_validator("risk_reward_ratio")
    @classmethod
    def _risk_reward_ratio_minimum(cls, value: float) -> float:
        if value < MIN_RISK_REWARD_RATIO:
            raise ValueError(f"risk_reward_ratio must be at least {MIN_RISK_REWARD_RATIO}.")
        return value

    @field_validator("detection_time_utc", "created_at_utc")
    @classmethod
    def _timestamps_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("Timestamps must be timezone-aware UTC.")
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("Timestamps must be in UTC.")
        return value

    @model_validator(mode="after")
    def _publishable_signal_type_required(self) -> "Signal":
        if self.signal_type not in (SignalType.PREMIUM, SignalType.STRONG):
            raise ValueError("A Signal's signal_type must be PREMIUM or STRONG.")
        return self

    @model_validator(mode="after")
    def _validate_direction_price_relationships(self) -> "Signal":
        if self.direction == Direction.BUY:
            if self.stop_loss >= self.entry_price:
                raise ValueError("BUY stop_loss must be below entry_price.")
            if self.take_profit <= self.entry_price:
                raise ValueError("BUY take_profit must be above entry_price.")
        elif self.direction == Direction.SELL:
            if self.stop_loss <= self.entry_price:
                raise ValueError("SELL stop_loss must be above entry_price.")
            if self.take_profit >= self.entry_price:
                raise ValueError("SELL take_profit must be below entry_price.")
        return self

    @property
    def is_publishable(self) -> bool:
        """Whether this signal meets the minimum publishable risk-reward ratio."""
        return self.risk_reward_ratio >= MIN_RISK_REWARD_RATIO
