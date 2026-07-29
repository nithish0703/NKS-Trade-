"""
Filters out fake breakout setups.
"""

from typing import Optional, Sequence

from app.liquidity.results import LiquiditySweepResult
from app.market_structure.shift_results import BreakDirection, StructureBreakResult
from app.models.candle import Candle
from app.models.validation_result import ValidationResult

from app.validators.results import FakeBreakoutResult, FakeBreakoutStatus

_LAYER_NAME = "FAKE_BREAKOUT_FILTER"


class FakeBreakoutFilter:
    """
    Detects fake breakouts: a confirmed structural break, supported by a
    prior liquidity sweep, whose price immediately reverses and closes
    back inside the previously broken structure within a limited window.
    """

    def __init__(self, maximum_reversal_candles: int, return_inside_tolerance_ratio: float) -> None:
        if maximum_reversal_candles < 1:
            raise ValueError(
                f"maximum_reversal_candles must be at least 1, got {maximum_reversal_candles}."
            )
        if return_inside_tolerance_ratio < 0:
            raise ValueError(
                f"return_inside_tolerance_ratio cannot be negative, got {return_inside_tolerance_ratio}."
            )

        self._maximum_reversal_candles = maximum_reversal_candles
        self._return_inside_tolerance_ratio = return_inside_tolerance_ratio

    def evaluate(
        self,
        candles: Sequence[Candle],
        sweep: Optional[LiquiditySweepResult],
        structure_break: Optional[StructureBreakResult],
    ) -> FakeBreakoutResult:
        """
        Evaluate whether the structural break is a fake breakout.

        Requires: liquidity sweep timestamp before the break timestamp,
        then inspects only the `maximum_reversal_candles` candles
        strictly after the break candle for an immediate, decisive
        close back inside the broken structure (adjusted only by the
        configured tolerance). Wick-only returns and reversals outside
        the configured window are never classified as fake breakouts.
        """
        if structure_break is None:
            return FakeBreakoutResult(
                status=FakeBreakoutStatus.UNKNOWN,
                liquidity_sweep=sweep,
                structure_break=None,
                reversal_candle_timestamp=None,
                returned_inside_structure=False,
                immediate_reversal=False,
                passed=False,
                reason="No structure break was provided.",
            )

        if sweep is None:
            return FakeBreakoutResult(
                status=FakeBreakoutStatus.UNKNOWN,
                liquidity_sweep=None,
                structure_break=structure_break,
                reversal_candle_timestamp=None,
                returned_inside_structure=False,
                immediate_reversal=False,
                passed=False,
                reason="No liquidity sweep was provided.",
            )

        if sweep.sweep_candle_timestamp >= structure_break.break_candle_timestamp:
            return FakeBreakoutResult(
                status=FakeBreakoutStatus.UNKNOWN,
                liquidity_sweep=sweep,
                structure_break=structure_break,
                reversal_candle_timestamp=None,
                returned_inside_structure=False,
                immediate_reversal=False,
                passed=False,
                reason="Liquidity sweep did not occur before the structure break.",
            )

        broken_price = structure_break.broken_swing.price
        tolerance = broken_price * self._return_inside_tolerance_ratio

        reversal_candidates = [
            c for c in candles if c.timestamp > structure_break.break_candle_timestamp
        ][: self._maximum_reversal_candles]

        if not reversal_candidates:
            return FakeBreakoutResult(
                status=FakeBreakoutStatus.PASSED,
                liquidity_sweep=sweep,
                structure_break=structure_break,
                reversal_candle_timestamp=None,
                returned_inside_structure=False,
                immediate_reversal=False,
                passed=True,
                reason="No reversal candles occurred within the configured window.",
            )

        if structure_break.direction == BreakDirection.BULLISH:
            for candle in reversal_candidates:
                closed_back_inside = candle.close < (broken_price - tolerance)
                decisive_rejection = candle.is_bearish or closed_back_inside
                if closed_back_inside and decisive_rejection:
                    return FakeBreakoutResult(
                        status=FakeBreakoutStatus.DETECTED,
                        liquidity_sweep=sweep,
                        structure_break=structure_break,
                        reversal_candle_timestamp=candle.timestamp,
                        returned_inside_structure=True,
                        immediate_reversal=True,
                        passed=False,
                        reason="Bullish break reversed and closed back below the broken swing high.",
                    )
        else:
            for candle in reversal_candidates:
                closed_back_inside = candle.close > (broken_price + tolerance)
                decisive_rejection = candle.is_bullish or closed_back_inside
                if closed_back_inside and decisive_rejection:
                    return FakeBreakoutResult(
                        status=FakeBreakoutStatus.DETECTED,
                        liquidity_sweep=sweep,
                        structure_break=structure_break,
                        reversal_candle_timestamp=candle.timestamp,
                        returned_inside_structure=True,
                        immediate_reversal=True,
                        passed=False,
                        reason="Bearish break reversed and closed back above the broken swing low.",
                    )

        return FakeBreakoutResult(
            status=FakeBreakoutStatus.PASSED,
            liquidity_sweep=sweep,
            structure_break=structure_break,
            reversal_candle_timestamp=None,
            returned_inside_structure=False,
            immediate_reversal=False,
            passed=True,
            reason="Price did not decisively close back inside the broken structure within the window.",
        )

    def validate(
        self,
        candles: Sequence[Candle],
        sweep: Optional[LiquiditySweepResult],
        structure_break: Optional[StructureBreakResult],
    ) -> ValidationResult:
        """
        Validate that no fake breakout occurred.

        Failure codes:
            - STRUCTURE_BREAK_DATA_MISSING
            - INVALID_SWEEP_BREAK_ORDER
            - FAKE_BREAKOUT_ANALYSIS_UNKNOWN
            - FAKE_BREAKOUT_DETECTED
        """
        result = self.evaluate(candles, sweep, structure_break)

        if result.status == FakeBreakoutStatus.PASSED:
            return ValidationResult.success(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                score=0.0,
            )

        if result.status == FakeBreakoutStatus.DETECTED:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="FAKE_BREAKOUT_DETECTED",
                score=0.0,
            )

        if structure_break is None:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="STRUCTURE_BREAK_DATA_MISSING",
                score=0.0,
            )

        if sweep is None or sweep.sweep_candle_timestamp >= structure_break.break_candle_timestamp:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=result.reason,
                rejection_code="INVALID_SWEEP_BREAK_ORDER",
                score=0.0,
            )

        return ValidationResult.failure(
            layer_name=_LAYER_NAME,
            reason=result.reason,
            rejection_code="FAKE_BREAKOUT_ANALYSIS_UNKNOWN",
            score=0.0,
        )
