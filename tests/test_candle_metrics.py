"""
Unit tests for app.indicators.candle_metrics.
"""

from datetime import datetime, timezone

import pytest

from app.indicators.candle_metrics import (
    calculate_candle_metrics,
    is_displacement_body,
)
from app.models.candle import Candle

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_candle(open_: float, high: float, low: float, close: float) -> Candle:
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


class TestCalculateCandleMetrics:
    def test_bullish_candle_metrics(self):
        candle = _make_candle(open_=100, high=110, low=95, close=108)
        metrics = calculate_candle_metrics(candle)
        assert metrics.bullish is True
        assert metrics.bearish is False

    def test_bearish_candle_metrics(self):
        candle = _make_candle(open_=108, high=110, low=95, close=100)
        metrics = calculate_candle_metrics(candle)
        assert metrics.bullish is False
        assert metrics.bearish is True

    def test_body_ratio(self):
        candle = _make_candle(open_=100, high=110, low=95, close=105)
        metrics = calculate_candle_metrics(candle)
        assert metrics.body_ratio == pytest.approx(5 / 15)

    def test_upper_wick_ratio(self):
        candle = _make_candle(open_=100, high=110, low=95, close=105)
        metrics = calculate_candle_metrics(candle)
        # upper wick = high - max(open, close) = 110 - 105 = 5
        assert metrics.upper_wick_ratio == pytest.approx(5 / 15)

    def test_lower_wick_ratio(self):
        candle = _make_candle(open_=100, high=110, low=95, close=105)
        metrics = calculate_candle_metrics(candle)
        # lower wick = min(open, close) - low = 100 - 95 = 5
        assert metrics.lower_wick_ratio == pytest.approx(5 / 15)

    def test_close_location_value(self):
        candle = _make_candle(open_=100, high=110, low=95, close=105)
        metrics = calculate_candle_metrics(candle)
        expected = (105 - 95) / (110 - 95)
        assert metrics.close_location_value == pytest.approx(expected)

    def test_zero_range_candle_handling(self):
        candle = _make_candle(open_=100, high=100, low=100, close=100)
        metrics = calculate_candle_metrics(candle)
        assert metrics.upper_wick_ratio == 0.0
        assert metrics.lower_wick_ratio == 0.0
        assert metrics.close_location_value == 0.0


class TestIsDisplacementBody:
    def test_displacement_body_at_60_percent(self):
        candle = _make_candle(open_=100, high=110, low=94, close=106)
        # body_ratio = 6 / 16 = 0.375, so bump body to exactly 60%
        candle = _make_candle(open_=100, high=100 + 10, low=100 - 0, close=100 + 6)
        # range = 10, body = 6 -> 60%
        assert candle.body_ratio == pytest.approx(0.6)
        assert is_displacement_body(candle, 0.60) is True

    def test_non_displacement_body_below_60_percent(self):
        candle = _make_candle(open_=100, high=110, low=90, close=105)
        # range = 20, body = 5 -> 25%
        assert candle.body_ratio == pytest.approx(0.25)
        assert is_displacement_body(candle, 0.60) is False
