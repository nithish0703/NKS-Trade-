"""
Immutable typed result models for the multi-pair scanner.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.scanner.pipeline_results import StrategyPipelineResult, PipelineStatus
from app.scanner.preview_analyzer import PreviewAnalysisResult


def _is_utc(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() == timezone.utc.utcoffset(value)


class PairScanStatus(str, Enum):
    """Outcome status of scanning a single trading pair in a scan cycle."""

    VALID = "VALID"
    REJECTED = "REJECTED"
    ERROR = "ERROR"
    DUPLICATE = "DUPLICATE"
    SKIPPED = "SKIPPED"


class PairScanResult(BaseModel):
    """Result of scanning a single trading pair once."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    status: PairScanStatus
    pipeline_result: Optional[StrategyPipelineResult] = None
    duplicate_key: Optional[str] = None
    duplicate: bool = False
    started_at_utc: datetime
    completed_at_utc: datetime
    duration_ms: float
    reason: Optional[str] = None
    error_type: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    preview_result: Optional[PreviewAnalysisResult] = None

    @field_validator("started_at_utc", "completed_at_utc")
    @classmethod
    def _timestamps_must_be_utc(cls, value: datetime) -> datetime:
        if not _is_utc(value):
            raise ValueError("Timestamps must be timezone-aware UTC.")
        return value

    @field_validator("duration_ms")
    @classmethod
    def _duration_cannot_be_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("duration_ms cannot be negative.")
        return value

    @model_validator(mode="after")
    def _completed_not_before_started(self) -> "PairScanResult":
        if self.completed_at_utc < self.started_at_utc:
            raise ValueError("completed_at_utc cannot be before started_at_utc.")
        return self

    @model_validator(mode="after")
    def _valid_requires_all_required_conditions_met(self) -> "PairScanResult":
        if self.status == PairScanStatus.VALID:
            if self.pipeline_result is None or self.pipeline_result.status != PipelineStatus.VALID:
                raise ValueError(
                    "A VALID PairScanResult requires a pipeline result with every "
                    "required condition met."
                )
        return self

    @model_validator(mode="after")
    def _duplicate_requires_flag_and_key(self) -> "PairScanResult":
        if self.status == PairScanStatus.DUPLICATE:
            if not self.duplicate or not self.duplicate_key:
                raise ValueError(
                    "A DUPLICATE PairScanResult requires duplicate=True and a duplicate_key."
                )
        return self

    @model_validator(mode="after")
    def _error_requires_type_and_reason(self) -> "PairScanResult":
        if self.status == PairScanStatus.ERROR:
            if not self.error_type or not self.reason:
                raise ValueError("An ERROR PairScanResult requires error_type and reason.")
        return self


class ScanCycleResult(BaseModel):
    """Result of running one complete multi-pair scan cycle."""

    model_config = ConfigDict(frozen=True)

    cycle_id: str
    started_at_utc: datetime
    completed_at_utc: datetime
    duration_ms: float
    configured_pairs: list[str]
    attempted_pairs: list[str]
    valid_results: list[PairScanResult]
    rejected_results: list[PairScanResult]
    duplicate_results: list[PairScanResult]
    error_results: list[PairScanResult]
    skipped_results: list[PairScanResult]
    pair_results: list[PairScanResult]
    total_pairs: int
    valid_count: int
    rejected_count: int
    duplicate_count: int
    error_count: int
    skipped_count: int
    rejection_breakdown: dict[str, int] = {}
    metadata: Optional[dict[str, Any]] = None

    @field_validator("started_at_utc", "completed_at_utc")
    @classmethod
    def _timestamps_must_be_utc(cls, value: datetime) -> datetime:
        if not _is_utc(value):
            raise ValueError("Timestamps must be timezone-aware UTC.")
        return value

    @field_validator("duration_ms")
    @classmethod
    def _duration_cannot_be_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("duration_ms cannot be negative.")
        return value

    @model_validator(mode="after")
    def _completed_not_before_started(self) -> "ScanCycleResult":
        if self.completed_at_utc < self.started_at_utc:
            raise ValueError("completed_at_utc cannot be before started_at_utc.")
        return self

    @model_validator(mode="after")
    def _counts_must_match_collections(self) -> "ScanCycleResult":
        checks = (
            (self.valid_count, self.valid_results, "valid_count"),
            (self.rejected_count, self.rejected_results, "rejected_count"),
            (self.duplicate_count, self.duplicate_results, "duplicate_count"),
            (self.error_count, self.error_results, "error_count"),
            (self.skipped_count, self.skipped_results, "skipped_count"),
        )
        for count, results, name in checks:
            if count != len(results):
                raise ValueError(f"{name} must match the length of its result collection.")
        return self

    @model_validator(mode="after")
    def _total_pairs_must_equal_configured_pairs(self) -> "ScanCycleResult":
        if self.total_pairs != len(self.configured_pairs):
            raise ValueError("total_pairs must equal the configured pair count.")
        return self


class ScannerRuntimeStatus(BaseModel):
    """Current runtime status of the recurring multi-pair scanner."""

    model_config = ConfigDict(frozen=True)

    running: bool
    shutdown_requested: bool
    cycles_completed: int
    last_cycle_started_at: Optional[datetime] = None
    last_cycle_completed_at: Optional[datetime] = None
    last_cycle_id: Optional[str] = None
    last_error: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None

    @field_validator("last_cycle_started_at", "last_cycle_completed_at")
    @classmethod
    def _timestamps_must_be_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and not _is_utc(value):
            raise ValueError("Timestamps must be timezone-aware UTC.")
        return value

    @field_validator("cycles_completed")
    @classmethod
    def _cycles_completed_cannot_be_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("cycles_completed cannot be negative.")
        return value
