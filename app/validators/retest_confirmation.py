"""
Validates retest confirmation of a zone or level.
"""

from typing import Optional

from app.models.trade_zone import TradeZone, ZoneStatus
from app.models.validation_result import ValidationResult
from app.zones.retest_results import RetestResult, RetestStatus

_LAYER_NAME = "retest_confirmation"


class RetestConfirmationValidator:
    """
    Validates that a RetestResult represents a genuine, confirmed retest
    of the currently selected entry zone.

    This validator does not calculate confidence points; score always
    remains zero at this step.
    """

    def validate(
        self,
        retest: Optional[RetestResult],
        expected_direction: str,
        selected_zone: TradeZone,
    ) -> ValidationResult:
        """
        Validate a candidate retest against the expected direction and
        the currently selected entry zone.

        Failure reasons (via rejection_code):
            - RETEST_MISSING
            - RETEST_NOT_CONFIRMED
            - RETEST_ZONE_MISMATCH
            - RETEST_DIRECTION_MISMATCH
            - RETEST_ZONE_INTERACTION_MISSING
            - RETEST_REJECTION_MISSING
            - RETEST_VOLUME_MISSING
            - RETEST_ZONE_NOT_FRESH
            - RETEST_ZONE_MITIGATED
            - RETEST_ZONE_INVALIDATED
        """
        if retest is None:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="No retest was detected.",
                rejection_code="RETEST_MISSING",
                score=0.0,
            )

        if retest.status != RetestStatus.CONFIRMED:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Retest is not confirmed.",
                rejection_code="RETEST_NOT_CONFIRMED",
                score=0.0,
            )

        if retest.zone.zone_id != selected_zone.zone_id:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Retest zone does not match the selected entry zone.",
                rejection_code="RETEST_ZONE_MISMATCH",
                score=0.0,
            )

        if retest.zone.direction != expected_direction:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Retest zone direction does not match the expected direction.",
                rejection_code="RETEST_DIRECTION_MISMATCH",
                score=0.0,
            )

        if not retest.zone_interaction_confirmed:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Retest zone interaction is not confirmed.",
                rejection_code="RETEST_ZONE_INTERACTION_MISSING",
                score=0.0,
            )

        if not retest.rejection_candle_confirmed:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Retest rejection candle is not confirmed.",
                rejection_code="RETEST_REJECTION_MISSING",
                score=0.0,
            )

        if not retest.volume_confirmed:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Retest volume confirmation is missing.",
                rejection_code="RETEST_VOLUME_MISSING",
                score=0.0,
            )

        if selected_zone.status == ZoneStatus.MITIGATED or selected_zone.mitigation_timestamp is not None:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Selected zone has already been mitigated.",
                rejection_code="RETEST_ZONE_MITIGATED",
                score=0.0,
            )

        if selected_zone.status == ZoneStatus.INVALIDATED or selected_zone.invalidation_timestamp is not None:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Selected zone has been invalidated.",
                rejection_code="RETEST_ZONE_INVALIDATED",
                score=0.0,
            )

        if selected_zone.status != ZoneStatus.FRESH:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Selected zone is not FRESH.",
                rejection_code="RETEST_ZONE_NOT_FRESH",
                score=0.0,
            )

        return ValidationResult.success(
            layer_name=_LAYER_NAME,
            reason="Confirmed retest with valid zone interaction, rejection candle, and volume confirmation.",
            score=0.0,
        )
