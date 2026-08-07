"""
Stage decision: aggregates the 5 pipeline stages' pass/fail outcomes
into a single binary CONFIRMED/REJECTED decision.

All 5 stages (HTF Bias, Liquidity Sweep, BOS, IFVG, Volume Profile+CVD) are
hard-mandatory gates -- the calling pipeline only reaches this
aggregation once every stage has already been evaluated. There is no
numeric score, percentage, or confidence tier anywhere in this module:
a setup is CONFIRMED only when every stage passed, and REJECTED
otherwise, with the specific failing stage(s) reported for diagnostics.
"""

from typing import Mapping

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.validation_result import ValidationResult

STAGE_NAMES: tuple[str, ...] = (
    "HTF_BIAS",
    "LIQUIDITY_SWEEP",
    "BOS",
    "IFVG",
    "ORDER_FLOW",
)


class PipelineStageOutcome(BaseModel):
    """Pass/fail outcome for a single pipeline stage. No score of any kind."""

    model_config = ConfigDict(frozen=True)

    stage_name: str
    passed: bool
    reason: str


class PipelineDecisionResult(BaseModel):
    """
    Aggregated binary decision across all 5 pipeline stages for a
    single setup. Carries no raw/normalized score, percentage, or
    classification tier -- `confirmed` is the only outcome field.
    """

    model_config = ConfigDict(frozen=True)

    confirmed: bool
    stage_outcomes: list[PipelineStageOutcome]
    failed_stages: list[str]
    reason: str

    @model_validator(mode="after")
    def _confirmed_requires_no_failed_stages(self) -> "PipelineDecisionResult":
        if self.confirmed and self.failed_stages:
            raise ValueError("confirmed cannot be True when failed_stages is non-empty.")
        if not self.confirmed and not self.failed_stages:
            raise ValueError("A REJECTED decision must report at least one failed stage.")
        return self


def calculate_pipeline_decision(stage_results: Mapping[str, ValidationResult]) -> PipelineDecisionResult:
    """
    Aggregate the pipeline's 5 stage outcomes into a single binary
    CONFIRMED/REJECTED decision.

    Every one of the 5 stages (HTF_BIAS, LIQUIDITY_SWEEP, BOS, IFVG,
    ORDER_FLOW) must be present in `stage_results`. A setup is
    CONFIRMED only when every stage passed; any single failure rejects
    it -- there is no partial credit, weighting, or intermediate tier.

    Raises:
        ValueError: If any required stage is missing from
            `stage_results`, or a supplied result's `layer_name` does
            not match its mapping key.
    """
    missing_stages = [name for name in STAGE_NAMES if name not in stage_results]
    if missing_stages:
        raise ValueError(f"Missing required pipeline stage(s): {', '.join(missing_stages)}.")

    stage_outcomes: list[PipelineStageOutcome] = []
    failed_stages: list[str] = []

    for stage_name in STAGE_NAMES:
        result = stage_results[stage_name]
        if result.layer_name != stage_name:
            raise ValueError(
                f"ValidationResult for '{stage_name}' has mismatched layer_name "
                f"'{result.layer_name}'."
            )

        if not result.passed:
            failed_stages.append(stage_name)

        stage_outcomes.append(
            PipelineStageOutcome(stage_name=stage_name, passed=result.passed, reason=result.reason or "")
        )

    confirmed = not failed_stages
    reason = (
        "All required strategy conditions were satisfied."
        if confirmed
        else f"Required condition(s) failed: {', '.join(failed_stages)}."
    )

    return PipelineDecisionResult(
        confirmed=confirmed,
        stage_outcomes=stage_outcomes,
        failed_stages=failed_stages,
        reason=reason,
    )
