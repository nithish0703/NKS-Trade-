"""
Stage 2: Liquidity Sweep.

Not every wick counts as a valid sweep. This module adds the specific
rules required for the new pipeline on top of an existing confirmed
LiquiditySweepResult (which already guarantees a previous high/low was
broken and price closed back on the origin side of the level):

  - The sweep candle's wick (in the direction of the sweep) must be
    larger than its body -- a decisive rejection wick, not a
    full-bodied breakout candle.
  - The sweep candle must close back inside the prior range (the
    range of the candles preceding the sweep candle), not just back
    across the liquidity level price -- a stricter, range-based
    variant of "closes back inside range" than a level-price-only check.

This never re-detects sweeps itself; it consumes an already-detected
`LiquiditySweepResult` (from the existing LiquiditySweepDetector) and
the raw candle series, and adds the wick/body + range-close rules. Its
`passed=True` output is what the next stage (BOS) requires as the
precondition for a valid trade idea.
"""

from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict

from app.liquidity.results import LiquiditySweepResult, SweepDirection
from app.models.candle import Candle


class ValidSweepResult(BaseModel):
    """Result of validating a confirmed liquidity sweep against the pipeline's stricter rules."""

    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: str
    wick_size: Optional[float] = None
    body_size: Optional[float] = None
    prior_range_high: Optional[float] = None
    prior_range_low: Optional[float] = None


def _sweep_candle(
    candles: Sequence[Candle], sweep: LiquiditySweepResult
) -> Optional[Candle]:
    for candle in candles:
        if candle.timestamp == sweep.sweep_candle_timestamp:
            return candle
    return None


def _prior_range(
    candles: Sequence[Candle], sweep_candle_index: int, lookback: int
) -> Optional[tuple[float, float]]:
    """Range (high, low) of the `lookback` candles strictly before the sweep candle."""
    prior_candles = candles[max(0, sweep_candle_index - lookback) : sweep_candle_index]
    if not prior_candles:
        return None
    return max(c.high for c in prior_candles), min(c.low for c in prior_candles)


def validate_liquidity_sweep(
    sweep: Optional[LiquiditySweepResult],
    candles: Sequence[Candle],
    *,
    expected_direction: str,
    range_lookback: int = 10,
) -> ValidSweepResult:
    """
    Validate a confirmed liquidity sweep against the pipeline's rules:
    previous high/low broken and reclaimed (guaranteed by
    `sweep.confirmed`), wick larger than body, and close back inside
    the prior range.

    Never fabricates a sweep: a missing or unconfirmed sweep, or a
    sweep whose direction doesn't match `expected_direction`, fails
    immediately without inspecting candle data.
    """
    if sweep is None:
        return ValidSweepResult(passed=False, reason="No confirmed liquidity sweep is available.")
    if not sweep.confirmed:
        return ValidSweepResult(
            passed=False, reason="Liquidity sweep was not confirmed (not reclaimed)."
        )

    required_direction = (
        SweepDirection.BULLISH if expected_direction == "BUY" else SweepDirection.BEARISH
    )
    if sweep.direction != required_direction:
        return ValidSweepResult(
            passed=False,
            reason=(
                f"Sweep direction {sweep.direction.value} does not match "
                f"expected direction {expected_direction}."
            ),
        )

    candle = _sweep_candle(candles, sweep)
    if candle is None:
        return ValidSweepResult(
            passed=False, reason="Sweep candle could not be located in the supplied candle series."
        )

    wick_size = candle.upper_wick if sweep.direction == SweepDirection.BEARISH else candle.lower_wick
    body_size = candle.body_size

    if wick_size <= body_size:
        return ValidSweepResult(
            passed=False,
            reason=(
                f"Sweep candle's rejection wick ({wick_size:.6g}) is not larger than "
                f"its body ({body_size:.6g}); not a decisive rejection."
            ),
            wick_size=wick_size,
            body_size=body_size,
        )

    prior_range = _prior_range(candles, sweep.sweep_candle_index, range_lookback)
    if prior_range is None:
        return ValidSweepResult(
            passed=False,
            reason="Insufficient prior candle history to determine the range to close back inside.",
            wick_size=wick_size,
            body_size=body_size,
        )

    prior_high, prior_low = prior_range
    close_inside_range = prior_low <= sweep.close_price <= prior_high
    if not close_inside_range:
        return ValidSweepResult(
            passed=False,
            reason=(
                f"Sweep candle closed at {sweep.close_price:.6g}, outside the prior "
                f"range [{prior_low:.6g}, {prior_high:.6g}]."
            ),
            wick_size=wick_size,
            body_size=body_size,
            prior_range_high=prior_high,
            prior_range_low=prior_low,
        )

    return ValidSweepResult(
        passed=True,
        reason=(
            f"Confirmed {expected_direction} sweep with a rejection wick "
            f"({wick_size:.6g}) larger than its body ({body_size:.6g}), closing back "
            f"inside the prior range [{prior_low:.6g}, {prior_high:.6g}]."
        ),
        wick_size=wick_size,
        body_size=body_size,
        prior_range_high=prior_high,
        prior_range_low=prior_low,
    )
