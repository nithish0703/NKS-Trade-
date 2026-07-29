"""
Immutable typed result models for displacement candles and BOS/CHOCH/MSS
structure-break detection.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.liquidity.results import LiquiditySweepResult

from app.market_structure.results import SwingPoint


class StructureBreakType(str, Enum):
    """Type of confirmed structural break."""

    BOS = "BOS"
    CHOCH = "CHOCH"
    MSS = "MSS"


class BreakDirection(str, Enum):
    """Direction implied by a structural break."""

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class BreakConfirmation(str, Enum):
    """Confirmation status of an evaluated structural break candidate."""

    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    PENDING = "PENDING"


class DisplacementResult(BaseModel):
    """
    Evaluation of a single candle as a candidate displacement candle.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    candle_timestamp: datetime
    candle_index: int
    direction: BreakDirection
    body_ratio: float
    candle_range: float
    body_size: float
    close_location_value: float
    volume_confirmed: bool
    strong_close: bool
    confirmed: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None

    @field_validator("candle_index")
    @classmethod
    def _index_cannot_be_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("candle_index cannot be negative.")
        return value

    @field_validator("body_ratio", "close_location_value")
    @classmethod
    def _ratio_between_zero_and_one(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("value must be between 0 and 1.")
        return value

    @field_validator("candle_range", "body_size")
    @classmethod
    def _cannot_be_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("candle_range and body_size cannot be negative.")
        return value

    @field_validator("candle_timestamp")
    @classmethod
    def _timestamp_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("candle_timestamp must be timezone-aware UTC.")
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("candle_timestamp must be in UTC.")
        return value


class StructureBreakResult(BaseModel):
    """
    Evaluation of a single confirmed or rejected structural break
    (BOS, CHOCH, or MSS) against a broken swing.
    """

    model_config = ConfigDict(frozen=True)

    break_id: str
    symbol: str
    timeframe: str
    break_type: StructureBreakType
    direction: BreakDirection
    broken_swing: SwingPoint
    break_candle_timestamp: datetime
    break_candle_index: int
    break_price: float
    close_price: float
    displacement: DisplacementResult
    preceding_liquidity_sweep: Optional[LiquiditySweepResult] = None
    strong_close_beyond_structure: bool
    wick_only_break: bool
    confirmation: BreakConfirmation
    reason: str
    metadata: Optional[dict[str, Any]] = None

    @field_validator("break_price", "close_price")
    @classmethod
    def _prices_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("break_price and close_price must be positive.")
        return value

    @field_validator("break_candle_index")
    @classmethod
    def _index_cannot_be_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("break_candle_index cannot be negative.")
        return value

    @field_validator("break_candle_timestamp")
    @classmethod
    def _timestamp_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("break_candle_timestamp must be timezone-aware UTC.")
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("break_candle_timestamp must be in UTC.")
        return value

    @model_validator(mode="after")
    def _confirmed_requires_sweep_and_no_wick_only(self) -> "StructureBreakResult":
        if self.confirmation == BreakConfirmation.CONFIRMED:
            if self.preceding_liquidity_sweep is None or not self.preceding_liquidity_sweep.confirmed:
                raise ValueError(
                    "confirmed StructureBreakResult must contain a valid preceding "
                    "liquidity sweep."
                )
            if self.wick_only_break:
                raise ValueError(
                    "confirmed StructureBreakResult cannot have wick_only_break=True."
                )
        return self


class StructureShiftDetectionResult(BaseModel):
    """
    Aggregated result of displacement, BOS, CHOCH, and MSS detection for
    a single symbol and timeframe.

    This model only reports structural break analysis; it does not
    contain entry price, stop loss, take profit, confidence score, or
    signal fields.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    displacement_candles: list[DisplacementResult]
    bos_results: list[StructureBreakResult]
    choch_results: list[StructureBreakResult]
    mss_results: list[StructureBreakResult]
    all_breaks: list[StructureBreakResult]
    latest_confirmed_break: Optional[StructureBreakResult] = None
    metadata: Optional[dict[str, Any]] = None
