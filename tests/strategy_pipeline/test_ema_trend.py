"""
Unit tests for app.strategy_pipeline.ema_trend (EMA trend filter).
"""

from unittest.mock import MagicMock

from app.strategy_pipeline.ema_trend import evaluate_ema_trend


def _snapshot(slope_direction):
    return MagicMock(ema200_slope_direction=slope_direction)


class TestEvaluateEmaTrend:
    def test_bullish_slope_permits_buy(self):
        result = evaluate_ema_trend(_snapshot("BULLISH"), "BUY")
        assert result.passed is True

    def test_bearish_slope_permits_sell(self):
        result = evaluate_ema_trend(_snapshot("BEARISH"), "SELL")
        assert result.passed is True

    def test_bearish_slope_rejects_buy(self):
        result = evaluate_ema_trend(_snapshot("BEARISH"), "BUY")
        assert result.passed is False

    def test_bullish_slope_rejects_sell(self):
        result = evaluate_ema_trend(_snapshot("BULLISH"), "SELL")
        assert result.passed is False

    def test_flat_slope_rejects_both_directions(self):
        assert evaluate_ema_trend(_snapshot("FLAT"), "BUY").passed is False
        assert evaluate_ema_trend(_snapshot("FLAT"), "SELL").passed is False

    def test_missing_snapshot_never_passes(self):
        result = evaluate_ema_trend(None, "BUY")
        assert result.passed is False

    def test_missing_slope_direction_never_passes(self):
        result = evaluate_ema_trend(_snapshot(None), "BUY")
        assert result.passed is False

    def test_reason_is_human_readable_and_non_empty(self):
        result = evaluate_ema_trend(_snapshot("BULLISH"), "BUY")
        assert result.reason
        assert isinstance(result.reason, str)
