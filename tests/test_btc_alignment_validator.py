"""
Unit tests for app.validators.btc_alignment.BTCAlignmentValidator.
"""

from app.market_structure.results import HigherTimeframeBias, HigherTimeframeBiasResult, MarketStructureResult, TrendDirection
from app.validators.btc_alignment import BTCAlignmentValidator


def _structure(trend: TrendDirection) -> MarketStructureResult:
    return MarketStructureResult(
        symbol="BTC-USDT",
        timeframe="4h",
        swings=[],
        classified_swings=[],
        trend_direction=trend,
        higher_high_count=0,
        higher_low_count=0,
        lower_high_count=0,
        lower_low_count=0,
        equal_high_count=0,
        equal_low_count=0,
    )


def _bias(final_bias: HigherTimeframeBias) -> HigherTimeframeBiasResult:
    return HigherTimeframeBiasResult(
        primary_timeframe="4h",
        secondary_timeframe="1h",
        primary_trend=TrendDirection.UNKNOWN,
        secondary_trend=TrendDirection.UNKNOWN,
        final_bias=final_bias,
        aligned=final_bias in (HigherTimeframeBias.BULLISH, HigherTimeframeBias.BEARISH),
        reason="test bias",
    )


class TestBTCAlignmentValidator:
    def test_buy_with_bullish_trend_and_bias_passes(self):
        result = BTCAlignmentValidator().validate(
            "ETH-USDT", "BUY", _structure(TrendDirection.BULLISH), _bias(HigherTimeframeBias.BULLISH)
        )
        assert result.passed is True

    def test_sell_with_bearish_trend_and_bias_passes(self):
        result = BTCAlignmentValidator().validate(
            "ETH-USDT", "SELL", _structure(TrendDirection.BEARISH), _bias(HigherTimeframeBias.BEARISH)
        )
        assert result.passed is True

    def test_buy_conflicts_with_bearish_btc(self):
        result = BTCAlignmentValidator().validate(
            "ETH-USDT", "BUY", _structure(TrendDirection.BEARISH), _bias(HigherTimeframeBias.BEARISH)
        )
        assert result.passed is False
        assert result.rejection_code == "BTC_DIRECTION_CONFLICT"

    def test_sell_conflicts_with_bullish_btc(self):
        result = BTCAlignmentValidator().validate(
            "ETH-USDT", "SELL", _structure(TrendDirection.BULLISH), _bias(HigherTimeframeBias.BULLISH)
        )
        assert result.passed is False
        assert result.rejection_code == "BTC_DIRECTION_CONFLICT"

    def test_mixed_btc_structure_fails(self):
        # MIXED bias with a directional trend still can't be considered aligned.
        result = BTCAlignmentValidator().validate(
            "ETH-USDT", "BUY", _structure(TrendDirection.BULLISH), _bias(HigherTimeframeBias.MIXED)
        )
        assert result.passed is False

    def test_range_btc_structure_fails(self):
        result = BTCAlignmentValidator().validate(
            "ETH-USDT", "BUY", _structure(TrendDirection.RANGE), _bias(HigherTimeframeBias.BULLISH)
        )
        assert result.passed is False
        assert result.rejection_code == "BTC_DIRECTION_UNKNOWN"

    def test_unknown_btc_bias_fails(self):
        result = BTCAlignmentValidator().validate(
            "ETH-USDT", "BUY", _structure(TrendDirection.BULLISH), _bias(HigherTimeframeBias.UNKNOWN)
        )
        assert result.passed is False
        assert result.rejection_code == "BTC_DIRECTION_UNKNOWN"

    def test_structure_and_bias_disagreement_fails(self):
        result = BTCAlignmentValidator().validate(
            "ETH-USDT", "BUY", _structure(TrendDirection.BULLISH), _bias(HigherTimeframeBias.BEARISH)
        )
        assert result.passed is False
        assert result.rejection_code == "BTC_HTF_BIAS_CONFLICT"

    def test_btc_usdt_itself_still_validated(self):
        result = BTCAlignmentValidator().validate(
            "BTC-USDT", "BUY", _structure(TrendDirection.BULLISH), _bias(HigherTimeframeBias.BULLISH)
        )
        assert result.passed is True
        result_fail = BTCAlignmentValidator().validate(
            "BTC-USDT", "BUY", _structure(TrendDirection.BEARISH), _bias(HigherTimeframeBias.BEARISH)
        )
        assert result_fail.passed is False

    def test_score_remains_zero(self):
        passing = BTCAlignmentValidator().validate(
            "ETH-USDT", "BUY", _structure(TrendDirection.BULLISH), _bias(HigherTimeframeBias.BULLISH)
        )
        assert passing.score == 0.0
        failing = BTCAlignmentValidator().validate(
            "ETH-USDT", "BUY", _structure(TrendDirection.BEARISH), _bias(HigherTimeframeBias.BEARISH)
        )
        assert failing.score == 0.0
