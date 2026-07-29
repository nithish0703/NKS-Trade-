"""
Average True Range (ATR) indicator.
"""

from typing import Optional, Sequence

from app.indicators.ema import IndicatorCalculationError
from app.models.candle import Candle


def calculate_true_ranges(candles: Sequence[Candle]) -> list[Optional[float]]:
    """
    Calculate the True Range for every candle in the sequence.

    The first candle's True Range is simply its high-low range. Every
    subsequent candle's True Range is the maximum of:
        - high - low
        - abs(high - previous_close)
        - abs(low - previous_close)

    Returns:
        A list the same length as `candles`, with one True Range value
        per candle (never None, given at least one candle).
    """
    if not candles:
        raise IndicatorCalculationError("candles cannot be empty.")

    true_ranges: list[Optional[float]] = []
    previous_close: Optional[float] = None

    for candle in candles:
        if previous_close is None:
            true_range = candle.high - candle.low
        else:
            true_range = max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        true_ranges.append(true_range)
        previous_close = candle.close

    return true_ranges


def calculate_atr(
    candles: Sequence[Candle], period: int
) -> list[Optional[float]]:
    """
    Calculate the Average True Range (ATR) using Wilder smoothing.

    The ATR is seeded using the simple average of the first `period` True
    Range values, then updated using Wilder's smoothing formula:

        atr = (previous_atr * (period - 1) + current_true_range) / period

    Returns:
        A list the same length as `candles`. Entries before the seeded
        ATR index are None.

    Raises:
        IndicatorCalculationError: If period is not positive or there are
            insufficient candles.
    """
    if period <= 0:
        raise IndicatorCalculationError(
            f"period must be greater than zero, got {period}."
        )
    if len(candles) < period:
        raise IndicatorCalculationError(
            f"Insufficient candles to calculate a {period}-period ATR; "
            f"got {len(candles)} candles."
        )

    true_ranges = calculate_true_ranges(candles)
    result: list[Optional[float]] = [None] * len(candles)

    seed = sum(true_ranges[:period]) / period
    result[period - 1] = seed

    previous_atr = seed
    for index in range(period, len(candles)):
        current_atr = (previous_atr * (period - 1) + true_ranges[index]) / period
        result[index] = current_atr
        previous_atr = current_atr

    return result


def get_latest_atr(candles: Sequence[Candle], period: int) -> float:
    """
    Return the most recent ATR value.

    Raises:
        IndicatorCalculationError: If there are insufficient candles to
            produce a valid ATR value.
    """
    atr_values = calculate_atr(candles, period)
    if not atr_values or atr_values[-1] is None:
        raise IndicatorCalculationError(
            f"Insufficient candles to calculate a {period}-period ATR; "
            f"got {len(candles)} candles."
        )
    return atr_values[-1]


def calculate_atr_ratio(current_atr: float, reference_atr: float) -> float:
    """
    Calculate the ratio of the current ATR to a reference ATR value.

    Raises:
        IndicatorCalculationError: If either value is not strictly positive.
    """
    if current_atr <= 0 or reference_atr <= 0:
        raise IndicatorCalculationError(
            "Both current_atr and reference_atr must be positive."
        )
    return current_atr / reference_atr


def calculate_atr_expansion(
    atr_values: Sequence[Optional[float]], reference_lookback: int
) -> float:
    """
    Calculate the ratio of the latest ATR value to the average of the
    prior `reference_lookback` valid ATR values.

    The latest ATR value itself is excluded from the reference average.

    Raises:
        IndicatorCalculationError: If reference_lookback is not positive,
            the latest ATR is unavailable, or there are fewer than
            `reference_lookback` valid reference ATR values immediately
            preceding it.
    """
    if reference_lookback <= 0:
        raise IndicatorCalculationError(
            f"reference_lookback must be greater than zero, got {reference_lookback}."
        )
    if not atr_values or atr_values[-1] is None:
        raise IndicatorCalculationError(
            "Latest ATR value is unavailable; cannot calculate expansion."
        )

    reference_candidates = atr_values[:-1]
    valid_reference_values = [v for v in reference_candidates if v is not None]

    if len(valid_reference_values) < reference_lookback:
        raise IndicatorCalculationError(
            f"Insufficient valid reference ATR values; required "
            f"{reference_lookback}, got {len(valid_reference_values)}."
        )

    reference_window = valid_reference_values[-reference_lookback:]
    reference_average = sum(reference_window) / reference_lookback

    if reference_average <= 0:
        raise IndicatorCalculationError(
            "Reference ATR average must be positive to calculate expansion."
        )

    return atr_values[-1] / reference_average
