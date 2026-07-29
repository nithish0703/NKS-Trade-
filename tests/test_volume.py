"""
Unit tests for app.indicators.volume.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.indicators.ema import IndicatorCalculationError
from app.indicators.volume import (
    calculate_volume_delta,
    calculate_volume_ema,
    calculate_volume_ratio,
    get_latest_volume_ema,
    is_above_average_volume,
)
from app.models.candle import Candle

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_candle(index: int, volume: float) -> Candle:
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=volume,
        symbol="BTC-USDT",
        timeframe="15m",
    )


class TestVolumeEma:
    def test_volume_ema_calculation(self):
        candles = [_make_candle(i, v) for i, v in enumerate([10, 20, 30, 40])]
        result = calculate_volume_ema(candles, 3)
        assert result[2] == pytest.approx((10 + 20 + 30) / 3)

    def test_latest_volume_ema(self):
        candles = [_make_candle(i, v) for i, v in enumerate([10, 20, 30, 40])]
        result = get_latest_volume_ema(candles, 3)
        assert result is not None

    def test_latest_volume_ema_insufficient_candles(self):
        candles = [_make_candle(0, 10)]
        with pytest.raises(IndicatorCalculationError):
            get_latest_volume_ema(candles, 3)


class TestAboveAverageVolume:
    def test_current_volume_above_ema(self):
        assert is_above_average_volume(150.0, 100.0) is True

    def test_current_volume_equal_to_ema_returns_false(self):
        assert is_above_average_volume(100.0, 100.0) is False

    def test_current_volume_below_ema_returns_false(self):
        assert is_above_average_volume(50.0, 100.0) is False


class TestVolumeRatio:
    def test_valid_volume_ratio(self):
        assert calculate_volume_ratio(150.0, 100.0) == pytest.approx(1.5)

    def test_zero_average_volume_rejection(self):
        with pytest.raises(IndicatorCalculationError):
            calculate_volume_ratio(100.0, 0.0)

    def test_negative_current_volume_rejection(self):
        with pytest.raises(IndicatorCalculationError):
            calculate_volume_ratio(-1.0, 100.0)


class TestVolumeDelta:
    def test_valid_volume_delta(self):
        assert calculate_volume_delta(60.0, 40.0) == pytest.approx(20.0)

    def test_unavailable_volume_delta_returns_none(self):
        assert calculate_volume_delta(None, 40.0) is None
        assert calculate_volume_delta(60.0, None) is None
        assert calculate_volume_delta(None, None) is None

    def test_negative_delta_input_rejection(self):
        with pytest.raises(IndicatorCalculationError):
            calculate_volume_delta(-10.0, 40.0)
        with pytest.raises(IndicatorCalculationError):
            calculate_volume_delta(10.0, -40.0)
