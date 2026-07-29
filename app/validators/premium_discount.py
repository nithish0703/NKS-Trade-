"""
Validates premium/discount positioning relative to range equilibrium.
"""

from app.models.validation_result import ValidationResult
from app.zones.dealing_range import DealingRangePosition, DealingRangeResult

_LAYER_NAME = "premium_discount"


class PremiumDiscountValidator:
    """
    Validates that a DealingRangeResult places price in the correct
    Premium/Discount zone for the expected trade direction.

    BUY setups are valid only in the Discount zone; SELL setups are
    valid only in the Premium zone. This validator does not calculate
    confidence points; score always remains zero at this step.
    """

    def validate(self, result: DealingRangeResult, expected_direction: str) -> ValidationResult:
        """
        Validate a DealingRangeResult against the expected direction.

        Failure reasons (via rejection_code):
            - DEALING_RANGE_MISSING
            - PRICE_OUTSIDE_DEALING_RANGE
            - PRICE_AT_EQUILIBRIUM
            - PRICE_IN_MIDDLE_OF_RANGE
            - BUY_NOT_IN_DISCOUNT
            - SELL_NOT_IN_PREMIUM
            - DEALING_RANGE_DIRECTION_MISMATCH
        """
        if (
            result.range_low is None
            or result.range_high is None
            or result.equilibrium_price is None
            or result.source_swing_low is None
            or result.source_swing_high is None
            or result.position == DealingRangePosition.UNKNOWN
        ):
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Dealing range could not be calculated (missing source swings).",
                rejection_code="DEALING_RANGE_MISSING",
                score=0.0,
            )

        if result.position == DealingRangePosition.OUTSIDE_RANGE:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Current price is outside the dealing range.",
                rejection_code="PRICE_OUTSIDE_DEALING_RANGE",
                score=0.0,
            )

        if result.position == DealingRangePosition.EQUILIBRIUM:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Current price sits at the dealing-range equilibrium.",
                rejection_code="PRICE_AT_EQUILIBRIUM",
                score=0.0,
            )

        if result.position == DealingRangePosition.MIDDLE:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Current price sits in the middle of the dealing range.",
                rejection_code="PRICE_IN_MIDDLE_OF_RANGE",
                score=0.0,
            )

        if expected_direction == "BUY" and result.position != DealingRangePosition.DISCOUNT:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="BUY setups require price to be in the Discount zone.",
                rejection_code="BUY_NOT_IN_DISCOUNT",
                score=0.0,
            )

        if expected_direction == "SELL" and result.position != DealingRangePosition.PREMIUM:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="SELL setups require price to be in the Premium zone.",
                rejection_code="SELL_NOT_IN_PREMIUM",
                score=0.0,
            )

        if not result.valid_for_direction:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Dealing-range position is not valid for the expected direction.",
                rejection_code="DEALING_RANGE_DIRECTION_MISMATCH",
                score=0.0,
            )

        return ValidationResult.success(
            layer_name=_LAYER_NAME,
            reason=f"Price position {result.position.value} is valid for {expected_direction} setups.",
            score=0.0,
        )
