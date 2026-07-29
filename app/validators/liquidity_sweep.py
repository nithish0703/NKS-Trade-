"""
Validates presence of a liquidity sweep prior to entry.
"""

from typing import Optional

from app.liquidity.results import LiquiditySweepResult, LiquidityStrength, SweepDirection
from app.models.validation_result import ValidationResult

_LAYER_NAME = "LIQUIDITY_SWEEP"


class LiquiditySweepValidator:
    """
    Validates that a confirmed, reclaimed, direction-matching liquidity
    sweep of a sufficiently strong institutional level exists.
    """

    def validate(
        self, sweep: Optional[LiquiditySweepResult], expected_direction: str
    ) -> ValidationResult:
        """
        Validate a candidate liquidity sweep.

        Failure codes:
            - LIQUIDITY_SWEEP_MISSING
            - LIQUIDITY_SWEEP_NOT_CONFIRMED
            - LIQUIDITY_NOT_RECLAIMED
            - LIQUIDITY_SWEEP_DIRECTION_MISMATCH
            - LIQUIDITY_LEVEL_TOO_WEAK
        """
        if sweep is None:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="No liquidity sweep was provided.",
                rejection_code="LIQUIDITY_SWEEP_MISSING",
                score=0.0,
            )

        if not sweep.confirmed:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Liquidity sweep is not confirmed.",
                rejection_code="LIQUIDITY_SWEEP_NOT_CONFIRMED",
                score=0.0,
            )

        if not sweep.reclaimed_level:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Liquidity level was not reclaimed.",
                rejection_code="LIQUIDITY_NOT_RECLAIMED",
                score=0.0,
            )

        expected_sweep_direction = (
            SweepDirection.BULLISH if expected_direction == "BUY" else SweepDirection.BEARISH
        )
        if sweep.direction != expected_sweep_direction:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Liquidity sweep direction does not match the expected direction.",
                rejection_code="LIQUIDITY_SWEEP_DIRECTION_MISMATCH",
                score=0.0,
            )

        if sweep.liquidity_level.strength not in (LiquidityStrength.STRONG, LiquidityStrength.INSTITUTIONAL):
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Liquidity level strength is too weak.",
                rejection_code="LIQUIDITY_LEVEL_TOO_WEAK",
                score=0.0,
            )

        return ValidationResult.success(
            layer_name=_LAYER_NAME,
            reason="LIQUIDITY_SWEEP_VALID",
            score=0.0,
        )
