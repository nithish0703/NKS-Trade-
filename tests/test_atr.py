"""
Unit tests for app.indicators.atr.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.indicators.atr import (
    calculate_atr,
    calculate_atr_expansion,
    calculate_atr_ratio,
    calculate_true_ranges,
    get_latest_atr,
)
from app.indicators.ema import IndicatorCalculationError
from app.models.candle import Candle

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_candle(index: int, high: float, low: float, close: float) -> Candle:
    open_price = close
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        symbol="BTC-USDT",
        timeframe="15m",
    )


class TestCalculateTrueRanges:
    def test_first_candle_true_range(self):
        candles = [_make_candle(0, high=110, low=100, close=105)]
        result = calculate_true_ranges(candles)
        assert result[0] == pytest.approx(10)

    def test_gap_up_true_range(self):
        candles = [
            _make_candle(0, high=100, low=95, close=98),
            _make_candle(1, high=120, low=115, close=118),
        ]
        result = calculate_true_ranges(candles)
        # abs(high - previous_close) = abs(120 - 98) = 22 dominates
        assert result[1] == pytest.approx(22)

    def test_gap_down_true_range(self):
        candles = [
            _make_candle(0, high=100, low=95, close=98),
            _make_candle(1, high=80, low=75, close=78),
        ]
        result = calculate_true_ranges(candles)
        # abs(low - previous_close) = abs(75 - 98) = 23 dominates
        assert result[1] == pytest.approx(23)

    def test_candles_not_mutated(self):
        candles = [_make_candle(0, 100, 95, 98), _make_candle(1, 105, 100, 103)]
        snapshot = [c.model_copy() for c in candles]
        calculate_true_ranges(candles)
        assert candles == snapshot


class TestCalculateAtr:
    def test_atr_seed_calculation(self):
        candles = [
            _make_candle(0, 100, 90, 95),
            _make_candle(1, 105, 95, 100),
            _make_candle(2, 110, 100, 105),
        ]
        true_ranges = calculate_true_ranges(candles)
        result = calculate_atr(candles, 3)
        assert result[2] == pytest.approx(sum(true_ranges[:3]) / 3)

    def test_wilder_atr_update(self):
        candles = [
            _make_candle(0, 100, 90, 95),
            _make_candle(1, 105, 95, 100),
            _make_candle(2, 110, 100, 105),
            _make_candle(3, 115, 105, 110),
        ]
        true_ranges = calculate_true_ranges(candles)
        result = calculate_atr(candles, 3)
        seed = sum(true_ranges[:3]) / 3
        expected = (seed * 2 + true_ranges[3]) / 3
        assert result[3] == pytest.approx(expected)

    def test_same_length_output(self):
        candles = [_make_candle(i, 100 + i, 90 + i, 95 + i) for i in range(5)]
        result = calculate_atr(candles, 3)
        assert len(result) == len(candles)

    def test_invalid_period(self):
        candles = [_make_candle(0, 100, 90, 95)]
        with pytest.raises(IndicatorCalculationError):
            calculate_atr(candles, 0)

    def test_insufficient_candles(self):
        candles = [_make_candle(0, 100, 90, 95)]
        with pytest.raises(IndicatorCalculationError):
            calculate_atr(candles, 3)

    def test_candles_not_mutated(self):
        candles = [_make_candle(i, 100 + i, 90 + i, 95 + i) for i in range(4)]
        snapshot = [c.model_copy() for c in candles]
        calculate_atr(candles, 3)
        assert candles == snapshot


class TestGetLatestAtr:
    def test_insufficient_candles_raises(self):
        candles = [_make_candle(0, 100, 90, 95)]
        with pytest.raises(IndicatorCalculationError):
            get_latest_atr(candles, 3)

    def test_valid_latest_atr(self):
        candles = [_make_candle(i, 100 + i, 90 + i, 95 + i) for i in range(3)]
        result = get_latest_atr(candles, 3)
        assert result is not None


class TestCalculateAtrRatio:
    def test_valid_ratio(self):
        assert calculate_atr_ratio(20.0, 10.0) == pytest.approx(2.0)

    def test_invalid_reference_value(self):
        with pytest.raises(IndicatorCalculationError):
            calculate_atr_ratio(20.0, 0.0)
        with pytest.raises(IndicatorCalculationError):
            calculate_atr_ratio(0.0, 10.0)


class TestCalculateAtrExpansion:
    def test_atr_expansion_calculation(self):
        atr_values = [None, None, 10.0, 10.0, 10.0, 20.0]
        ratio = calculate_atr_expansion(atr_values, 3)
        assert ratio == pytest.approx(2.0)

    def test_invalid_reference_lookback(self):
        atr_values = [None, None, 10.0, 20.0]
        with pytest.raises(IndicatorCalculationError):
            calculate_atr_expansion(atr_values, 0)

    def test_insufficient_reference_values(self):
        atr_values = [None, None, 10.0, 20.0]
        with pytest.raises(IndicatorCalculationError):
            calculate_atr_expansion(atr_values, 5)

    def test_latest_atr_unavailable(self):
        atr_values = [None, None, 10.0, None]
        with pytest.raises(IndicatorCalculationError):
            calculate_atr_expansion(atr_values, 2)
