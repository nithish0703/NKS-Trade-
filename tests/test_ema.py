"""
Unit tests for app.indicators.ema.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.indicators.ema import (
    IndicatorCalculationError,
    calculate_candle_close_ema,
    calculate_ema,
    calculate_ema_slope,
    classify_ema_slope,
    get_latest_ema,
)
from app.models.candle import Candle

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_candle(index: int, close: float) -> Candle:
    base = close + 100
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=base,
        high=base + 1,
        low=base - 1,
        close=base,
        volume=100.0,
        symbol="BTC-USDT",
        timeframe="15m",
    )


class TestCalculateEma:
    def test_valid_ema_calculation(self):
        values = [1, 2, 3, 4]
        result = calculate_ema(values, 3)
        assert result[0] is None
        assert result[1] is None
        assert result[2] == pytest.approx(2.0)
        assert result[3] == pytest.approx(3.0)

    def test_correct_sma_seed(self):
        values = [10, 20, 30, 40, 50]
        result = calculate_ema(values, 3)
        assert result[2] == pytest.approx((10 + 20 + 30) / 3)

    def test_same_length_output(self):
        values = [1, 2, 3, 4, 5, 6]
        result = calculate_ema(values, 3)
        assert len(result) == len(values)

    def test_invalid_period(self):
        with pytest.raises(IndicatorCalculationError):
            calculate_ema([1, 2, 3], 0)

    def test_empty_values(self):
        with pytest.raises(IndicatorCalculationError):
            calculate_ema([], 3)

    def test_non_finite_value_rejected(self):
        with pytest.raises(IndicatorCalculationError):
            calculate_ema([1, float("nan"), 3], 2)

    def test_input_values_not_mutated(self):
        values = [1, 2, 3, 4]
        original = list(values)
        calculate_ema(values, 3)
        assert values == original


class TestCandleCloseEma:
    def test_delegates_to_calculate_ema(self):
        closes = [1, 2, 3, 4]
        candles = [_make_candle(i, close) for i, close in enumerate(closes)]
        result = calculate_candle_close_ema(candles, 3)
        assert result == calculate_ema([c.close for c in candles], 3)

    def test_candles_not_mutated(self):
        candles = [_make_candle(i, close) for i, close in enumerate([1, 2, 3, 4])]
        snapshot = [c.model_copy() for c in candles]
        calculate_candle_close_ema(candles, 3)
        assert candles == snapshot


class TestGetLatestEma:
    def test_insufficient_candles_for_latest_ema(self):
        candles = [_make_candle(i, close) for i, close in enumerate([1, 2])]
        with pytest.raises(IndicatorCalculationError):
            get_latest_ema(candles, 3)

    def test_valid_latest_ema(self):
        closes = [1, 2, 3, 4]
        candles = [_make_candle(i, close) for i, close in enumerate(closes)]
        result = get_latest_ema(candles, 3)
        assert result == pytest.approx(calculate_ema([c.close for c in candles], 3)[-1])


class TestCalculateEmaSlope:
    def test_valid_positive_slope(self):
        ema_values = [None, None, 10.0, 20.0, 30.0]
        slope = calculate_ema_slope(ema_values, 2)
        assert slope > 0

    def test_valid_negative_slope(self):
        ema_values = [None, None, 30.0, 20.0, 10.0]
        slope = calculate_ema_slope(ema_values, 2)
        assert slope < 0

    def test_insufficient_slope_lookback(self):
        ema_values = [None, 10.0]
        with pytest.raises(IndicatorCalculationError):
            calculate_ema_slope(ema_values, 5)

    def test_invalid_lookback(self):
        ema_values = [1.0, 2.0, 3.0]
        with pytest.raises(IndicatorCalculationError):
            calculate_ema_slope(ema_values, 0)

    def test_zero_denominator_rejected(self):
        ema_values = [0.0, 1.0, 2.0]
        with pytest.raises(IndicatorCalculationError):
            calculate_ema_slope(ema_values, 2)

    def test_none_reference_value_rejected(self):
        ema_values = [None, None, 10.0]
        with pytest.raises(IndicatorCalculationError):
            calculate_ema_slope(ema_values, 2)


class TestClassifyEmaSlope:
    def test_bullish_classification(self):
        assert classify_ema_slope(0.01, 0.0005) == "BULLISH"

    def test_bearish_classification(self):
        assert classify_ema_slope(-0.01, 0.0005) == "BEARISH"

    def test_flat_classification(self):
        assert classify_ema_slope(0.0001, 0.0005) == "FLAT"
