"""
Stage 3: BOS (Trade idea valid?).

After a valid liquidity sweep, the trade idea is only valid once price
breaks structure in the expected direction: a confirmed Break of
Structure (BOS) whose own preceding liquidity sweep is the sweep this
pipeline already validated in Stage 2. A BOS from an unrelated, earlier
sweep does not validate today's trade idea.

This never re-detects BOS itself; it consumes the existing
`BOSDetector`'s output unmodified (which already requires its own
confirmed supporting liquidity sweep before the break candle, and
already rejects wick-only breaks) and adds the pipeline-specific rule
that the BOS's supporting sweep must be the same sweep object Stage 2
validated, not merely "some earlier sweep of the right direction."
"""

from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict

from app.liquidity.results import LiquiditySweepResult
from app.market_structure.bos_detector import BOSDetector
from app.market_structure.displacement import DisplacementResult
from app.market_structure.results import SwingPoint
from app.market_structure.shift_results import BreakDirection, StructureBreakResult
from app.models.candle import Candle


class BosResult(BaseModel):
    """
    Result of the BOS (trade idea validity) stage.

    `structure_break` is the confirmed BOS to use for entry context,
    only present when the stage passes. This model never contains
    entry price, stop loss, or take profit -- those belong to risk
    management, computed only after every stage passes.
    """

    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: str
    structure_break: Optional[StructureBreakResult] = None


def _direction_matches(break_direction: BreakDirection, expected_direction: str) -> bool:
    if expected_direction == "BUY":
        return break_direction == BreakDirection.BULLISH
    return break_direction == BreakDirection.BEARISH


def evaluate_bos(
    *,
    candles: Sequence[Candle],
    swings: Sequence[SwingPoint],
    displacement_results: Sequence[DisplacementResult],
    liquidity_sweeps: Sequence[LiquiditySweepResult],
    validated_sweep: Optional[LiquiditySweepResult],
    bos_detector: BOSDetector,
    expected_direction: str,
) -> BosResult:
    """
    Evaluate whether a confirmed, direction-matching BOS exists whose
    supporting liquidity sweep is `validated_sweep` (Stage 2's output).

    `liquidity_sweeps` is the full set of confirmed sweeps known for
    this symbol/timeframe (the same input BOSDetector would normally
    receive) -- not just `validated_sweep` alone -- so the detector
    independently discovers whichever sweep genuinely precedes and
    supports each break candle, exactly as it would in production.
    `validated_sweep` is then used only to check that the BOS the
    detector actually found is anchored to that specific sweep, not
    merely "some sweep of the right direction."

    Passes only when: `validated_sweep` is present and confirmed, and a
    BOSDetector-confirmed break exists in the expected direction whose
    `preceding_liquidity_sweep` is that exact sweep (matched by
    `sweep_id`). Never fabricates a BOS from a missing or unconfirmed
    sweep, and never accepts a BOS supported by a different, unrelated
    sweep.
    """
    if validated_sweep is None or not validated_sweep.confirmed:
        return BosResult(
            passed=False,
            reason="No confirmed liquidity sweep is available to anchor a BOS.",
        )

    bos_results = bos_detector.detect(candles, swings, displacement_results, liquidity_sweeps)

    matching = [
        b
        for b in bos_results
        if _direction_matches(b.direction, expected_direction)
        and b.preceding_liquidity_sweep is not None
        and b.preceding_liquidity_sweep.sweep_id == validated_sweep.sweep_id
    ]

    if not matching:
        return BosResult(
            passed=False,
            reason=(
                f"No confirmed {expected_direction}-aligned BOS anchored to the "
                "validated liquidity sweep exists yet; the trade idea is not yet valid."
            ),
        )

    entry_break = min(matching, key=lambda b: b.break_candle_timestamp)

    return BosResult(
        passed=True,
        reason=(
            f"Confirmed {expected_direction}-aligned BOS anchored to the validated "
            "liquidity sweep: the trade idea is valid."
        ),
        structure_break=entry_break,
    )
