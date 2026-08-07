"""
EMA trend filter: an additional condition folded into Stage 1 (HTF
Bias) direction gating.

Reuses the existing `ema200_slope_direction` field already computed by
`IndicatorCalculator`/`classify_ema_slope` on every `IndicatorSnapshot`
(see app/indicators/ema.py, app/indicators/calculator.py) -- this
module performs no EMA calculation of its own. BUY is only permitted
when the entry-timeframe EMA200 slope is BULLISH; SELL is only
permitted when it is BEARISH. A FLAT or unavailable slope permits
neither direction.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.indicators.results import IndicatorSnapshot


class EmaTrendResult(BaseModel):
    """Result of the EMA trend filter check."""

    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: str


def evaluate_ema_trend(
    snapshot: Optional[IndicatorSnapshot], expected_direction: str
) -> EmaTrendResult:
    """
    Check that the entry-timeframe EMA200 slope direction agrees with
    `expected_direction`.

    Passes only when `expected_direction == "BUY"` and
    `snapshot.ema200_slope_direction == "BULLISH"`, or
    `expected_direction == "SELL"` and
    `snapshot.ema200_slope_direction == "BEARISH"`. A missing snapshot
    or a FLAT/unavailable slope direction never passes.
    """
    if snapshot is None or snapshot.ema200_slope_direction is None:
        return EmaTrendResult(
            passed=False,
            reason="EMA200 slope direction is unavailable.",
        )

    slope_direction = snapshot.ema200_slope_direction

    if expected_direction == "BUY" and slope_direction == "BULLISH":
        return EmaTrendResult(
            passed=True,
            reason="EMA200 slope is BULLISH, agreeing with the BUY direction.",
        )

    if expected_direction == "SELL" and slope_direction == "BEARISH":
        return EmaTrendResult(
            passed=True,
            reason="EMA200 slope is BEARISH, agreeing with the SELL direction.",
        )

    return EmaTrendResult(
        passed=False,
        reason=(
            f"EMA200 slope is {slope_direction}, which does not agree with the "
            f"{expected_direction} direction."
        ),
    )
