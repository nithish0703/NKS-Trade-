"""
Exponential Moving Average (EMA) indicator.
"""

import math
from typing import Optional, Sequence

from app.models.candle import Candle


class IndicatorCalculationError(Exception):
    """Raised when an indicator cannot be calculated from the given inputs."""


def calculate_ema(values: Sequence[float], period: int) -> list[Optional[float]]:
    """
    Calculate the Exponential Moving Average (EMA) for a sequence of values.

    The EMA is seeded using the Simple Moving Average (SMA) of the first
    `period` values, then updated using the standard EMA multiplier of
    2 / (period + 1) for every subsequent value.

    Returns:
        A list the same length as `values`. Entries before the EMA seed
        index are None.

    Raises:
        IndicatorCalculationError: If period is not positive, values is
            empty, or any value is not a finite number.
    """
    if period <= 0:
        raise IndicatorCalculationError(
            f"period must be greater than zero, got {period}."
        )
    if not values:
        raise IndicatorCalculationError("values cannot be empty.")
    for value in values:
        if not math.isfinite(value):
            raise IndicatorCalculationError(f"Non-finite value encountered: {value}.")

    result: list[Optional[float]] = [None] * len(values)

    if len(values) < period:
        return result

    multiplier = 2 / (period + 1)
    seed = sum(values[:period]) / period
    result[period - 1] = seed

    previous_ema = seed
    for index in range(period, len(values)):
        current_ema = (values[index] - previous_ema) * multiplier + previous_ema
        result[index] = current_ema
        previous_ema = current_ema

    return result


def calculate_candle_close_ema(
    candles: Sequence[Candle], period: int
) -> list[Optional[float]]:
    """Calculate EMA over candle close prices, preserving candle order."""
    closes = [candle.close for candle in candles]
    return calculate_ema(closes, period)


def get_latest_ema(candles: Sequence[Candle], period: int) -> float:
    """
    Return the most recent EMA value calculated from candle close prices.

    Raises:
        IndicatorCalculationError: If there are insufficient candles to
            produce a valid EMA value.
    """
    ema_values = calculate_candle_close_ema(candles, period)
    if not ema_values or ema_values[-1] is None:
        raise IndicatorCalculationError(
            f"Insufficient candles to calculate a {period}-period EMA; "
            f"got {len(candles)} candles."
        )
    return ema_values[-1]


def calculate_ema_slope(
    ema_values: Sequence[Optional[float]], lookback: int
) -> float:
    """
    Calculate the signed slope ratio of an EMA series over `lookback` periods.

    Formula:
        (current_ema - ema_lookback_periods_ago) / abs(ema_lookback_periods_ago)

    Raises:
        IndicatorCalculationError: If lookback is not positive, there is
            insufficient data, or the reference EMA value is zero or
            unavailable.
    """
    if lookback <= 0:
        raise IndicatorCalculationError(
            f"lookback must be greater than zero, got {lookback}."
        )
    if len(ema_values) <= lookback:
        raise IndicatorCalculationError(
            f"Insufficient EMA values for a lookback of {lookback}; "
            f"got {len(ema_values)} values."
        )

    current_ema = ema_values[-1]
    reference_ema = ema_values[-1 - lookback]

    if current_ema is None or reference_ema is None:
        raise IndicatorCalculationError(
            "Insufficient valid EMA values to calculate slope."
        )
    if reference_ema == 0:
        raise IndicatorCalculationError(
            "Cannot calculate EMA slope with a zero reference EMA value."
        )

    return (current_ema - reference_ema) / abs(reference_ema)


def classify_ema_slope(slope: float, flat_threshold: float) -> str:
    """
    Classify an EMA slope ratio as "BULLISH", "BEARISH", or "FLAT".

    This is a pure classification helper; it does not make any trade
    acceptance or rejection decisions.
    """
    if slope > flat_threshold:
        return "BULLISH"
    if slope < -flat_threshold:
        return "BEARISH"
    return "FLAT"
