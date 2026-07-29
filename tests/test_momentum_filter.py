"""
Unit tests for app.validators.momentum_filter.MomentumFilter.
"""

from app.indicators.results import IndicatorSnapshot
from app.validators.momentum_filter import MomentumFilter
from app.validators.results import MomentumAlignment


def _snapshot(ema20=None, ema50=None) -> IndicatorSnapshot:
    return IndicatorSnapshot(ema20=ema20, ema50=ema50)


class TestMomentumFilter:
    def test_buy_ema20_above_ema50_is_aligned(self):
        snapshot = _snapshot(ema20=110.0, ema50=100.0)
        result = MomentumFilter().evaluate(snapshot, "BUY")
        assert result.alignment == MomentumAlignment.BUY_ALIGNED
        assert result.confidence_boost_eligible is True

    def test_buy_ema20_below_ema50_does_not_reject(self):
        snapshot = _snapshot(ema20=90.0, ema50=100.0)
        validation = MomentumFilter().validate(snapshot, "BUY")
        assert validation.passed is True

    def test_sell_ema20_below_ema50_is_aligned(self):
        snapshot = _snapshot(ema20=90.0, ema50=100.0)
        result = MomentumFilter().evaluate(snapshot, "SELL")
        assert result.alignment == MomentumAlignment.SELL_ALIGNED
        assert result.confidence_boost_eligible is True

    def test_sell_ema20_above_ema50_does_not_reject(self):
        snapshot = _snapshot(ema20=110.0, ema50=100.0)
        validation = MomentumFilter().validate(snapshot, "SELL")
        assert validation.passed is True

    def test_equal_emas_are_neutral(self):
        snapshot = _snapshot(ema20=100.0, ema50=100.0)
        result = MomentumFilter().evaluate(snapshot, "BUY")
        assert result.alignment == MomentumAlignment.NEUTRAL
        assert result.confidence_boost_eligible is False

    def test_missing_ema_values_do_not_reject(self):
        snapshot = _snapshot(ema20=None, ema50=None)
        validation = MomentumFilter().validate(snapshot, "BUY")
        assert validation.passed is True
        result = MomentumFilter().evaluate(snapshot, "BUY")
        assert result.alignment == MomentumAlignment.NEUTRAL
        assert result.confidence_boost_eligible is False

    def test_validator_always_passes(self):
        for ema20, ema50, direction in [
            (110.0, 100.0, "BUY"),
            (90.0, 100.0, "BUY"),
            (100.0, 100.0, "SELL"),
            (None, None, "SELL"),
        ]:
            snapshot = _snapshot(ema20=ema20, ema50=ema50)
            validation = MomentumFilter().validate(snapshot, direction)
            assert validation.passed is True

    def test_confidence_boost_flag_only_when_aligned(self):
        aligned = MomentumFilter().evaluate(_snapshot(110.0, 100.0), "BUY")
        conflict = MomentumFilter().evaluate(_snapshot(90.0, 100.0), "BUY")
        assert aligned.confidence_boost_eligible is True
        assert conflict.confidence_boost_eligible is False

    def test_score_remains_zero(self):
        validation = MomentumFilter().validate(_snapshot(110.0, 100.0), "BUY")
        assert validation.score == 0.0
