"""
Validates alignment with higher timeframe (HTF) bias.
"""

from app.market_structure.results import HigherTimeframeBias, HigherTimeframeBiasResult
from app.models.validation_result import ValidationResult

_LAYER_NAME = "HTF_BIAS"


class HigherTimeframeBiasValidator:
    """
    Validates that a HigherTimeframeBiasResult supports the expected
    trade direction. Score always remains zero here; scoring is applied
    later by the confidence-scoring engine.
    """

    def validate(self, result: HigherTimeframeBiasResult, expected_direction: str) -> ValidationResult:
        """
        Validate HTF bias alignment.

        Pass only when BUY has final_bias BULLISH with aligned=True, or
        SELL has final_bias BEARISH with aligned=True.

        Failure codes:
            - HTF_BIAS_MISSING
            - HTF_BIAS_MIXED
            - HTF_BIAS_UNKNOWN
            - HTF_BIAS_DIRECTION_MISMATCH
        """
        if result is None:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="No higher-timeframe bias result was provided.",
                rejection_code="HTF_BIAS_MISSING",
                score=0.0,
            )

        if result.final_bias == HigherTimeframeBias.MIXED:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Higher-timeframe bias is MIXED.",
                rejection_code="HTF_BIAS_MIXED",
                score=0.0,
            )

        if result.final_bias == HigherTimeframeBias.UNKNOWN:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Higher-timeframe bias is UNKNOWN.",
                rejection_code="HTF_BIAS_UNKNOWN",
                score=0.0,
            )

        if expected_direction == "BUY":
            required_bias = HigherTimeframeBias.BULLISH
        elif expected_direction == "SELL":
            required_bias = HigherTimeframeBias.BEARISH
        else:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=f"Unrecognized expected_direction '{expected_direction}'.",
                rejection_code="HTF_BIAS_DIRECTION_MISMATCH",
                score=0.0,
            )

        if result.final_bias != required_bias or not result.aligned:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=(
                    f"Higher-timeframe bias '{result.final_bias.value}' (aligned="
                    f"{result.aligned}) does not support the expected {expected_direction} direction."
                ),
                rejection_code="HTF_BIAS_DIRECTION_MISMATCH",
                score=0.0,
            )

        return ValidationResult.success(
            layer_name=_LAYER_NAME,
            reason="HTF_BIAS_VALID",
            score=0.0,
        )
