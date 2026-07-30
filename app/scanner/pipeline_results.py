"""
Immutable typed result models for the final strategy pipeline.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.models.market_context import MarketContext
from app.models.validation_result import ValidationResult
from app.risk.results import RiskPlan, RiskPlanStatus
from app.scoring.results import ConfidenceClassification, ConfidenceScoreResult


class PipelineStatus(str, Enum):
    """Overall outcome status of a strategy-pipeline run."""

    VALID = "VALID"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


class PipelineStageResult(BaseModel):
    """Audit record of a single pipeline stage's execution."""

    model_config = ConfigDict(frozen=True)

    stage_order: int
    layer_name: str
    mandatory: bool
    executed: bool
    passed: bool
    reason: Optional[str] = None
    validation_result: Optional[ValidationResult] = None
    duration_ms: float
    metadata: Optional[dict[str, Any]] = None

    @field_validator("stage_order")
    @classmethod
    def _stage_order_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("stage_order must be positive.")
        return value

    @field_validator("duration_ms")
    @classmethod
    def _duration_cannot_be_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("duration_ms cannot be negative.")
        return value

    @model_validator(mode="after")
    def _non_executed_cannot_be_passed(self) -> "PipelineStageResult":
        if not self.executed and self.passed:
            raise ValueError("A non-executed stage cannot be marked passed.")
        return self

    @model_validator(mode="after")
    def _validation_result_requires_execution(self) -> "PipelineStageResult":
        if not self.executed and self.validation_result is not None:
            raise ValueError("validation_result may be present only for executed stages.")
        return self


class StrategyPipelineResult(BaseModel):
    """
    Final internal result of running the complete institutional SMC
    strategy pipeline for a single symbol.

    This model does not include Telegram status, dashboard status,
    exchange-order ID, or deployment state.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    expected_direction: Optional[str] = None
    detection_time_utc: datetime
    status: PipelineStatus
    passed: bool
    failed_layer: Optional[str] = None
    rejection_reason: Optional[str] = None
    market_context: Optional[MarketContext] = None
    stages: list[PipelineStageResult]
    market_regime_result: Optional[ValidationResult] = None
    htf_bias_result: Optional[Any] = None
    liquidity_detection_result: Optional[Any] = None
    liquidity_sweep: Optional[Any] = None
    structure_shift_result: Optional[Any] = None
    selected_structure_break: Optional[Any] = None
    volume_validation: Optional[ValidationResult] = None
    zone_detection_result: Optional[Any] = None
    selected_entry_zone: Optional[Any] = None
    dealing_range_result: Optional[Any] = None
    retest_result: Optional[Any] = None
    session_result: Optional[ValidationResult] = None
    btc_alignment_result: Optional[ValidationResult] = None
    fake_breakout_result: Optional[ValidationResult] = None
    candle_quality_result: Optional[ValidationResult] = None
    risk_plan: Optional[RiskPlan] = None
    confidence_result: Optional[ConfidenceScoreResult] = None
    metadata: Optional[dict[str, Any]] = None

    @field_validator("detection_time_utc")
    @classmethod
    def _detection_time_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("detection_time_utc must be timezone-aware UTC.")
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("detection_time_utc must be in UTC.")
        return value

    @model_validator(mode="after")
    def _passed_requires_valid_status(self) -> "StrategyPipelineResult":
        if self.passed and self.status != PipelineStatus.VALID:
            raise ValueError("passed=True requires status=VALID.")
        return self

    @model_validator(mode="after")
    def _rejected_requires_failure_context(self) -> "StrategyPipelineResult":
        if self.status == PipelineStatus.REJECTED:
            if not self.failed_layer or not self.rejection_reason:
                raise ValueError(
                    "A REJECTED pipeline result requires both failed_layer and rejection_reason."
                )
        return self

    @model_validator(mode="after")
    def _valid_requires_risk_plan(self) -> "StrategyPipelineResult":
        if self.status == PipelineStatus.VALID:
            if self.risk_plan is None or self.risk_plan.status != RiskPlanStatus.VALID:
                raise ValueError("A VALID pipeline result requires a valid RiskPlan.")
        return self

    @model_validator(mode="after")
    def _valid_requires_publishable_confidence(self) -> "StrategyPipelineResult":
        if self.status == PipelineStatus.VALID:
            if self.confidence_result is None or not self.confidence_result.publishable:
                raise ValueError(
                    "A VALID pipeline result requires a publishable ConfidenceScoreResult."
                )
        return self

    @model_validator(mode="after")
    def _valid_requires_all_mandatory_stages_passed(self) -> "StrategyPipelineResult":
        if self.status == PipelineStatus.VALID:
            for stage in self.stages:
                if stage.mandatory and stage.executed and not stage.passed:
                    raise ValueError(
                        f"A VALID pipeline result cannot contain a failed mandatory "
                        f"executed stage: '{stage.layer_name}'."
                    )
        return self
