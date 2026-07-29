"""
Unit tests for app.validators.volatility_filter.VolatilityFilter.
"""

from datetime import datetime, timedelta, timezone

from app.indicators.results import IndicatorSnapshot
from app.models.candle import Candle
from app.validators.results import VolatilityStatus
from app.validators.volatility_filter import VolatilityFilter

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(index: int, high: float, low: float) -> Candle:
    mid = (high + low) / 2
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=mid,
        high=high,
        low=low,
        close=mid,
        volume=100.0,
        symbol="BTC-USDT",
        timeframe="15m",
    )


def _snapshot(atr=10.0, atr_expansion_ratio=1.5) -> IndicatorSnapshot:
    return IndicatorSnapshot(atr=atr, atr_expansion_ratio=atr_expansion_ratio)


def _filter(
    min_atr=0.00000001, min_expansion=1.0, lookback=10, min_range_ratio=0.50
) -> VolatilityFilter:
    return VolatilityFilter(
        minimum_atr_value=min_atr,
        minimum_atr_expansion_ratio=min_expansion,
        compression_lookback=lookback,
        minimum_candle_range_ratio=min_range_ratio,
    )


def _normal_candles(count: int, range_size=10.0) -> list[Candle]:
    return [_candle(i, 100 + range_size, 100) for i in range(count)]


class TestVolatilityFilter:
    def test_expanding_volatility_passes(self):
        candles = _normal_candles(11, range_size=10.0)
        candles[-1] = _candle(10, 120, 100)  # larger current range
        result = _filter().validate(candles, _snapshot(atr=10.0, atr_expansion_ratio=1.5))
        assert result.passed is True

    def test_normal_volatility_passes(self):
        candles = _normal_candles(11, range_size=10.0)
        result = _filter(min_expansion=1.0).validate(candles, _snapshot(atr=10.0, atr_expansion_ratio=1.0))
        assert result.passed is True

    def test_compressed_atr_fails(self):
        candles = _normal_candles(11, range_size=10.0)
        result = _filter().validate(candles, _snapshot(atr=10.0, atr_expansion_ratio=0.5))
        assert result.passed is False
        assert result.rejection_code == "ATR_COMPRESSION"

    def test_low_atr_fails(self):
        candles = _normal_candles(11, range_size=10.0)
        result = _filter(min_atr=5.0).validate(candles, _snapshot(atr=1.0, atr_expansion_ratio=1.5))
        assert result.passed is False
        assert result.rejection_code == "LOW_ATR"

    def test_zero_atr_fails(self):
        candles = _normal_candles(11, range_size=10.0)
        result = _filter().validate(candles, _snapshot(atr=0.0, atr_expansion_ratio=1.5))
        assert result.passed is False
        assert result.rejection_code == "DEAD_MARKET"

    def test_tiny_candle_fails(self):
        candles = _normal_candles(10, range_size=10.0)
        candles.append(_candle(10, 100.5, 100))  # tiny current range vs average 10
        result = _filter().validate(candles, _snapshot(atr=10.0, atr_expansion_ratio=1.5))
        assert result.passed is False
        assert result.rejection_code == "TINY_CANDLE"

    def test_zero_average_range_fails(self):
        flat_candles = [_candle(i, 100, 100) for i in range(10)]  # zero range candles
        flat_candles.append(_candle(10, 110, 100))
        result = _filter().validate(flat_candles, _snapshot(atr=10.0, atr_expansion_ratio=1.5))
        assert result.passed is False
        assert result.rejection_code == "DEAD_MARKET"

    def test_insufficient_candles_handled(self):
        candles = [_candle(0, 110, 100)]  # only the current candle, no lookback
        result = _filter().evaluate(candles, _snapshot(atr=10.0, atr_expansion_ratio=1.5))
        assert result.average_candle_range is None
        assert result.compression_ratio is None
        # With no average available, tiny-candle/compression checks can't
        # apply, so a valid ATR/expansion should still classify as EXPANDING.
        assert result.status in (VolatilityStatus.EXPANDING, VolatilityStatus.NORMAL)

    def test_current_candle_ratio_calculated_correctly(self):
        candles = _normal_candles(10, range_size=10.0)
        candles.append(_candle(10, 120, 100))  # range 20, avg 10 -> ratio 2.0
        result = _filter().evaluate(candles, _snapshot(atr=10.0, atr_expansion_ratio=1.5))
        assert result.compression_ratio == 2.0

    def test_inputs_not_mutated(self):
        candles = _normal_candles(11, range_size=10.0)
        snapshot = _snapshot()
        candles_snapshot = [c.model_copy() for c in candles]
        snapshot_copy = snapshot.model_copy()

        _filter().validate(candles, snapshot)

        assert candles == candles_snapshot
        assert snapshot == snapshot_copy

    def test_score_remains_zero(self):
        candles = _normal_candles(11, range_size=10.0)
        passing = _filter().validate(candles, _snapshot(atr=10.0, atr_expansion_ratio=1.5))
        assert passing.score == 0.0
        failing = _filter().validate(candles, _snapshot(atr=0.0))
        assert failing.score == 0.0
