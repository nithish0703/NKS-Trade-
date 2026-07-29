"""
Validates market structure shift confirmation.
"""

from typing import Optional

from app.config.thresholds import DISPLACEMENT_CANDLE_MIN_BODY_RATIO
from app.market_structure.shift_results import (
    BreakConfirmation,
    StructureShiftDetectionResult,
)
from app.models.validation_result import ValidationResult

_LAYER_NAME = "structure_shift"


class StructureShiftValidator:
    """
    Validates that a StructureShiftDetectionResult contains a genuinely
    confirmed BOS/CHoCH/MSS break suitable for use by the later strategy
    pipeline.

    This validator does not calculate confidence points; score always
    remains zero at this step.
    """

    def validate(
        self,
        result: StructureShiftDetectionResult,
        expected_direction: Optional[str] = None,
    ) -> ValidationResult:
        """
        Validate the latest confirmed structural break in `result`.

        Failure reasons (via rejection_code):
            - STRUCTURE_SHIFT_MISSING
            - LIQUIDITY_SWEEP_ORDER_INVALID
            - DISPLACEMENT_NOT_CONFIRMED
            - WEAK_CANDLE_BODY
            - WEAK_STRUCTURE_CLOSE
            - WICK_ONLY_BREAK
            - DIRECTION_MISMATCH
        """
        latest = result.latest_confirmed_break

        if latest is None:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="No confirmed structural break (BOS/CHoCH/MSS) is available.",
                rejection_code="STRUCTURE_SHIFT_MISSING",
                score=0.0,
            )

        if latest.confirmation != BreakConfirmation.CONFIRMED:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Latest structural break is not confirmed.",
                rejection_code="STRUCTURE_SHIFT_MISSING",
                score=0.0,
            )

        sweep = latest.preceding_liquidity_sweep
        if (
            sweep is None
            or not sweep.confirmed
            or sweep.sweep_candle_timestamp >= latest.break_candle_timestamp
        ):
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=(
                    "Preceding liquidity sweep is missing, unconfirmed, or did not "
                    "occur before the structural break."
                ),
                rejection_code="LIQUIDITY_SWEEP_ORDER_INVALID",
                score=0.0,
            )

        if not latest.displacement.confirmed:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Displacement candle supporting the structural break is not confirmed.",
                rejection_code="DISPLACEMENT_NOT_CONFIRMED",
                score=0.0,
            )

        if latest.displacement.body_ratio < DISPLACEMENT_CANDLE_MIN_BODY_RATIO:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=(
                    f"Displacement candle body ratio {latest.displacement.body_ratio:.4f} "
                    f"is below the minimum {DISPLACEMENT_CANDLE_MIN_BODY_RATIO:.4f}."
                ),
                rejection_code="WEAK_CANDLE_BODY",
                score=0.0,
            )

        if not latest.strong_close_beyond_structure:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Break candle did not close with a strong close beyond structure.",
                rejection_code="WEAK_STRUCTURE_CLOSE",
                score=0.0,
            )

        if latest.wick_only_break:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Structural break is wick-only and was not confirmed by a close.",
                rejection_code="WICK_ONLY_BREAK",
                score=0.0,
            )

        if expected_direction is not None and latest.direction.value != expected_direction:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=(
                    f"Structural break direction '{latest.direction.value}' does not match "
                    f"expected direction '{expected_direction}'."
                ),
                rejection_code="DIRECTION_MISMATCH",
                score=0.0,
            )

        return ValidationResult.success(
            layer_name=_LAYER_NAME,
            reason=(
                f"Confirmed {latest.break_type.value} structural break with valid "
                "preceding liquidity sweep and displacement."
            ),
            score=0.0,
        )
