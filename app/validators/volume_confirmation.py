"""
Validates volume confirmation for the setup.
"""

from typing import Optional

from app.market_structure.shift_results import DisplacementResult
from app.models.validation_result import ValidationResult
from app.zones.retest_results import RetestResult

_LAYER_NAME = "VOLUME_CONFIRMATION"


class VolumeConfirmationValidator:
    """
    Validates that both the confirming displacement candle and the
    confirming retest candle show volume confirmation.
    """

    def validate(
        self,
        displacement: Optional[DisplacementResult],
        retest: Optional[RetestResult],
    ) -> ValidationResult:
        """
        Validate displacement and retest volume confirmation.

        Failure codes:
            - VOLUME_DATA_MISSING
            - DISPLACEMENT_VOLUME_NOT_CONFIRMED
            - RETEST_VOLUME_NOT_CONFIRMED
        """
        if displacement is None or retest is None:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Displacement or retest data is missing.",
                rejection_code="VOLUME_DATA_MISSING",
                score=0.0,
            )

        if not displacement.confirmed or not displacement.volume_confirmed:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Displacement candle volume is not confirmed.",
                rejection_code="DISPLACEMENT_VOLUME_NOT_CONFIRMED",
                score=0.0,
            )

        if not retest.volume_confirmed:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Retest candle volume is not confirmed.",
                rejection_code="RETEST_VOLUME_NOT_CONFIRMED",
                score=0.0,
            )

        return ValidationResult.success(
            layer_name=_LAYER_NAME,
            reason="VOLUME_CONFIRMATION_VALID",
            score=0.0,
        )
