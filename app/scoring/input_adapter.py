"""
Adapts individual validator ValidationResult objects into the exact
scoring-layer mapping required by the confidence scoring engine.
"""

from app.models.validation_result import ValidationResult

from app.scoring.score_engine import ConfidenceScoringError

# Deterministic mapping from constructor keyword to scoring layer name.
_LAYER_KEY_TO_NAME: dict[str, str] = {
    "market_regime": "MARKET_REGIME",
    "htf_bias": "HTF_BIAS",
    "liquidity_sweep": "LIQUIDITY_SWEEP",
    "structure_shift": "STRUCTURE_SHIFT",
    "volume_confirmation": "VOLUME_CONFIRMATION",
    "entry_zone": "ENTRY_ZONE",
    "premium_discount": "PREMIUM_DISCOUNT",
    "retest_confirmation": "RETEST_CONFIRMATION",
    "atr": "ATR",
    "session_filter": "SESSION_FILTER",
    "btc_alignment": "BTC_ALIGNMENT",
    "fake_breakout_filter": "FAKE_BREAKOUT_FILTER",
}


class ConfidenceInputAdapter:
    """
    Builds the exact `{layer_name: ValidationResult}` mapping expected by
    `ConfidenceScoringEngine`, without recalculating any validation logic.
    """

    def build_validation_map(
        self,
        *,
        market_regime: ValidationResult,
        htf_bias: ValidationResult,
        liquidity_sweep: ValidationResult,
        structure_shift: ValidationResult,
        volume_confirmation: ValidationResult,
        entry_zone: ValidationResult,
        premium_discount: ValidationResult,
        retest_confirmation: ValidationResult,
        atr: ValidationResult,
        session_filter: ValidationResult,
        btc_alignment: ValidationResult,
        fake_breakout_filter: ValidationResult,
    ) -> dict[str, ValidationResult]:
        """
        Build the scoring-layer validation map.

        Raises:
            ConfidenceScoringError: If any supplied ValidationResult's
                `layer_name` does not match its expected scoring-layer
                name.
        """
        supplied = {
            "market_regime": market_regime,
            "htf_bias": htf_bias,
            "liquidity_sweep": liquidity_sweep,
            "structure_shift": structure_shift,
            "volume_confirmation": volume_confirmation,
            "entry_zone": entry_zone,
            "premium_discount": premium_discount,
            "retest_confirmation": retest_confirmation,
            "atr": atr,
            "session_filter": session_filter,
            "btc_alignment": btc_alignment,
            "fake_breakout_filter": fake_breakout_filter,
        }

        validation_map: dict[str, ValidationResult] = {}
        for key, expected_layer_name in _LAYER_KEY_TO_NAME.items():
            result = supplied[key]
            if result is None:
                raise ConfidenceScoringError(f"Missing required validation result for '{key}'.")
            if result.layer_name != expected_layer_name:
                raise ConfidenceScoringError(
                    f"ValidationResult supplied for '{key}' has layer_name "
                    f"'{result.layer_name}', expected '{expected_layer_name}'."
                )
            if expected_layer_name in validation_map:
                raise ConfidenceScoringError(
                    f"Duplicate validation result for layer '{expected_layer_name}'."
                )
            validation_map[expected_layer_name] = result

        return validation_map
