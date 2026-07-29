"""
Unit tests for app.validators.candle_quality.CandleQualityValidator.
"""

from datetime import datetime, timezone

from app.models.candle import Candle
from app.validators.candle_quality import CandleQualityValidator

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        timestamp=UTC_NOW,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        symbol="BTC-USDT",
        timeframe="15m",
    )


def _validator(
    min_body=0.60,
    doji_max=0.10,
    spinning_top_max=0.30,
    max_opposite_wick=0.35,
    bull_min=0.75,
    bear_max=0.25,
) -> CandleQualityValidator:
    return CandleQualityValidator(
        minimum_body_ratio=min_body,
        doji_maximum_body_ratio=doji_max,
        spinning_top_maximum_body_ratio=spinning_top_max,
        maximum_opposite_wick_ratio=max_opposite_wick,
        bullish_close_location_minimum=bull_min,
        bearish_close_location_maximum=bear_max,
    )


class TestCandleQualityValidator:
    def test_strong_bullish_candle_passes_buy(self):
        # range 10, body 8, close at high, minimal upper wick
        candle = _candle(open_=100, high=110, low=100, close=108)
        result = _validator().validate(candle, "BUY")
        assert result.passed is True

    def test_strong_bearish_candle_passes_sell(self):
        candle = _candle(open_=108, high=110, low=100, close=102)
        result = _validator().validate(candle, "SELL")
        assert result.passed is True

    def test_buy_bearish_candle_fails(self):
        candle = _candle(open_=108, high=110, low=100, close=102)
        result = _validator().validate(candle, "BUY")
        assert result.passed is False
        assert result.rejection_code == "CANDLE_DIRECTION_MISMATCH"

    def test_sell_bullish_candle_fails(self):
        candle = _candle(open_=100, high=110, low=100, close=108)
        result = _validator().validate(candle, "SELL")
        assert result.passed is False
        assert result.rejection_code == "CANDLE_DIRECTION_MISMATCH"

    def test_doji_fails(self):
        candle = _candle(open_=100, high=110, low=90, close=100.5)  # body ratio ~0.025
        result = _validator().validate(candle, "BUY")
        assert result.passed is False
        assert result.rejection_code == "DOJI_CANDLE"

    def test_tiny_body_fails(self):
        # body ratio between doji and spinning-top thresholds but below min
        candle = _candle(open_=100, high=110, low=90, close=104)  # body 4/20=0.20
        result = _validator(doji_max=0.10, spinning_top_max=0.15).validate(candle, "BUY")
        assert result.passed is False
        assert result.rejection_code == "TINY_CANDLE_BODY"

    def test_spinning_top_fails(self):
        candle = _candle(open_=100, high=110, low=90, close=104)  # body ratio 0.20
        result = _validator(doji_max=0.10, spinning_top_max=0.30).validate(candle, "BUY")
        assert result.passed is False
        assert result.rejection_code == "SPINNING_TOP"

    def test_buy_long_upper_wick_fails(self):
        # Strong body ratio, close near top of body, but overall large upper wick.
        candle = _candle(open_=100, high=120, low=99, close=110)
        result = _validator(min_body=0.10, max_opposite_wick=0.10).validate(candle, "BUY")
        assert result.passed is False
        assert result.rejection_code == "EXCESSIVE_OPPOSITE_WICK"

    def test_sell_long_lower_wick_fails(self):
        candle = _candle(open_=110, high=111, low=80, close=100)
        result = _validator(min_body=0.10, max_opposite_wick=0.10).validate(candle, "SELL")
        assert result.passed is False
        assert result.rejection_code == "EXCESSIVE_OPPOSITE_WICK"

    def test_buy_weak_close_fails(self):
        candle = _candle(open_=100, high=120, low=99, close=110)
        result = _validator(min_body=0.10, max_opposite_wick=0.90, bull_min=0.95).validate(candle, "BUY")
        assert result.passed is False
        assert result.rejection_code == "WEAK_CANDLE_CLOSE"

    def test_sell_weak_close_fails(self):
        candle = _candle(open_=110, high=111, low=80, close=100)
        result = _validator(min_body=0.10, max_opposite_wick=0.90, bear_max=0.05).validate(candle, "SELL")
        assert result.passed is False
        assert result.rejection_code == "WEAK_CANDLE_CLOSE"

    def test_exact_body_threshold_passes(self):
        # range 10, body 6 -> ratio 0.60 exactly, close at high for CLV=1.0
        candle = _candle(open_=104, high=110, low=100, close=110)
        result = _validator(min_body=0.60, bull_min=0.75).validate(candle, "BUY")
        assert result.passed is True

    def test_zero_range_candle_fails_safely(self):
        candle = _candle(open_=100, high=100, low=100, close=100)
        result = _validator().validate(candle, "BUY")
        assert result.passed is False
        assert result.rejection_code == "CANDLE_QUALITY_DATA_INVALID"

    def test_score_remains_zero(self):
        strong = _candle(open_=100, high=110, low=100, close=108)
        passing = _validator().validate(strong, "BUY")
        assert passing.score == 0.0
        failing = _validator().validate(strong, "SELL")
        assert failing.score == 0.0
