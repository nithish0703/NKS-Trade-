"""
Check 5: CVD (Cumulative Volume Delta) Confirmation.

  BUY:  CVD higher low
  SELL: CVD lower high
  If CVD disagrees -> ignore.

Binance Futures kline data does include a takerBuyBaseVolume field, but
this application does not currently consume it; CVD here is computed
via the standard proxy used when true tick/taker data isn't wired up:
a candle's full volume counts as buy volume if it closed green
(bullish) and sell volume if it closed red (bearish), and CVD is the
running cumulative sum of (buy_volume - sell_volume) per candle. A
doji (close == open) contributes zero to the delta.
"""

from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict

from app.models.candle import Candle


class CvdPoint(BaseModel):
    """A single point in a cumulative volume delta series."""

    model_config = ConfigDict(frozen=True)

    index: int
    cvd: float


class CvdConfirmationResult(BaseModel):
    """Result of the CVD Confirmation check."""

    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: str
    cvd_series: list[CvdPoint] = []


def calculate_cvd_series(candles: Sequence[Candle]) -> list[CvdPoint]:
    """
    Compute the cumulative volume delta series for `candles`, using the
    candle-direction proxy: a bullish candle's full volume counts as
    buy volume, a bearish candle's full volume counts as sell volume,
    and a doji (close == open) contributes zero delta.
    """
    series: list[CvdPoint] = []
    running_total = 0.0
    for index, candle in enumerate(candles):
        if candle.is_bullish:
            delta = candle.volume
        elif candle.is_bearish:
            delta = -candle.volume
        else:
            delta = 0.0
        running_total += delta
        series.append(CvdPoint(index=index, cvd=running_total))
    return series


def _find_swing_lows(values: Sequence[float], left_strength: int, right_strength: int) -> list[int]:
    """Indices where `values` forms a confirmed local minimum (swing low)."""
    swing_indices: list[int] = []
    for index in range(len(values)):
        left_start = index - left_strength
        right_end = index + right_strength
        if left_start < 0 or right_end >= len(values):
            continue
        left_window = values[left_start:index]
        right_window = values[index + 1 : right_end + 1]
        if all(values[index] < v for v in left_window) and all(
            values[index] <= v for v in right_window
        ):
            swing_indices.append(index)
    return swing_indices


def _find_swing_highs(values: Sequence[float], left_strength: int, right_strength: int) -> list[int]:
    """Indices where `values` forms a confirmed local maximum (swing high)."""
    swing_indices: list[int] = []
    for index in range(len(values)):
        left_start = index - left_strength
        right_end = index + right_strength
        if left_start < 0 or right_end >= len(values):
            continue
        left_window = values[left_start:index]
        right_window = values[index + 1 : right_end + 1]
        if all(values[index] > v for v in left_window) and all(
            values[index] >= v for v in right_window
        ):
            swing_indices.append(index)
    return swing_indices


def evaluate_cvd_confirmation(
    candles: Sequence[Candle],
    *,
    expected_direction: str,
    left_strength: int = 3,
    right_strength: int = 3,
) -> CvdConfirmationResult:
    """
    Confirm CVD agrees with `expected_direction`:

      - BUY requires the two most recent confirmed CVD swing lows to
        form a higher low (later low > earlier low).
      - SELL requires the two most recent confirmed CVD swing highs to
        form a lower high (later high < earlier high).

    Never fabricates a swing pattern from insufficient candle history;
    fewer than two confirmed swing points of the required type fails.
    """
    if not candles:
        return CvdConfirmationResult(passed=False, reason="No candles available.")

    series = calculate_cvd_series(candles)
    values = [point.cvd for point in series]

    if expected_direction == "BUY":
        swing_low_indices = _find_swing_lows(values, left_strength, right_strength)
        if len(swing_low_indices) < 2:
            return CvdConfirmationResult(
                passed=False,
                reason="Fewer than two confirmed CVD swing lows exist; cannot determine higher-low pattern.",
                cvd_series=series,
            )
        earlier_index, later_index = swing_low_indices[-2], swing_low_indices[-1]
        earlier_low, later_low = values[earlier_index], values[later_index]
        if later_low > earlier_low:
            return CvdConfirmationResult(
                passed=True,
                reason=(
                    f"CVD formed a higher low ({earlier_low:.6g} -> {later_low:.6g}), "
                    "confirming genuine buying pressure."
                ),
                cvd_series=series,
            )
        return CvdConfirmationResult(
            passed=False,
            reason=(
                f"CVD did not form a higher low ({earlier_low:.6g} -> {later_low:.6g}); "
                "CVD disagrees with BUY."
            ),
            cvd_series=series,
        )

    # SELL
    swing_high_indices = _find_swing_highs(values, left_strength, right_strength)
    if len(swing_high_indices) < 2:
        return CvdConfirmationResult(
            passed=False,
            reason="Fewer than two confirmed CVD swing highs exist; cannot determine lower-high pattern.",
            cvd_series=series,
        )
    earlier_index, later_index = swing_high_indices[-2], swing_high_indices[-1]
    earlier_high, later_high = values[earlier_index], values[later_index]
    if later_high < earlier_high:
        return CvdConfirmationResult(
            passed=True,
            reason=(
                f"CVD formed a lower high ({earlier_high:.6g} -> {later_high:.6g}), "
                "confirming genuine selling pressure."
            ),
            cvd_series=series,
        )
    return CvdConfirmationResult(
        passed=False,
        reason=(
            f"CVD did not form a lower high ({earlier_high:.6g} -> {later_high:.6g}); "
            "CVD disagrees with SELL."
        ),
        cvd_series=series,
    )
