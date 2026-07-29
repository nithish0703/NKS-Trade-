"""
Immutable typed result models for confidence scoring and classification.
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_MAXIMUM_RAW_SCORE = 120


class ConfidenceClassification(str, Enum):
    """Final confidence classification tier for a scored setup."""

    PREMIUM = "PREMIUM"
    STRONG = "STRONG"
    MEDIUM = "MEDIUM"
    IGNORE = "IGNORE"


class LayerScore(BaseModel):
    """
    Points awarded (or withheld) for a single scoring layer, based on an
    already-computed ValidationResult. This model does not recalculate
    any validation logic.
    """

    model_config = ConfigDict(frozen=True)

    layer_name: str
    maximum_points: float
    awarded_points: float
    passed: bool
    mandatory: bool
    reason: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None

    @field_validator("maximum_points", "awarded_points")
    @classmethod
    def _points_cannot_be_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("maximum_points and awarded_points cannot be negative.")
        return value

    @model_validator(mode="after")
    def _awarded_cannot_exceed_maximum(self) -> "LayerScore":
        if self.awarded_points > self.maximum_points:
            raise ValueError("awarded_points cannot exceed maximum_points.")
        return self

    @model_validator(mode="after")
    def _failed_mandatory_layer_receives_zero_points(self) -> "LayerScore":
        if self.mandatory and not self.passed and self.awarded_points != 0:
            raise ValueError("A failed mandatory layer must receive zero points.")
        return self


class ConfidenceScoreResult(BaseModel):
    """
    Aggregated result of confidence scoring, normalization, and
    classification for a single setup.

    This model does not include entry price, stop loss, take profit,
    trade ID, or Telegram/dashboard fields.
    """

    model_config = ConfigDict(frozen=True)

    raw_score: float
    maximum_raw_score: float
    normalized_score: float
    classification: ConfidenceClassification
    publishable: bool
    mandatory_layers_passed: bool
    layer_scores: list[LayerScore]
    failed_mandatory_layers: list[str]
    reason: str
    metadata: Optional[dict[str, Any]] = None

    @field_validator("maximum_raw_score")
    @classmethod
    def _maximum_raw_score_must_equal_120(cls, value: float) -> float:
        if value != _MAXIMUM_RAW_SCORE:
            raise ValueError(f"maximum_raw_score must equal {_MAXIMUM_RAW_SCORE}.")
        return value

    @field_validator("raw_score")
    @classmethod
    def _raw_score_must_be_in_range(cls, value: float) -> float:
        if value < 0 or value > _MAXIMUM_RAW_SCORE:
            raise ValueError(f"raw_score must be between 0 and {_MAXIMUM_RAW_SCORE}.")
        return value

    @field_validator("normalized_score")
    @classmethod
    def _normalized_score_must_be_in_range(cls, value: float) -> float:
        if value < 0 or value > 100:
            raise ValueError("normalized_score must be between 0 and 100.")
        return value

    @model_validator(mode="after")
    def _publishable_requires_premium_or_strong(self) -> "ConfidenceScoreResult":
        if self.publishable and self.classification not in (
            ConfidenceClassification.PREMIUM,
            ConfidenceClassification.STRONG,
        ):
            raise ValueError("publishable can be True only for PREMIUM or STRONG.")
        return self

    @model_validator(mode="after")
    def _mandatory_layers_passed_consistency(self) -> "ConfidenceScoreResult":
        if self.failed_mandatory_layers and self.mandatory_layers_passed:
            raise ValueError("mandatory_layers_passed must be False when any mandatory layer failed.")
        return self
