"""
Volume-based indicators and helpers.
"""

from typing import Optional, Sequence

from app.indicators.ema import IndicatorCalculationError, calculate_ema
from app.models.candle import Candle


def calculate_volume_ema(
    candles: Sequence[Candle], period: int
) -> list[Optional[float]]:
    """Calculate EMA over candle volumes, preserving candle order."""
    volumes = [candle.volume for candle in candles]
    return calculate_ema(volumes, period)


def get_latest_volume_ema(candles: Sequence[Candle], period: int) -> float:
    """
    Return the most recent volume EMA value.

    Raises:
        IndicatorCalculationError: If there are insufficient candles to
            produce a valid volume EMA value.
    """
    ema_values = calculate_volume_ema(candles, period)
    if not ema_values or ema_values[-1] is None:
        raise IndicatorCalculationError(
            f"Insufficient candles to calculate a {period}-period volume EMA; "
            f"got {len(candles)} candles."
        )
    return ema_values[-1]


def calculate_volume_ratio(current_volume: float, average_volume: float) -> float:
    """
    Calculate the ratio of current volume to average volume.

    Raises:
        IndicatorCalculationError: If current_volume is negative or
            average_volume is not strictly positive.
    """
    if current_volume < 0:
        raise IndicatorCalculationError("current_volume cannot be negative.")
    if average_volume <= 0:
        raise IndicatorCalculationError("average_volume must be positive.")
    return current_volume / average_volume


def is_above_average_volume(current_volume: float, volume_ema: float) -> bool:
    """
    Return True only when current_volume is strictly greater than volume_ema.

    This is a pure comparison helper; it does not reject or generate signals.
    """
    return current_volume > volume_ema


def calculate_volume_delta(
    buy_volume: Optional[float], sell_volume: Optional[float]
) -> Optional[float]:
    """
    Calculate the delta between buy and sell volume, when both are available.

    Returns:
        None when either input is unavailable. Otherwise, buy_volume - sell_volume.

    Raises:
        IndicatorCalculationError: If either provided value is negative.

    This function never estimates buy/sell volume from candle direction;
    it only operates on values explicitly supplied by the caller.
    """
    if buy_volume is None or sell_volume is None:
        return None
    if buy_volume < 0 or sell_volume < 0:
        raise IndicatorCalculationError("buy_volume and sell_volume cannot be negative.")
    return buy_volume - sell_volume
