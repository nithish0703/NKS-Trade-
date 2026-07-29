"""
Data model representing the result of a validator check.
"""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_validator


class ValidationResult(BaseModel):
    """
    Generic result produced by a single validation layer.

    Used by every validator in `app.validators` to report whether a
    given check passed, with supporting context.
    """

    model_config = ConfigDict(frozen=True)

    passed: bool
    layer_name: str
    reason: Optional[str] = None
    score: float = 0.0
    data: Optional[dict[str, Any]] = None
    rejection_code: Optional[str] = None

    @field_validator("score")
    @classmethod
    def _score_cannot_be_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("score cannot be negative.")
        return value

    @classmethod
    def success(
        cls,
        layer_name: str,
        reason: Optional[str] = None,
        score: float = 0.0,
        data: Optional[dict[str, Any]] = None,
    ) -> "ValidationResult":
        """Build a passing ValidationResult."""
        return cls(
            passed=True,
            layer_name=layer_name,
            reason=reason,
            score=score,
            data=data,
            rejection_code=None,
        )

    @classmethod
    def failure(
        cls,
        layer_name: str,
        reason: Optional[str] = None,
        rejection_code: Optional[str] = None,
        score: float = 0.0,
        data: Optional[dict[str, Any]] = None,
    ) -> "ValidationResult":
        """Build a failing ValidationResult."""
        return cls(
            passed=False,
            layer_name=layer_name,
            reason=reason,
            score=score,
            data=data,
            rejection_code=rejection_code,
        )
