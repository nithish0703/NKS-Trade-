"""
Calculates the Premium/Discount dealing range and current-price position
relative to its 50% equilibrium.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Sequence

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.market_structure.results import SwingPoint, SwingType


class DealingRangeCalculationError(Exception):
    """Raised when dealing-range calculations cannot be performed."""


class DealingRangePosition(str, Enum):
    """Position of the current price within the dealing range."""

    PREMIUM = "PREMIUM"
    DISCOUNT = "DISCOUNT"
    EQUILIBRIUM = "EQUILIBRIUM"
    MIDDLE = "MIDDLE"
    OUTSIDE_RANGE = "OUTSIDE_RANGE"
    UNKNOWN = "UNKNOWN"


class DealingRangeResult(BaseModel):
    """
    Result of Premium/Discount dealing-range calculation for a single
    symbol and timeframe. This model does not generate a trade signal.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    direction: str
    range_low: Optional[float] = None
    range_high: Optional[float] = None
    equilibrium_price: Optional[float] = None
    current_price: float
    position: DealingRangePosition
    source_swing_low: Optional[SwingPoint] = None
    source_swing_high: Optional[SwingPoint] = None
    valid_for_direction: bool
    reason: str
    metadata: Optional[dict[str, Any]] = None

    @field_validator("range_low", "range_high")
    @classmethod
    def _range_prices_must_be_positive(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and value <= 0:
            raise ValueError("range_low and range_high must be positive.")
        return value

    @field_validator("current_price")
    @classmethod
    def _current_price_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("current_price must be positive.")
        return value

    @model_validator(mode="after")
    def _validate_range_ordering(self) -> "DealingRangeResult":
        if self.range_low is not None and self.range_high is not None:
            if self.range_low >= self.range_high:
                raise ValueError("range_low must be strictly less than range_high.")
            if self.equilibrium_price is not None and not (
                self.range_low <= self.equilibrium_price <= self.range_high
            ):
                raise ValueError("equilibrium_price must lie inside the range.")
        return self


class DealingRangeCalculator:
    """
    Calculates the Premium/Discount dealing range from confirmed major
    structural swing points and classifies the current price's position
    relative to the range's 50% equilibrium.
    """

    def __init__(self, equilibrium_tolerance_ratio: float, middle_zone_tolerance_ratio: float) -> None:
        if equilibrium_tolerance_ratio < 0:
            raise DealingRangeCalculationError(
                f"equilibrium_tolerance_ratio cannot be negative, got {equilibrium_tolerance_ratio}."
            )
        if middle_zone_tolerance_ratio < 0:
            raise DealingRangeCalculationError(
                f"middle_zone_tolerance_ratio cannot be negative, got {middle_zone_tolerance_ratio}."
            )
        if middle_zone_tolerance_ratio > 0.5:
            raise DealingRangeCalculationError(
                f"middle_zone_tolerance_ratio cannot exceed 0.5, got {middle_zone_tolerance_ratio}."
            )

        self._equilibrium_tolerance_ratio = equilibrium_tolerance_ratio
        self._middle_zone_tolerance_ratio = middle_zone_tolerance_ratio

    def calculate(
        self, swings: Sequence[SwingPoint], current_price: float, expected_direction: str
    ) -> DealingRangeResult:
        """
        Calculate the dealing range from the latest confirmed swing high
        and swing low, then classify `current_price` relative to it.

        Returns a DealingRangeResult with position UNKNOWN when a
        defensible range cannot be calculated (e.g. only one swing side
        exists). No range is ever fabricated.
        """
        if current_price <= 0:
            raise DealingRangeCalculationError("current_price must be positive.")

        symbol = swings[0].symbol if swings else ""
        timeframe = swings[0].timeframe if swings else ""

        confirmed_highs = [s for s in swings if s.swing_type == SwingType.HIGH and s.confirmed]
        confirmed_lows = [s for s in swings if s.swing_type == SwingType.LOW and s.confirmed]

        if not confirmed_highs or not confirmed_lows:
            return DealingRangeResult(
                symbol=symbol,
                timeframe=timeframe,
                direction=expected_direction,
                range_low=None,
                range_high=None,
                equilibrium_price=None,
                current_price=current_price,
                position=DealingRangePosition.UNKNOWN,
                source_swing_low=None,
                source_swing_high=None,
                valid_for_direction=False,
                reason="A confirmed swing high and swing low are both required to calculate a dealing range.",
            )

        latest_high = max(confirmed_highs, key=lambda s: s.timestamp)
        latest_low = max(confirmed_lows, key=lambda s: s.timestamp)

        if latest_low.price >= latest_high.price:
            return DealingRangeResult(
                symbol=symbol,
                timeframe=timeframe,
                direction=expected_direction,
                range_low=None,
                range_high=None,
                equilibrium_price=None,
                current_price=current_price,
                position=DealingRangePosition.UNKNOWN,
                source_swing_low=latest_low,
                source_swing_high=latest_high,
                valid_for_direction=False,
                reason="Selected swing low is not strictly below the selected swing high.",
            )

        range_low = latest_low.price
        range_high = latest_high.price
        equilibrium = range_low + ((range_high - range_low) * 0.5)
        range_span = range_high - range_low

        position = self._classify_position(current_price, range_low, range_high, equilibrium, range_span)
        valid_for_direction = self._is_valid_for_direction(position, expected_direction)

        return DealingRangeResult(
            symbol=symbol,
            timeframe=timeframe,
            direction=expected_direction,
            range_low=range_low,
            range_high=range_high,
            equilibrium_price=equilibrium,
            current_price=current_price,
            position=position,
            source_swing_low=latest_low,
            source_swing_high=latest_high,
            valid_for_direction=valid_for_direction,
            reason=self._describe_position(position, expected_direction, valid_for_direction),
        )

    def _classify_position(
        self,
        current_price: float,
        range_low: float,
        range_high: float,
        equilibrium: float,
        range_span: float,
    ) -> DealingRangePosition:
        if current_price < range_low or current_price > range_high:
            return DealingRangePosition.OUTSIDE_RANGE

        equilibrium_band = range_span * self._equilibrium_tolerance_ratio
        if abs(current_price - equilibrium) <= equilibrium_band:
            return DealingRangePosition.EQUILIBRIUM

        middle_band = range_span * self._middle_zone_tolerance_ratio
        if abs(current_price - equilibrium) <= middle_band:
            return DealingRangePosition.MIDDLE

        if current_price < equilibrium:
            return DealingRangePosition.DISCOUNT
        return DealingRangePosition.PREMIUM

    @staticmethod
    def _is_valid_for_direction(position: DealingRangePosition, expected_direction: str) -> bool:
        if expected_direction == "BUY":
            return position == DealingRangePosition.DISCOUNT
        if expected_direction == "SELL":
            return position == DealingRangePosition.PREMIUM
        return False

    @staticmethod
    def _describe_position(
        position: DealingRangePosition, expected_direction: str, valid_for_direction: bool
    ) -> str:
        if valid_for_direction:
            return f"Price position {position.value} is valid for {expected_direction} setups."
        return f"Price position {position.value} is not valid for {expected_direction} setups."
