"""
Classifies signals based on their composite score.
"""

import math

from app.config.settings import get_settings
from app.config.thresholds import (
    MEDIUM_SIGNAL_MINIMUM_SCORE,
    PREMIUM_SIGNAL_MINIMUM_SCORE,
)

from app.scoring.results import ConfidenceClassification


class ConfidenceClassificationError(Exception):
    """Raised when a normalized confidence score cannot be classified."""


def classify_confidence(normalized_score: float) -> ConfidenceClassification:
    """
    Classify a normalized (0-100) confidence score.

    90-100: PREMIUM
    settings.min_publishable_confidence_score (default 80) to below 90: STRONG
    70 to below the STRONG cutoff: MEDIUM
    below 70: IGNORE

    The STRONG/publishable cutoff is read from Settings (env-overridable
    via MIN_PUBLISHABLE_CONFIDENCE_SCORE) rather than a hardcoded
    threshold, so signal frequency can be tuned without a code change.

    Raises:
        ConfidenceClassificationError: If the score is not finite or is
            outside the 0-100 range.
    """
    if normalized_score is None or not math.isfinite(normalized_score):
        raise ConfidenceClassificationError("normalized_score must be a finite number.")
    if normalized_score < 0 or normalized_score > 100:
        raise ConfidenceClassificationError("normalized_score must be between 0 and 100.")

    strong_minimum_score = get_settings().min_publishable_confidence_score

    if normalized_score >= PREMIUM_SIGNAL_MINIMUM_SCORE:
        return ConfidenceClassification.PREMIUM
    if normalized_score >= strong_minimum_score:
        return ConfidenceClassification.STRONG
    if normalized_score >= MEDIUM_SIGNAL_MINIMUM_SCORE:
        return ConfidenceClassification.MEDIUM
    return ConfidenceClassification.IGNORE


def is_publishable(classification: ConfidenceClassification) -> bool:
    """Return True only for PREMIUM or STRONG classifications."""
    return classification in (ConfidenceClassification.PREMIUM, ConfidenceClassification.STRONG)
