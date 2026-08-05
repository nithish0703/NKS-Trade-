"""
Unit tests for app.strategy_pipeline.scoring.calculate_pipeline_decision.
"""

import pytest

from app.models.validation_result import ValidationResult
from app.strategy_pipeline.scoring import STAGE_NAMES, calculate_pipeline_decision

ALL_PASSING = {
    stage: ValidationResult.success(layer_name=stage, reason="ok") for stage in STAGE_NAMES
}


def _with_failure(stage_name: str) -> dict:
    results = dict(ALL_PASSING)
    results[stage_name] = ValidationResult.failure(
        layer_name=stage_name, reason="fail", rejection_code="X"
    )
    return results


class TestCalculatePipelineDecision:
    def test_all_stages_pass_gives_confirmed(self):
        result = calculate_pipeline_decision(ALL_PASSING)
        assert result.confirmed is True
        assert result.failed_stages == []

    def test_any_single_stage_failure_rejects(self):
        for stage_name in STAGE_NAMES:
            result = calculate_pipeline_decision(_with_failure(stage_name))
            assert result.confirmed is False
            assert result.failed_stages == [stage_name]

    def test_two_stage_failures_are_both_reported(self):
        results = dict(ALL_PASSING)
        results["HTF_BIAS"] = ValidationResult.failure(layer_name="HTF_BIAS", reason="fail")
        results["BOS"] = ValidationResult.failure(layer_name="BOS", reason="fail")
        result = calculate_pipeline_decision(results)
        assert result.confirmed is False
        assert set(result.failed_stages) == {"HTF_BIAS", "BOS"}

    def test_missing_stage_raises_value_error(self):
        results = dict(ALL_PASSING)
        del results["IFVG"]
        with pytest.raises(ValueError):
            calculate_pipeline_decision(results)

    def test_mismatched_layer_name_raises_value_error(self):
        results = dict(ALL_PASSING)
        results["IFVG"] = ValidationResult.success(layer_name="WRONG_NAME", reason="ok")
        with pytest.raises(ValueError):
            calculate_pipeline_decision(results)

    def test_all_failing_gives_rejected_with_every_stage_listed(self):
        results = {
            stage: ValidationResult.failure(layer_name=stage, reason="fail") for stage in STAGE_NAMES
        }
        result = calculate_pipeline_decision(results)
        assert result.confirmed is False
        assert set(result.failed_stages) == set(STAGE_NAMES)

    def test_stage_outcomes_carry_no_score_fields(self):
        result = calculate_pipeline_decision(ALL_PASSING)
        for outcome in result.stage_outcomes:
            assert not hasattr(outcome, "awarded_points")
            assert not hasattr(outcome, "maximum_points")
            assert outcome.passed is True

    def test_failed_stage_outcome_carries_its_reason(self):
        result = calculate_pipeline_decision(_with_failure("ORDER_FLOW"))
        failed_outcome = next(o for o in result.stage_outcomes if o.stage_name == "ORDER_FLOW")
        assert failed_outcome.passed is False
        assert failed_outcome.reason == "fail"

    def test_reason_string_lists_failed_stages(self):
        result = calculate_pipeline_decision(_with_failure("HTF_BIAS"))
        assert "HTF_BIAS" in result.reason

    def test_confirmed_reason_is_generic_success_message(self):
        result = calculate_pipeline_decision(ALL_PASSING)
        assert "satisfied" in result.reason.lower()

    def test_result_has_no_score_percentage_or_classification_fields(self):
        result = calculate_pipeline_decision(ALL_PASSING)
        assert not hasattr(result, "raw_score")
        assert not hasattr(result, "normalized_score")
        assert not hasattr(result, "classification")
        assert not hasattr(result, "publishable")
