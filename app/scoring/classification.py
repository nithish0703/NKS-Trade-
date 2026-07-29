"""
Classifies signals based on their composite score.
"""

import math

from app.config.thresholds import (
    MEDIUM_SIGNAL_MINIMUM_SCORE,
    PREMIUM_SIGNAL_MINIMUM_SCORE,
    STRONG_SIGNAL_MINIMUM_SCORE,
)

from app.scoring.results import ConfidenceClassification


class ConfidenceClassificationError(Exception):
    """Raised when a normalized confidence score cannot be classified."""


def classify_confidence(normalized_score: float) -> ConfidenceClassification:
    """
    Classify a normalized (0-100) confidence score.

    90-100: PREMIUM
    80 to below 90: STRONG
    70 to below 80: MEDIUM
    below 70: IGNORE

    Raises:
        ConfidenceClassificationError: If the score is not finite or is
            outside the 0-100 range.
    """
    if normalized_score is None or not math.isfinite(normalized_score):
        raise ConfidenceClassificationError("normalized_score must be a finite number.")
    if normalized_score < 0 or normalized_score > 100:
        raise ConfidenceClassificationError("normalized_score must be between 0 and 100.")

    if normalized_score >= PREMIUM_SIGNAL_MINIMUM_SCORE:
        return ConfidenceClassification.PREMIUM
    if normalized_score >= STRONG_SIGNAL_MINIMUM_SCORE:
        return ConfidenceClassification.STRONG
    if normalized_score >= MEDIUM_SIGNAL_MINIMUM_SCORE:
        return ConfidenceClassification.MEDIUM
    return ConfidenceClassification.IGNORE


def is_publishable(classification: ConfidenceClassification) -> bool:
    """Return True only for PREMIUM or STRONG classifications."""
    return classification in (ConfidenceClassification.PREMIUM, ConfidenceClassification.STRONG)
