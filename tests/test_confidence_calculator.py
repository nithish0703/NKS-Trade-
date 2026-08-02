"""
Unit tests for app.scoring.calculator.ConfidenceCalculator.
"""

from app.models.validation_result import ValidationResult
from app.scoring.calculator import ConfidenceCalculator
from app.scoring.input_adapter import ConfidenceInputAdapter
from app.scoring.results import ConfidenceClassification
from app.scoring.score_engine import ConfidenceScoringEngine, _SOFT_LAYERS


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
        assert result.raw_score == 115.0

    def test_premium_result(self):
        result = _calculator().calculate(**_passing_kwargs())
        assert result.classification == ConfidenceClassification.PREMIUM
        assert result.publishable is True

    def test_soft_layer_failure_does_not_force_ignore(self):
        # Soft layers (e.g. FAKE_BREAKOUT_FILTER) only cost their own
        # points; a single failure should not force IGNORE if the
        # resulting normalized score still lands in a publishable band.
        # raw = 110/115 = 95.65 -> still PREMIUM.
        kwargs = _passing_kwargs()
        kwargs["fake_breakout_filter"] = ValidationResult.failure(
            layer_name="FAKE_BREAKOUT_FILTER", reason="fail"
        )
        result = _calculator().calculate(**kwargs)
        assert result.mandatory_layers_passed is True
        assert result.classification == ConfidenceClassification.PREMIUM
        assert result.raw_score == 110.0

    def test_strong_result_reachable_via_soft_layer_failures(self):
        # Fail 3 of 5 soft layers: raw = 100/115 = 86.96 -> STRONG.
        kwargs = _passing_kwargs()
        for layer in ("PREMIUM_DISCOUNT", "RETEST_CONFIRMATION", "SESSION_FILTER"):
            kwargs_key = next(k for k, v in kwargs.items() if v.layer_name == layer)
            kwargs[kwargs_key] = ValidationResult.failure(layer_name=layer, reason="fail")
        result = _calculator().calculate(**kwargs)
        assert result.classification == ConfidenceClassification.STRONG
        assert result.publishable is True

    def test_medium_result_internal_only(self):
        # Fail all 5 soft layers: raw = 90/115 = 78.26 -> MEDIUM, not publishable.
        kwargs = _passing_kwargs()
        for layer in _SOFT_LAYERS:
            kwargs_key = next(k for k, v in kwargs.items() if v.layer_name == layer)
            kwargs[kwargs_key] = ValidationResult.failure(layer_name=layer, reason="fail")
        result = _calculator().calculate(**kwargs)
        assert result.classification == ConfidenceClassification.MEDIUM
        assert result.publishable is False
        assert result.mandatory_layers_passed is True

    def test_hard_layer_failure_forces_ignore(self):
        kwargs = _passing_kwargs()
        kwargs["htf_bias"] = ValidationResult.failure(layer_name="HTF_BIAS", reason="fail")
        result = _calculator().calculate(**kwargs)
        assert result.classification == ConfidenceClassification.IGNORE
        assert result.mandatory_layers_passed is False
        assert result.publishable is False

    def test_publishable_only_for_premium_and_strong(self):
        premium = _calculator().calculate(**_passing_kwargs())
        assert premium.publishable is True

        kwargs = _passing_kwargs()
        kwargs["htf_bias"] = ValidationResult.failure(layer_name="HTF_BIAS", reason="fail")
        ignored = _calculator().calculate(**kwargs)
        assert ignored.publishable is False

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
