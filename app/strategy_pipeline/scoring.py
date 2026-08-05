"""
Stage scoring: aggregates the 5 pipeline stages' pass/fail outcomes into
a 0-100 confidence score and PREMIUM/STRONG/MEDIUM/IGNORE classification.

All 5 stages (HTF Bias, Liquidity Sweep, BOS, IFVG, OI+CVD) are equally
weighted at 20 points each and are all hard-mandatory gates -- the
calling pipeline only reaches scoring once every stage has already
passed, so in practice every scored setup has a raw score of 100 and
classifies as PREMIUM. The per-stage LayerScore breakdown and
classification machinery are still computed the same way the legacy
scoring engine did (reusing `classify_confidence`/`is_publishable`
unchanged), so this stays a genuine aggregation step rather than a
hardcoded constant -- and if a future stage gains partial/graded
sub-scoring instead of a flat pass/fail, this module accommodates it
without a reshape.
"""

from typing import Mapping

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.models.validation_result import ValidationResult
from app.scoring.classification import classify_confidence, is_publishable
from app.scoring.results import ConfidenceClassification

STAGE_MAXIMUM_POINTS = 20.0
PIPELINE_MAXIMUM_RAW_SCORE = 100.0

STAGE_NAMES: tuple[str, ...] = (
    "HTF_BIAS",
    "LIQUIDITY_SWEEP",
    "BOS",
    "IFVG",
    "ORDER_FLOW",
)

_STAGE_WEIGHTS: Mapping[str, float] = {name: STAGE_MAXIMUM_POINTS for name in STAGE_NAMES}

if sum(_STAGE_WEIGHTS.values()) != PIPELINE_MAXIMUM_RAW_SCORE:
    raise AssertionError(
        f"Pipeline stage weights must sum to {PIPELINE_MAXIMUM_RAW_SCORE}, "
        f"got {sum(_STAGE_WEIGHTS.values())}."
    )


class PipelineStageScore(BaseModel):
    """Points awarded (or withheld) for a single pipeline stage."""

    model_config = ConfigDict(frozen=True)

    stage_name: str
    maximum_points: float
    awarded_points: float
    passed: bool
    reason: str

    @field_validator("maximum_points", "awarded_points")
    @classmethod
    def _points_cannot_be_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("maximum_points and awarded_points cannot be negative.")
        return value

    @model_validator(mode="after")
    def _awarded_cannot_exceed_maximum(self) -> "PipelineStageScore":
        if self.awarded_points > self.maximum_points:
            raise ValueError("awarded_points cannot exceed maximum_points.")
        return self

    @model_validator(mode="after")
    def _failed_stage_receives_zero_points(self) -> "PipelineStageScore":
        if not self.passed and self.awarded_points != 0:
            raise ValueError("A failed stage must receive zero points.")
        return self


class PipelineScoreResult(BaseModel):
    """
    Aggregated result of pipeline-stage scoring, normalization, and
    classification for a single setup.
    """

    model_config = ConfigDict(frozen=True)

    raw_score: float
    maximum_raw_score: float
    normalized_score: float
    classification: ConfidenceClassification
    publishable: bool
    all_stages_passed: bool
    stage_scores: list[PipelineStageScore]
    failed_stages: list[str]
    reason: str

    @field_validator("raw_score")
    @classmethod
    def _raw_score_must_be_in_range(cls, value: float) -> float:
        if value < 0 or value > PIPELINE_MAXIMUM_RAW_SCORE:
            raise ValueError(f"raw_score must be between 0 and {PIPELINE_MAXIMUM_RAW_SCORE}.")
        return value

    @field_validator("normalized_score")
    @classmethod
    def _normalized_score_must_be_in_range(cls, value: float) -> float:
        if value < 0 or value > 100:
            raise ValueError("normalized_score must be between 0 and 100.")
        return value

    @model_validator(mode="after")
    def _publishable_requires_premium_or_strong(self) -> "PipelineScoreResult":
        if self.publishable and self.classification not in (
            ConfidenceClassification.PREMIUM,
            ConfidenceClassification.STRONG,
        ):
            raise ValueError("publishable can be True only for PREMIUM or STRONG.")
        return self

    @model_validator(mode="after")
    def _all_stages_passed_consistency(self) -> "PipelineScoreResult":
        if self.failed_stages and self.all_stages_passed:
            raise ValueError("all_stages_passed must be False when any stage failed.")
        return self


def calculate_pipeline_score(stage_results: Mapping[str, ValidationResult]) -> PipelineScoreResult:
    """
    Calculate the pipeline's confidence score from a mapping of stage
    name to ValidationResult.

    Every one of the 5 stages (HTF_BIAS, LIQUIDITY_SWEEP, BOS, IFVG,
    ORDER_FLOW) must be present in `stage_results`. Each stage awards
    its full 20 points if `passed`, else zero -- no partial credit.

    Raises:
        ValueError: If any required stage is missing from
            `stage_results`, or a supplied result's `layer_name` does
            not match its mapping key.
    """
    missing_stages = [name for name in STAGE_NAMES if name not in stage_results]
    if missing_stages:
        raise ValueError(f"Missing required pipeline stage(s): {', '.join(missing_stages)}.")

    stage_scores: list[PipelineStageScore] = []
    failed_stages: list[str] = []
    raw_score = 0.0

    for stage_name in STAGE_NAMES:
        result = stage_results[stage_name]
        if result.layer_name != stage_name:
            raise ValueError(
                f"ValidationResult for '{stage_name}' has mismatched layer_name "
                f"'{result.layer_name}'."
            )

        maximum_points = _STAGE_WEIGHTS[stage_name]
        awarded_points = maximum_points if result.passed else 0.0
        raw_score += awarded_points

        if not result.passed:
            failed_stages.append(stage_name)

        stage_scores.append(
            PipelineStageScore(
                stage_name=stage_name,
                maximum_points=maximum_points,
                awarded_points=awarded_points,
                passed=result.passed,
                reason=result.reason or "",
            )
        )

    all_stages_passed = not failed_stages
    normalized_score = round((raw_score / PIPELINE_MAXIMUM_RAW_SCORE) * 100, 2)

    if not all_stages_passed:
        classification = ConfidenceClassification.IGNORE
        publishable = False
        reason = "STAGE_FAILED"
    else:
        classification = classify_confidence(normalized_score)
        publishable = is_publishable(classification)
        if classification == ConfidenceClassification.PREMIUM:
            reason = "PREMIUM_CONFIDENCE"
        elif classification == ConfidenceClassification.STRONG:
            reason = "STRONG_CONFIDENCE"
        elif classification == ConfidenceClassification.MEDIUM:
            reason = "MEDIUM_INTERNAL_ONLY"
        else:
            reason = "IGNORE_LOW_CONFIDENCE"

    return PipelineScoreResult(
        raw_score=raw_score,
        maximum_raw_score=PIPELINE_MAXIMUM_RAW_SCORE,
        normalized_score=normalized_score,
        classification=classification,
        publishable=publishable,
        all_stages_passed=all_stages_passed,
        stage_scores=stage_scores,
        failed_stages=failed_stages,
        reason=reason,
    )
