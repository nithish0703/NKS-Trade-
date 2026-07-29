"""
Average Directional Index (ADX) indicator.

This is a manual Wilder-style implementation; no external technical
analysis library is used.
"""

from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict

from app.indicators.atr import calculate_true_ranges
from app.indicators.ema import IndicatorCalculationError
from app.models.candle import Candle


class ADXResult(BaseModel):
    """Container for ADX calculation outputs, one entry per input candle."""

    model_config = ConfigDict(frozen=True)

    plus_di: list[Optional[float]]
    minus_di: list[Optional[float]]
    dx: list[Optional[float]]
    adx: list[Optional[float]]


def _wilder_smooth(values: Sequence[float], period: int) -> list[Optional[float]]:
    """Apply Wilder smoothing, seeded with a simple sum of the first `period` values."""
    result: list[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return result

    seed = sum(values[:period])
    result[period - 1] = seed

    previous = seed
    for index in range(period, len(values)):
        current = previous - (previous / period) + values[index]
        result[index] = current
        previous = current

    return result


def calculate_adx(candles: Sequence[Candle], period: int) -> ADXResult:
    """
    Calculate ADX, +DI, -DI, and DX using Wilder smoothing.

    Requirements:
        - Directional movement and true range are Wilder-smoothed.
        - +DI = 100 * smoothed(+DM) / smoothed(TR)
        - -DI = 100 * smoothed(-DM) / smoothed(TR)
        - DX = 100 * abs(+DI - -DI) / (+DI + -DI)
        - ADX is seeded with the average of the first `period` valid DX
          values, then Wilder-smoothed thereafter.

    Returns:
        An ADXResult whose lists are all the same length as `candles`.
        Unavailable values are None.

    Raises:
        IndicatorCalculationError: If period is not positive or there are
            insufficient candles.
    """
    if period <= 0:
        raise IndicatorCalculationError(
            f"period must be greater than zero, got {period}."
        )
    # Directional movement requires at least period + 1 candles to seed
    # the smoothed +DM/-DM/TR series, and a further `period` DX values to
    # seed ADX itself.
    minimum_required = (period * 2) + 1
    if len(candles) < minimum_required:
        raise IndicatorCalculationError(
            f"Insufficient candles to calculate a {period}-period ADX; "
            f"required at least {minimum_required}, got {len(candles)}."
        )

    true_ranges = calculate_true_ranges(candles)

    plus_dms: list[float] = [0.0]
    minus_dms: list[float] = [0.0]
    for index in range(1, len(candles)):
        up_move = candles[index].high - candles[index - 1].high
        down_move = candles[index - 1].low - candles[index].low

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)

    smoothed_tr = _wilder_smooth(true_ranges, period)
    smoothed_plus_dm = _wilder_smooth(plus_dms, period)
    smoothed_minus_dm = _wilder_smooth(minus_dms, period)

    length = len(candles)
    plus_di: list[Optional[float]] = [None] * length
    minus_di: list[Optional[float]] = [None] * length
    dx: list[Optional[float]] = [None] * length

    for index in range(length):
        tr = smoothed_tr[index]
        pdm = smoothed_plus_dm[index]
        mdm = smoothed_minus_dm[index]

        if tr is None or pdm is None or mdm is None:
            continue

        if tr == 0:
            plus_di[index] = 0.0
            minus_di[index] = 0.0
        else:
            plus_di[index] = 100 * pdm / tr
            minus_di[index] = 100 * mdm / tr

        di_sum = plus_di[index] + minus_di[index]
        if di_sum == 0:
            dx[index] = 0.0
        else:
            dx[index] = 100 * abs(plus_di[index] - minus_di[index]) / di_sum

    adx: list[Optional[float]] = [None] * length
    valid_dx_indices = [i for i in range(length) if dx[i] is not None]

    if len(valid_dx_indices) >= period:
        seed_index = valid_dx_indices[period - 1]
        seed_window = [dx[i] for i in valid_dx_indices[:period]]
        seed_value = sum(seed_window) / period
        adx[seed_index] = seed_value

        previous_adx = seed_value
        for i in valid_dx_indices[period:]:
            current_adx = (previous_adx * (period - 1) + dx[i]) / period
            adx[i] = current_adx
            previous_adx = current_adx

    return ADXResult(plus_di=plus_di, minus_di=minus_di, dx=dx, adx=adx)


def get_latest_adx(candles: Sequence[Candle], period: int) -> float:
    """
    Return the most recent ADX value.

    Raises:
        IndicatorCalculationError: If no valid latest ADX value exists.
    """
    result = calculate_adx(candles, period)
    if not result.adx or result.adx[-1] is None:
        raise IndicatorCalculationError(
            f"Insufficient candles to calculate a {period}-period ADX; "
            f"got {len(candles)} candles."
        )
    return result.adx[-1]
