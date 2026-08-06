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


class SignalStatus(str, Enum):
    """
    Binary outcome of the strategy pipeline for a candidate setup.

    A Signal only ever exists with status=CONFIRMED: every required
    strategy condition passed. A REJECTED outcome never produces a
    Signal at all (it is represented purely as a rejected pipeline
    result, never persisted as a Signal) -- there is no intermediate
    score or confidence tier between these two outcomes.
    """

    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class Signal(BaseModel):
    """
    Fully-formed institutional trade signal produced by the signal
    builder after every required strategy condition has passed.

    Carries no numeric score, percentage, or confidence tier of any
    kind: `status` is always CONFIRMED for a persisted Signal. Ranking
    scores computed elsewhere (e.g. for the dashboard's Scanning Coins
    panel) are never part of this model and never influence whether a
    Signal is produced.
    """

    model_config = ConfigDict(frozen=True)

    trade_id: str
    coin: str
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward_ratio: float
    status: SignalStatus
    liquidity_type: str
    entry_zone_type: str
    structure_confirmation: str
    detection_time_utc: datetime
    institutional_reason: str

    setup_key: str
    liquidity_sweep_id: str
    structure_break_id: str
    entry_zone_id: str
    created_at_utc: datetime

    @field_validator("entry_price", "stop_loss", "take_profit")
    @classmethod
    def _must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("entry_price, stop_loss and take_profit must be positive.")
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
    def _signal_status_must_be_confirmed(self) -> "Signal":
        if self.status != SignalStatus.CONFIRMED:
            raise ValueError("A Signal's status must be CONFIRMED.")
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
