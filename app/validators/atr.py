"""
Validates ATR and ATR-expansion sufficiency for a setup.
"""

import math
from typing import Optional

from app.config.thresholds import MARKET_REGIME_MINIMUM_ATR_EXPANSION_RATIO
from app.indicators.results import IndicatorSnapshot
from app.models.validation_result import ValidationResult
from app.validators.results import VolatilityStatus

_LAYER_NAME = "ATR"


def _is_finite(value: Optional[float]) -> bool:
    return value is not None and math.isfinite(value)


class ATRValidator:
    """
    Validates that ATR is available, positive, and sufficiently
    expanded, using the existing configured expansion threshold.
    """

    def __init__(
        self, minimum_atr_expansion_ratio: float = MARKET_REGIME_MINIMUM_ATR_EXPANSION_RATIO
    ) -> None:
        self._minimum_atr_expansion_ratio = minimum_atr_expansion_ratio

    def validate(
        self,
        snapshot: IndicatorSnapshot,
        volatility_status: Optional[VolatilityStatus] = None,
    ) -> ValidationResult:
        """
        Validate ATR and ATR expansion from an IndicatorSnapshot.

        Failure codes:
            - ATR_DATA_MISSING
            - ATR_INVALID
            - ATR_EXPANSION_INSUFFICIENT
        """
        if snapshot is None or not _is_finite(snapshot.atr):
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="ATR is missing.",
                rejection_code="ATR_DATA_MISSING",
                score=0.0,
            )

        if snapshot.atr <= 0:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="ATR is not positive.",
                rejection_code="ATR_INVALID",
                score=0.0,
            )

        if not _is_finite(snapshot.atr_expansion_ratio):
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="ATR expansion ratio is missing.",
                rejection_code="ATR_EXPANSION_INSUFFICIENT",
                score=0.0,
            )

        if snapshot.atr_expansion_ratio < self._minimum_atr_expansion_ratio:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="ATR expansion ratio is below the configured minimum.",
                rejection_code="ATR_EXPANSION_INSUFFICIENT",
                score=0.0,
            )

        if volatility_status in (VolatilityStatus.COMPRESSED, VolatilityStatus.DEAD):
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=f"Volatility status is {volatility_status.value}.",
                rejection_code="ATR_EXPANSION_INSUFFICIENT",
                score=0.0,
            )

        return ValidationResult.success(
            layer_name=_LAYER_NAME,
            reason="ATR_VALID",
            score=0.0,
        )
