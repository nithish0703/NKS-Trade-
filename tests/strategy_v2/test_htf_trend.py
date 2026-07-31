"""
Unit tests for app.strategy_v2.htf_trend (Check 1: Higher Timeframe Trend, 1H).
"""

from app.market_structure.results import MarketStructureResult, TrendDirection
from app.strategy_v2.htf_trend import HtfTrendDirection, evaluate_htf_trend


def _structure(trend: TrendDirection, timeframe: str = "1h") -> MarketStructureResult:
    return MarketStructureResult(
        symbol="BTC-USDT",
        timeframe=timeframe,
        swings=[],
        classified_swings=[],
        latest_swing_high=None,
        latest_swing_low=None,
        trend_direction=trend,
        higher_high_count=0,
        higher_low_count=0,
        lower_high_count=0,
        lower_low_count=0,
        equal_high_count=0,
        equal_low_count=0,
        confidence_notes=None,
    )


class TestEvaluateHtfTrend:
    def test_bullish_1h_permits_buy(self):
        result = evaluate_htf_trend(_structure(TrendDirection.BULLISH))
        assert result.passed is True
        assert result.permitted_direction == HtfTrendDirection.BUY

    def test_bearish_1h_permits_sell(self):
        result = evaluate_htf_trend(_structure(TrendDirection.BEARISH))
        assert result.passed is True
        assert result.permitted_direction == HtfTrendDirection.SELL

    def test_range_1h_permits_nothing(self):
        result = evaluate_htf_trend(_structure(TrendDirection.RANGE))
        assert result.passed is False
        assert result.permitted_direction == HtfTrendDirection.NONE

    def test_unknown_1h_permits_nothing(self):
        result = evaluate_htf_trend(_structure(TrendDirection.UNKNOWN))
        assert result.passed is False
        assert result.permitted_direction == HtfTrendDirection.NONE

    def test_missing_structure_never_fabricates_a_direction(self):
        result = evaluate_htf_trend(None)
        assert result.passed is False
        assert result.permitted_direction == HtfTrendDirection.NONE
        assert result.trend_direction == TrendDirection.UNKNOWN

    def test_reports_the_1h_timeframe(self):
        result = evaluate_htf_trend(_structure(TrendDirection.BULLISH))
        assert result.timeframe == "1h"

    def test_reason_is_human_readable_and_non_empty(self):
        for trend in TrendDirection:
            result = evaluate_htf_trend(_structure(trend))
            assert result.reason
            assert isinstance(result.reason, str)
