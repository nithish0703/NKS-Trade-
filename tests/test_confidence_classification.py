"""
Unit tests for app.scoring.classification.
"""

import math

import pytest

from app.scoring.classification import (
    ConfidenceClassificationError,
    classify_confidence,
    is_publishable,
)
from app.scoring.results import ConfidenceClassification


class TestClassifyConfidence:
    def test_100_is_premium(self):
        assert classify_confidence(100.0) == ConfidenceClassification.PREMIUM

    def test_90_is_premium(self):
        assert classify_confidence(90.0) == ConfidenceClassification.PREMIUM

    def test_89_99_is_strong(self):
        assert classify_confidence(89.99) == ConfidenceClassification.STRONG

    def test_80_is_strong(self):
        assert classify_confidence(80.0) == ConfidenceClassification.STRONG

    def test_79_99_is_medium(self):
        assert classify_confidence(79.99) == ConfidenceClassification.MEDIUM

    def test_70_is_medium(self):
        assert classify_confidence(70.0) == ConfidenceClassification.MEDIUM

    def test_69_99_is_ignore(self):
        assert classify_confidence(69.99) == ConfidenceClassification.IGNORE

    def test_0_is_ignore(self):
        assert classify_confidence(0.0) == ConfidenceClassification.IGNORE

    def test_negative_score_rejected(self):
        with pytest.raises(ConfidenceClassificationError):
            classify_confidence(-1.0)

    def test_above_100_rejected(self):
        with pytest.raises(ConfidenceClassificationError):
            classify_confidence(100.1)

    def test_nan_rejected(self):
        with pytest.raises(ConfidenceClassificationError):
            classify_confidence(math.nan)


class TestIsPublishable:
    def test_premium_publishable(self):
        assert is_publishable(ConfidenceClassification.PREMIUM) is True

    def test_strong_publishable(self):
        assert is_publishable(ConfidenceClassification.STRONG) is True

    def test_medium_not_publishable(self):
        assert is_publishable(ConfidenceClassification.MEDIUM) is False

    def test_ignore_not_publishable(self):
        assert is_publishable(ConfidenceClassification.IGNORE) is False
