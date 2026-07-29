"""
Reusable candle-quality calculations.

This module only computes descriptive metrics about a single candle's
shape; it does not evaluate structure, volume, or market direction.
"""

from pydantic import BaseModel, ConfigDict

from app.models.candle import Candle


class CandleMetrics(BaseModel):
    """Descriptive shape metrics for a single candle."""

    model_config = ConfigDict(frozen=True)

    body_size: float
    candle_range: float
    body_ratio: float
    upper_wick: float
    lower_wick: float
    upper_wick_ratio: float
    lower_wick_ratio: float
    close_location_value: float
    bullish: bool
    bearish: bool


def calculate_candle_metrics(candle: Candle) -> CandleMetrics:
    """
    Calculate descriptive shape metrics for a single candle.

    Close location value (CLV) is calculated as:
        (close - low) / (high - low)

    Zero-range candles are handled safely, returning 0.0 for all
    ratio-based metrics. This function does not decide whether a candle
    is acceptable or should be rejected.
    """
    candle_range = candle.candle_range

    if candle_range == 0:
        upper_wick_ratio = 0.0
        lower_wick_ratio = 0.0
        close_location_value = 0.0
    else:
        upper_wick_ratio = candle.upper_wick / candle_range
        lower_wick_ratio = candle.lower_wick / candle_range
        close_location_value = (candle.close - candle.low) / candle_range

    return CandleMetrics(
        body_size=candle.body_size,
        candle_range=candle_range,
        body_ratio=candle.body_ratio,
        upper_wick=candle.upper_wick,
        lower_wick=candle.lower_wick,
        upper_wick_ratio=upper_wick_ratio,
        lower_wick_ratio=lower_wick_ratio,
        close_location_value=close_location_value,
        bullish=candle.is_bullish,
        bearish=candle.is_bearish,
    )


def is_displacement_body(candle: Candle, minimum_body_ratio: float) -> bool:
    """
    Return True when the candle's body ratio meets or exceeds the
    configured minimum displacement body ratio.

    This function does not check structure, volume, or market direction.
    """
    return candle.body_ratio >= minimum_body_ratio
