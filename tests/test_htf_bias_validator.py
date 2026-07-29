"""
Unit tests for app.validators.htf_bias.HigherTimeframeBiasValidator.
"""

from app.market_structure.results import HigherTimeframeBias, HigherTimeframeBiasResult, TrendDirection
from app.validators.htf_bias import HigherTimeframeBiasValidator


def _bias(final_bias: HigherTimeframeBias, aligned=True) -> HigherTimeframeBiasResult:
    return HigherTimeframeBiasResult(
        primary_timeframe="4h",
        secondary_timeframe="1h",
        primary_trend=TrendDirection.UNKNOWN,
        secondary_trend=TrendDirection.UNKNOWN,
        final_bias=final_bias,
        aligned=aligned,
        reason="test",
    )


class TestHigherTimeframeBiasValidator:
    def test_buy_with_bullish_aligned_bias_passes(self):
        result = HigherTimeframeBiasValidator().validate(_bias(HigherTimeframeBias.BULLISH), "BUY")
        assert result.passed is True
        assert result.score == 0.0

    def test_sell_with_bearish_aligned_bias_passes(self):
        result = HigherTimeframeBiasValidator().validate(_bias(HigherTimeframeBias.BEARISH), "SELL")
        assert result.passed is True

    def test_mixed_bias_fails(self):
        result = HigherTimeframeBiasValidator().validate(_bias(HigherTimeframeBias.MIXED), "BUY")
        assert result.passed is False
        assert result.rejection_code == "HTF_BIAS_MIXED"

    def test_unknown_bias_fails(self):
        result = HigherTimeframeBiasValidator().validate(_bias(HigherTimeframeBias.UNKNOWN), "BUY")
        assert result.passed is False
        assert result.rejection_code == "HTF_BIAS_UNKNOWN"

    def test_buy_with_bearish_bias_fails(self):
        result = HigherTimeframeBiasValidator().validate(_bias(HigherTimeframeBias.BEARISH), "BUY")
        assert result.passed is False
        assert result.rejection_code == "HTF_BIAS_DIRECTION_MISMATCH"

    def test_sell_with_bullish_bias_fails(self):
        result = HigherTimeframeBiasValidator().validate(_bias(HigherTimeframeBias.BULLISH), "SELL")
        assert result.passed is False
        assert result.rejection_code == "HTF_BIAS_DIRECTION_MISMATCH"

    def test_aligned_false_fails(self):
        result = HigherTimeframeBiasValidator().validate(
            _bias(HigherTimeframeBias.BULLISH, aligned=False), "BUY"
        )
        assert result.passed is False
        assert result.rejection_code == "HTF_BIAS_DIRECTION_MISMATCH"

    def test_score_remains_zero(self):
        passing = HigherTimeframeBiasValidator().validate(_bias(HigherTimeframeBias.BULLISH), "BUY")
        assert passing.score == 0.0
        failing = HigherTimeframeBiasValidator().validate(_bias(HigherTimeframeBias.MIXED), "BUY")
        assert failing.score == 0.0
