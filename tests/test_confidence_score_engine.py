"""
Unit tests for app.scoring.score_engine.ConfidenceScoringEngine.
"""

import pytest

from app.models.validation_result import ValidationResult
from app.scoring.results import ConfidenceClassification
from app.scoring.score_engine import (
    ConfidenceScoringEngine,
    ConfidenceScoringError,
    _ALL_LAYERS,
    _HARD_LAYERS,
    _SOFT_LAYERS,
)

ALL_PASSING = {
    layer: ValidationResult.success(layer_name=layer, reason="ok") for layer in _ALL_LAYERS
}


def _with_failure(layer_name: str) -> dict:
    results = dict(ALL_PASSING)
    results[layer_name] = ValidationResult.failure(layer_name=layer_name, reason="fail", rejection_code="X")
    return results


class TestConfidenceScoringEngine:
    def test_all_layers_pass_gives_raw_score_115(self):
        result = ConfidenceScoringEngine().calculate(ALL_PASSING)
        assert result.raw_score == 115.0

    def test_all_layers_pass_gives_normalized_score_100(self):
        result = ConfidenceScoringEngine().calculate(ALL_PASSING)
        assert result.normalized_score == 100.0

    def test_all_layers_pass_gives_premium(self):
        result = ConfidenceScoringEngine().calculate(ALL_PASSING)
        assert result.classification == ConfidenceClassification.PREMIUM
        assert result.publishable is True

    def test_five_point_soft_layer_failure_reduces_raw_score_correctly(self):
        results = _with_failure("SESSION_FILTER")
        result = ConfidenceScoringEngine().calculate(results)
        assert result.raw_score == 110.0

    def test_htf_bias_failure_removes_25_points(self):
        results = _with_failure("HTF_BIAS")
        result = ConfidenceScoringEngine().calculate(results)
        assert result.raw_score == 90.0

    def test_missing_layer_raises_error(self):
        results = dict(ALL_PASSING)
        del results["SESSION_FILTER"]
        with pytest.raises(ConfidenceScoringError):
            ConfidenceScoringEngine().calculate(results)

    def test_soft_layer_failure_does_not_force_ignore(self):
        # Only 5 points lost (110/115 = 95.65) -- still PREMIUM, not IGNORE.
        results = _with_failure("SESSION_FILTER")
        result = ConfidenceScoringEngine().calculate(results)
        assert result.classification == ConfidenceClassification.PREMIUM
        assert result.mandatory_layers_passed is True

    def test_hard_layer_failure_forces_ignore_even_if_score_above_80(self):
        # HTF_BIAS failing leaves raw 90/115 = 78.3, but a hard-layer
        # failure forces IGNORE regardless of the numeric score.
        results = _with_failure("HTF_BIAS")
        result = ConfidenceScoringEngine().calculate(results)
        assert result.classification == ConfidenceClassification.IGNORE
        assert result.mandatory_layers_passed is False
        assert result.publishable is False

    def test_no_partial_points_awarded(self):
        result = ConfidenceScoringEngine().calculate(ALL_PASSING)
        for layer_score in result.layer_scores:
            assert layer_score.awarded_points in (0.0, layer_score.maximum_points)

    def test_deterministic_layer_order(self):
        result_one = ConfidenceScoringEngine().calculate(ALL_PASSING)
        result_two = ConfidenceScoringEngine().calculate(ALL_PASSING)
        names_one = [ls.layer_name for ls in result_one.layer_scores]
        names_two = [ls.layer_name for ls in result_two.layer_scores]
        assert names_one == names_two

    def test_maximum_raw_score_is_exactly_115(self):
        result = ConfidenceScoringEngine().calculate(ALL_PASSING)
        assert result.maximum_raw_score == 115

    def test_normalized_calculation_correct(self):
        results = _with_failure("MARKET_REGIME")  # raw = 100
        result = ConfidenceScoringEngine().calculate(results)
        assert result.normalized_score == pytest.approx(round((100 / 115) * 100, 2))

    def test_result_does_not_contain_trade_fields(self):
        result = ConfidenceScoringEngine().calculate(ALL_PASSING)
        result_fields = set(type(result).model_fields.keys())
        forbidden = {"entry_price", "stop_loss", "take_profit", "trade_id"}
        assert result_fields.isdisjoint(forbidden)

    def test_input_validations_not_mutated(self):
        snapshot = {k: v.model_copy() for k, v in ALL_PASSING.items()}
        ConfidenceScoringEngine().calculate(ALL_PASSING)
        assert ALL_PASSING == snapshot

    def test_mismatched_layer_name_raises_error(self):
        results = dict(ALL_PASSING)
        results["SESSION_FILTER"] = ValidationResult.success(layer_name="WRONG_NAME", reason="ok")
        with pytest.raises(ConfidenceScoringError):
            ConfidenceScoringEngine().calculate(results)

    def test_hard_layers_marked_mandatory_in_layer_scores(self):
        result = ConfidenceScoringEngine().calculate(ALL_PASSING)
        for layer_score in result.layer_scores:
            expected_mandatory = layer_score.layer_name in _HARD_LAYERS
            assert layer_score.mandatory == expected_mandatory

    def test_soft_layers_never_appear_in_failed_mandatory_layers(self):
        results = dict(ALL_PASSING)
        for layer in _SOFT_LAYERS:
            results = _with_failure(layer) if layer not in results else results
        for layer in _SOFT_LAYERS:
            results[layer] = ValidationResult.failure(layer_name=layer, reason="fail")
        result = ConfidenceScoringEngine().calculate(results)
        assert result.failed_mandatory_layers == []
        assert result.mandatory_layers_passed is True
