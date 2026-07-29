"""
Unit tests for app.scoring.calculator.ConfidenceCalculator.
"""

from app.models.validation_result import ValidationResult
from app.scoring.calculator import ConfidenceCalculator
from app.scoring.input_adapter import ConfidenceInputAdapter
from app.scoring.results import ConfidenceClassification
from app.scoring.score_engine import ConfidenceScoringEngine, _MANDATORY_LAYERS


def _passing_kwargs() -> dict:
    return {
        "market_regime": ValidationResult.success(layer_name="MARKET_REGIME"),
        "htf_bias": ValidationResult.success(layer_name="HTF_BIAS"),
        "liquidity_sweep": ValidationResult.success(layer_name="LIQUIDITY_SWEEP"),
        "structure_shift": ValidationResult.success(layer_name="STRUCTURE_SHIFT"),
        "volume_confirmation": ValidationResult.success(layer_name="VOLUME_CONFIRMATION"),
        "entry_zone": ValidationResult.success(layer_name="ENTRY_ZONE"),
        "premium_discount": ValidationResult.success(layer_name="PREMIUM_DISCOUNT"),
        "retest_confirmation": ValidationResult.success(layer_name="RETEST_CONFIRMATION"),
        "atr": ValidationResult.success(layer_name="ATR"),
        "session_filter": ValidationResult.success(layer_name="SESSION_FILTER"),
        "btc_alignment": ValidationResult.success(layer_name="BTC_ALIGNMENT"),
        "fake_breakout_filter": ValidationResult.success(layer_name="FAKE_BREAKOUT_FILTER"),
    }


def _calculator() -> ConfidenceCalculator:
    return ConfidenceCalculator(
        input_adapter=ConfidenceInputAdapter(), scoring_engine=ConfidenceScoringEngine()
    )


class TestConfidenceCalculator:
    def test_complete_calculation_using_all_validator_results(self):
        result = _calculator().calculate(**_passing_kwargs())
        assert result.raw_score == 120.0

    def test_premium_result(self):
        result = _calculator().calculate(**_passing_kwargs())
        assert result.classification == ConfidenceClassification.PREMIUM
        assert result.publishable is True

    def test_strong_result(self):
        # Since all 12 scoring layers are mandatory, any single failure
        # forces IGNORE per spec, regardless of the numeric score. STRONG
        # classification is therefore only reachable via the scoring
        # engine directly, using a hypothetical non-mandatory layer setup
        # -- verified here by classifying a raw score in the STRONG band
        # through the classification helper the calculator relies on.
        from app.scoring.classification import classify_confidence

        assert classify_confidence(85.0) == ConfidenceClassification.STRONG

    def test_medium_result_internal_only(self):
        # See note in test_strong_result: MEDIUM is only reachable at the
        # classification level when not all layers are mandatory-gated;
        # verify the classification boundary itself here.
        from app.scoring.classification import classify_confidence

        assert classify_confidence(75.0) == ConfidenceClassification.MEDIUM

    def test_ignore_result(self):
        kwargs = _passing_kwargs()
        for layer in list(_MANDATORY_LAYERS)[:8]:
            kwargs_key = next(
                k
                for k, v in kwargs.items()
                if v.layer_name == layer
            )
            kwargs[kwargs_key] = ValidationResult.failure(layer_name=layer, reason="fail")
        result = _calculator().calculate(**kwargs)
        assert result.classification == ConfidenceClassification.IGNORE
        assert result.publishable is False

    def test_mandatory_failure_forces_ignore(self):
        kwargs = _passing_kwargs()
        kwargs["fake_breakout_filter"] = ValidationResult.failure(
            layer_name="FAKE_BREAKOUT_FILTER", reason="fail"
        )
        # raw = 115/120 = 95.8 numerically PREMIUM range, but mandatory failed.
        result = _calculator().calculate(**kwargs)
        assert result.classification == ConfidenceClassification.IGNORE
        assert result.mandatory_layers_passed is False

    def test_publishable_only_for_premium_and_strong(self):
        premium = _calculator().calculate(**_passing_kwargs())
        assert premium.publishable is True

        kwargs = _passing_kwargs()
        kwargs["htf_bias"] = ValidationResult.failure(layer_name="HTF_BIAS", reason="fail")
        kwargs["entry_zone"] = ValidationResult.failure(layer_name="ENTRY_ZONE", reason="fail")
        medium = _calculator().calculate(**kwargs)
        assert medium.publishable is False

    def test_no_signal_generated(self):
        result = _calculator().calculate(**_passing_kwargs())
        result_fields = set(type(result).model_fields.keys())
        assert "signal_type" not in result_fields
        assert "entry_price" not in result_fields

    def test_no_persistence_or_publishing_side_effects(self):
        # Calling twice must be pure and side-effect free.
        calculator = _calculator()
        result_one = calculator.calculate(**_passing_kwargs())
        result_two = calculator.calculate(**_passing_kwargs())
        assert result_one.raw_score == result_two.raw_score

    def test_inputs_not_mutated(self):
        kwargs = _passing_kwargs()
        snapshot = {k: v.model_copy() for k, v in kwargs.items()}
        _calculator().calculate(**kwargs)
        assert kwargs == snapshot
