"""
Unit tests for app.strategy_pipeline.scoring.calculate_pipeline_score.
"""

import pytest

from app.models.validation_result import ValidationResult
from app.scoring.results import ConfidenceClassification
from app.strategy_pipeline.scoring import (
    STAGE_NAMES,
    calculate_pipeline_score,
)

ALL_PASSING = {
    stage: ValidationResult.success(layer_name=stage, reason="ok") for stage in STAGE_NAMES
}


def _with_failure(stage_name: str) -> dict:
    results = dict(ALL_PASSING)
    results[stage_name] = ValidationResult.failure(
        layer_name=stage_name, reason="fail", rejection_code="X"
    )
    return results


class TestCalculatePipelineScore:
    def test_all_stages_pass_gives_raw_score_100(self):
        result = calculate_pipeline_score(ALL_PASSING)
        assert result.raw_score == 100.0

    def test_all_stages_pass_gives_normalized_score_100(self):
        result = calculate_pipeline_score(ALL_PASSING)
        assert result.normalized_score == 100.0

    def test_all_stages_pass_gives_premium_and_publishable(self):
        result = calculate_pipeline_score(ALL_PASSING)
        assert result.classification == ConfidenceClassification.PREMIUM
        assert result.publishable is True
        assert result.all_stages_passed is True
        assert result.failed_stages == []

    def test_each_stage_is_worth_20_points(self):
        for stage_name in STAGE_NAMES:
            results = _with_failure(stage_name)
            result = calculate_pipeline_score(results)
            assert result.raw_score == 80.0, f"{stage_name} failure should cost exactly 20 points"

    def test_any_single_stage_failure_forces_ignore_and_unpublishable(self):
        # Every stage is hard-mandatory: a single failure must force
        # IGNORE/not-publishable regardless of the resulting numeric
        # score (80/100 would otherwise classify as STRONG).
        for stage_name in STAGE_NAMES:
            results = _with_failure(stage_name)
            result = calculate_pipeline_score(results)
            assert result.classification == ConfidenceClassification.IGNORE
            assert result.publishable is False
            assert result.all_stages_passed is False
            assert result.failed_stages == [stage_name]

    def test_two_stage_failures_gives_raw_score_60(self):
        results = dict(ALL_PASSING)
        results["HTF_BIAS"] = ValidationResult.failure(layer_name="HTF_BIAS", reason="fail")
        results["BOS"] = ValidationResult.failure(layer_name="BOS", reason="fail")
        result = calculate_pipeline_score(results)
        assert result.raw_score == 60.0
        assert set(result.failed_stages) == {"HTF_BIAS", "BOS"}

    def test_missing_stage_raises_value_error(self):
        results = dict(ALL_PASSING)
        del results["IFVG"]
        with pytest.raises(ValueError):
            calculate_pipeline_score(results)

    def test_mismatched_layer_name_raises_value_error(self):
        results = dict(ALL_PASSING)
        results["IFVG"] = ValidationResult.success(layer_name="WRONG_NAME", reason="ok")
        with pytest.raises(ValueError):
            calculate_pipeline_score(results)

    def test_all_failing_gives_raw_score_zero(self):
        results = {
            stage: ValidationResult.failure(layer_name=stage, reason="fail") for stage in STAGE_NAMES
        }
        result = calculate_pipeline_score(results)
        assert result.raw_score == 0.0
        assert result.normalized_score == 0.0
        assert result.classification == ConfidenceClassification.IGNORE
        assert result.publishable is False

    def test_stage_scores_report_correct_maximum_points(self):
        result = calculate_pipeline_score(ALL_PASSING)
        for stage_score in result.stage_scores:
            assert stage_score.maximum_points == 20.0
            assert stage_score.awarded_points == 20.0
            assert stage_score.passed is True

    def test_failed_stage_score_carries_its_reason(self):
        results = _with_failure("ORDER_FLOW")
        result = calculate_pipeline_score(results)
        failed_score = next(s for s in result.stage_scores if s.stage_name == "ORDER_FLOW")
        assert failed_score.passed is False
        assert failed_score.awarded_points == 0.0
        assert failed_score.reason == "fail"

    def test_reason_string_reflects_classification(self):
        assert calculate_pipeline_score(ALL_PASSING).reason == "PREMIUM_CONFIDENCE"

        failed = calculate_pipeline_score(_with_failure("HTF_BIAS"))
        assert failed.reason == "STAGE_FAILED"
