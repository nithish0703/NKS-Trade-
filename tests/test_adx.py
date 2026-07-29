"""
Unit tests for app.indicators.adx.

Deterministic synthetic candles are used throughout.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.indicators.adx import calculate_adx, get_latest_adx
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


def _make_uptrend_candles(count: int) -> list[Candle]:
    candles = []
    for i in range(count):
        base = 100 + i * 2
        candles.append(_make_candle(i, high=base + 2, low=base - 1, close=base + 1))
    return candles


def _make_flat_candles(count: int) -> list[Candle]:
    return [_make_candle(i, high=101, low=99, close=100) for i in range(count)]


PERIOD = 5
MIN_REQUIRED = (PERIOD * 2) + 1


class TestCalculateAdx:
    def test_result_lengths_match_candle_count(self):
        candles = _make_uptrend_candles(MIN_REQUIRED + 5)
        result = calculate_adx(candles, PERIOD)
        assert len(result.plus_di) == len(candles)
        assert len(result.minus_di) == len(candles)
        assert len(result.dx) == len(candles)
        assert len(result.adx) == len(candles)

    def test_positive_directional_movement_dominates_in_uptrend(self):
        candles = _make_uptrend_candles(MIN_REQUIRED + 5)
        result = calculate_adx(candles, PERIOD)
        last_plus_di = result.plus_di[-1]
        last_minus_di = result.minus_di[-1]
        assert last_plus_di is not None and last_minus_di is not None
        assert last_plus_di > last_minus_di

    def test_negative_directional_movement_dominates_in_downtrend(self):
        uptrend = _make_uptrend_candles(MIN_REQUIRED + 5)
        # Mirror into a downtrend using the same shape.
        candles = []
        for i, c in enumerate(reversed(uptrend)):
            candles.append(
                _make_candle(i, high=c.high, low=c.low, close=c.close)
            )
        result = calculate_adx(candles, PERIOD)
        last_plus_di = result.plus_di[-1]
        last_minus_di = result.minus_di[-1]
        assert last_plus_di is not None and last_minus_di is not None
        assert last_minus_di > last_plus_di

    def test_di_and_dx_ranges(self):
        candles = _make_uptrend_candles(MIN_REQUIRED + 10)
        result = calculate_adx(candles, PERIOD)
        for value in result.plus_di:
            if value is not None:
                assert 0 <= value <= 100
        for value in result.minus_di:
            if value is not None:
                assert 0 <= value <= 100
        for value in result.dx:
            if value is not None:
                assert 0 <= value <= 100

    def test_adx_range(self):
        candles = _make_uptrend_candles(MIN_REQUIRED + 10)
        result = calculate_adx(candles, PERIOD)
        for value in result.adx:
            if value is not None:
                assert 0 <= value <= 100

    def test_flat_price_sequence_handling(self):
        candles = _make_flat_candles(MIN_REQUIRED + 5)
        result = calculate_adx(candles, PERIOD)
        # No directional movement at all; DX/ADX should resolve to 0, not error.
        assert result.dx[-1] == pytest.approx(0.0)

    def test_invalid_period(self):
        candles = _make_uptrend_candles(MIN_REQUIRED + 5)
        with pytest.raises(IndicatorCalculationError):
            calculate_adx(candles, 0)

    def test_insufficient_candles(self):
        candles = _make_uptrend_candles(3)
        with pytest.raises(IndicatorCalculationError):
            calculate_adx(candles, PERIOD)

    def test_candles_not_mutated(self):
        candles = _make_uptrend_candles(MIN_REQUIRED + 5)
        snapshot = [c.model_copy() for c in candles]
        calculate_adx(candles, PERIOD)
        assert candles == snapshot


class TestGetLatestAdx:
    def test_latest_adx_extraction(self):
        candles = _make_uptrend_candles(MIN_REQUIRED + 10)
        value = get_latest_adx(candles, PERIOD)
        assert value is not None
        assert 0 <= value <= 100

    def test_insufficient_candles_raises(self):
        candles = _make_uptrend_candles(3)
        with pytest.raises(IndicatorCalculationError):
            get_latest_adx(candles, PERIOD)
