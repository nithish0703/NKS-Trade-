"""
Validates that price is within an acceptable entry zone.
"""

from app.models.trade_zone import ZoneStatus, ZoneType
from app.models.validation_result import ValidationResult
from app.zones.results import ZoneDetectionResult

_LAYER_NAME = "entry_zone"
_VALID_ZONE_TYPES = (ZoneType.ORDER_BLOCK, ZoneType.BREAKER_BLOCK, ZoneType.FAIR_VALUE_GAP)


class EntryZoneValidator:
    """
    Validates that a ZoneDetectionResult contains a genuinely usable
    selected entry zone for the later strategy pipeline.

    This validator does not calculate confidence points; score always
    remains zero at this step.
    """

    def validate(self, result: ZoneDetectionResult, expected_direction: str) -> ValidationResult:
        """
        Validate the selected zone in `result`.

        Failure reasons (via rejection_code):
            - ENTRY_ZONE_MISSING
            - ENTRY_ZONE_NOT_FRESH
            - ENTRY_ZONE_MITIGATED
            - ENTRY_ZONE_INVALIDATED
            - ENTRY_ZONE_DIRECTION_MISMATCH
            - ENTRY_ZONE_TYPE_INVALID
        """
        zone = result.selected_zone

        if zone is None:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="No entry zone was selected.",
                rejection_code="ENTRY_ZONE_MISSING",
                score=0.0,
            )

        if zone.status != ZoneStatus.FRESH:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=f"Selected zone status is '{zone.status.value}', not FRESH.",
                rejection_code="ENTRY_ZONE_NOT_FRESH",
                score=0.0,
            )

        if zone.mitigation_timestamp is not None:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Selected zone has already been mitigated.",
                rejection_code="ENTRY_ZONE_MITIGATED",
                score=0.0,
            )

        if zone.invalidation_timestamp is not None:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Selected zone has been invalidated.",
                rejection_code="ENTRY_ZONE_INVALIDATED",
                score=0.0,
            )

        if zone.direction != expected_direction:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=(
                    f"Selected zone direction '{zone.direction}' does not match "
                    f"expected direction '{expected_direction}'."
                ),
                rejection_code="ENTRY_ZONE_DIRECTION_MISMATCH",
                score=0.0,
            )

        if zone.zone_type not in _VALID_ZONE_TYPES:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=f"Selected zone type '{zone.zone_type.value}' is not a valid entry zone type.",
                rejection_code="ENTRY_ZONE_TYPE_INVALID",
                score=0.0,
            )

        return ValidationResult.success(
            layer_name=_LAYER_NAME,
            reason=f"Valid fresh {zone.zone_type.value} entry zone selected.",
            score=0.0,
        )
