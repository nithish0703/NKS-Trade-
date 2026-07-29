"""
Unit tests for app.validators.atr.ATRValidator.
"""

from app.indicators.results import IndicatorSnapshot
from app.validators.atr import ATRValidator


def _snapshot(atr=10.0, atr_expansion_ratio=1.5) -> IndicatorSnapshot:
    return IndicatorSnapshot(atr=atr, atr_expansion_ratio=atr_expansion_ratio)


class TestATRValidator:
    def test_positive_atr_and_valid_expansion_passes(self):
        result = ATRValidator().validate(_snapshot())
        assert result.passed is True

    def test_missing_atr_fails(self):
        result = ATRValidator().validate(_snapshot(atr=None))
        assert result.passed is False
        assert result.rejection_code == "ATR_DATA_MISSING"

    def test_zero_atr_fails(self):
        result = ATRValidator().validate(_snapshot(atr=0.0))
        assert result.passed is False
        assert result.rejection_code == "ATR_INVALID"

    def test_negative_atr_fails(self):
        result = ATRValidator().validate(_snapshot(atr=-1.0))
        assert result.passed is False
        assert result.rejection_code == "ATR_INVALID"

    def test_missing_expansion_fails(self):
        result = ATRValidator().validate(_snapshot(atr_expansion_ratio=None))
        assert result.passed is False
        assert result.rejection_code == "ATR_EXPANSION_INSUFFICIENT"

    def test_insufficient_expansion_fails(self):
        result = ATRValidator(minimum_atr_expansion_ratio=1.0).validate(
            _snapshot(atr_expansion_ratio=0.5)
        )
        assert result.passed is False
        assert result.rejection_code == "ATR_EXPANSION_INSUFFICIENT"

    def test_score_remains_zero(self):
        passing = ATRValidator().validate(_snapshot())
        assert passing.score == 0.0
        failing = ATRValidator().validate(_snapshot(atr=None))
        assert failing.score == 0.0
