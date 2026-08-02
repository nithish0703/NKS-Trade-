"""
Check 3: CHoCH + BOS Confirmation.

Don't enter right after a sweep. The required flow is:

    Liquidity Sweep -> CHoCH -> BOS -> Entry

If no CHoCH exists, the setup is ignored outright -- a lone BOS with no
preceding CHoCH (which the legacy StructureShiftCalculator would happily
accept) is never sufficient for the new strategy.

This module reuses the existing CHOCHDetector and BOSDetector
unmodified (both already require their own preceding confirmed
liquidity sweep) and adds the missing sequencing rule: the confirmed
BOS used for entry must occur strictly after a confirmed, direction-
matching CHoCH.
"""

from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict

from app.liquidity.results import LiquiditySweepResult
from app.market_structure.bos_detector import BOSDetector
from app.market_structure.choch_detector import CHOCHDetector
from app.market_structure.displacement import DisplacementDetector
from app.market_structure.results import MarketStructureResult, SwingPoint
from app.market_structure.shift_results import BreakDirection, StructureBreakResult
from app.models.candle import Candle


class ChochBosSequenceResult(BaseModel):
    """
    Result of the Sweep -> CHoCH -> BOS -> Entry sequencing check.

    `entry_break` is the confirmed BOS to use for entry, only present
    when the full sequence is satisfied. This model never contains
    entry price, stop loss, or take profit -- those belong to risk
    management, computed only after this check passes.
    """

    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: str
    choch: Optional[StructureBreakResult] = None
    entry_break: Optional[StructureBreakResult] = None


def _direction_matches(break_direction: BreakDirection, expected_direction: str) -> bool:
    if expected_direction == "BUY":
        return break_direction == BreakDirection.BULLISH
    return break_direction == BreakDirection.BEARISH


def evaluate_choch_bos_sequence(
    *,
    candles: Sequence[Candle],
    structure: MarketStructureResult,
    swings: Sequence[SwingPoint],
    liquidity_sweeps: Sequence[LiquiditySweepResult],
    displacement_detector: DisplacementDetector,
    choch_detector: CHOCHDetector,
    bos_detector: BOSDetector,
    expected_direction: str,
    volume_ema_values: Optional[Sequence[Optional[float]]] = None,
) -> ChochBosSequenceResult:
    """
    Evaluate the Sweep -> CHoCH -> BOS -> Entry flow.

    Passes only when a confirmed, direction-matching CHoCH exists AND a
    confirmed, direction-matching BOS occurs strictly after that CHoCH's
    break candle. If no CHoCH exists at all, the setup is rejected
    immediately regardless of any BOS present -- a lone BOS never
    satisfies this check.

    The CHoCH used is the *initiating* (earliest) direction-matching
    CHoCH: it is the break that starts the reversal, and the entry BOS is
    the continuation break after it. Anchoring on the latest CHoCH would
    make the check unsatisfiable, since every aligned BOS is also
    reported as a CHoCH by CHOCHDetector under a matching trend.

    `volume_ema_values` must align 1:1 with `candles` and is required for
    displacement candles to be volume-confirmed; without it no
    displacement can ever be confirmed and no break can be detected.
    """
    if not candles:
        return ChochBosSequenceResult(passed=False, reason="No candles available.")

    displacement_results = displacement_detector.detect(candles, volume_ema_values)

    choch_results = choch_detector.detect(candles, structure, swings, displacement_results, liquidity_sweeps)
    matching_chochs = [c for c in choch_results if _direction_matches(c.direction, expected_direction)]

    if not matching_chochs:
        return ChochBosSequenceResult(
            passed=False,
            reason=(
                f"No confirmed {expected_direction}-aligned CHoCH exists; a lone BOS "
                "without a preceding CHoCH is never sufficient (no CHoCH = ignore)."
            ),
        )

    initiating_choch = min(matching_chochs, key=lambda c: c.break_candle_timestamp)

    bos_results = bos_detector.detect(candles, swings, displacement_results, liquidity_sweeps)
    matching_bos_after_choch = [
        b
        for b in bos_results
        if _direction_matches(b.direction, expected_direction)
        and b.break_candle_timestamp > initiating_choch.break_candle_timestamp
    ]

    if not matching_bos_after_choch:
        return ChochBosSequenceResult(
            passed=False,
            reason=(
                f"A confirmed {expected_direction}-aligned CHoCH exists, but no "
                "confirmed BOS has occurred after it yet."
            ),
            choch=initiating_choch,
        )

    entry_break = min(matching_bos_after_choch, key=lambda b: b.break_candle_timestamp)

    return ChochBosSequenceResult(
        passed=True,
        reason=(
            f"Confirmed {expected_direction}-aligned CHoCH followed by a confirmed "
            f"{expected_direction}-aligned BOS: Sweep -> CHoCH -> BOS -> Entry satisfied."
        ),
        choch=initiating_choch,
        entry_break=entry_break,
    )
