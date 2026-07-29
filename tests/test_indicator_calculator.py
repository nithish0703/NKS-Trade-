"""
Unit tests for app.indicators.calculator.IndicatorCalculator.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.config.thresholds import (
    ADX_PERIOD,
    ATR_EXPANSION_LOOKBACK,
    ATR_PERIOD,
    EMA_FAST_PERIOD,
    EMA_SLOPE_LOOKBACK,
    EMA_SLOW_PERIOD,
    EMA_TREND_PERIOD,
    VOLUME_EMA_PERIOD,
)
from app.indicators.calculator import IndicatorCalculator
from app.indicators.ema import IndicatorCalculationError
from app.indicators.results import IndicatorSnapshot
from app.models.candle import Candle

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

# Enough candles for EMA200 seed + slope lookback + ATR expansion lookback
# + ADX warmup (2 * period + 1), with generous headroom.
CANDLE_COUNT = (
    EMA_TREND_PERIOD
    + EMA_SLOPE_LOOKBACK
    + ATR_EXPANSION_LOOKBACK
    + (ADX_PERIOD * 2)
    + 50
)


def _make_candles(count: int, symbol: str = "BTC-USDT", timeframe: str = "1h") -> list[Candle]:
    candles = []
    for i in range(count):
        base = 100 + (i % 20) * 0.5 + i * 0.05
        high = base + 2
        low = base - 2
        close = base + 0.5
        volume = 1000 + (i % 10) * 10
        candles.append(
            Candle(
                timestamp=UTC_NOW + timedelta(hours=i),
                open=base,
                high=high,
                low=low,
                close=close,
                volume=volume,
                symbol=symbol,
                timeframe=timeframe,
            )
        )
    return candles


class TestCalculateSnapshot:
    def test_complete_indicator_snapshot(self):
        candles = _make_candles(CANDLE_COUNT)
        calculator = IndicatorCalculator()
        snapshot = calculator.calculate_snapshot(candles)
        assert isinstance(snapshot, IndicatorSnapshot)

    def test_ema_values_available(self):
        candles = _make_candles(CANDLE_COUNT)
        snapshot = IndicatorCalculator().calculate_snapshot(candles)
        assert snapshot.ema20 is not None
        assert snapshot.ema50 is not None
        assert snapshot.ema200 is not None

    def test_ema_slope_and_direction_available(self):
        candles = _make_candles(CANDLE_COUNT)
        snapshot = IndicatorCalculator().calculate_snapshot(candles)
        assert snapshot.ema200_slope is not None
        assert snapshot.ema200_slope_direction in ("BULLISH", "BEARISH", "FLAT")

    def test_atr_available(self):
        candles = _make_candles(CANDLE_COUNT)
        snapshot = IndicatorCalculator().calculate_snapshot(candles)
        assert snapshot.atr is not None

    def test_atr_expansion_available(self):
        candles = _make_candles(CANDLE_COUNT)
        snapshot = IndicatorCalculator().calculate_snapshot(candles)
        assert snapshot.atr_expansion_ratio is not None

    def test_adx_and_directional_indicators_available(self):
        candles = _make_candles(CANDLE_COUNT)
        snapshot = IndicatorCalculator().calculate_snapshot(candles)
        assert snapshot.adx is not None
        assert snapshot.plus_di is not None
        assert snapshot.minus_di is not None

    def test_volume_ema_and_ratio_available(self):
        candles = _make_candles(CANDLE_COUNT)
        snapshot = IndicatorCalculator().calculate_snapshot(candles)
        assert snapshot.volume_ema20 is not None
        assert snapshot.volume_ratio is not None

    def test_insufficient_candles_raises(self):
        candles = _make_candles(10)
        with pytest.raises(IndicatorCalculationError):
            IndicatorCalculator().calculate_snapshot(candles)

    def test_empty_candles_raises(self):
        with pytest.raises(IndicatorCalculationError):
            IndicatorCalculator().calculate_snapshot([])

    def test_non_chronological_candles_rejected(self):
        candles = _make_candles(CANDLE_COUNT)
        shuffled = [candles[1], candles[0]] + candles[2:]
        with pytest.raises(IndicatorCalculationError):
            IndicatorCalculator().calculate_snapshot(shuffled)

    def test_candle_input_remains_unchanged(self):
        candles = _make_candles(CANDLE_COUNT)
        snapshot_copy = [c.model_copy() for c in candles]
        IndicatorCalculator().calculate_snapshot(candles)
        assert candles == snapshot_copy

    def test_no_trade_decision_fields_in_snapshot(self):
        candles = _make_candles(CANDLE_COUNT)
        snapshot = IndicatorCalculator().calculate_snapshot(candles)
        snapshot_fields = set(type(snapshot).model_fields.keys())
        forbidden_fields = {
            "direction",
            "confidence_score",
            "accepted",
            "rejected",
            "rejection_reason",
            "signal_type",
            "trade_decision",
        }
        assert snapshot_fields.isdisjoint(forbidden_fields)


class TestCalculateMultipleTimeframes:
    def test_multiple_timeframe_calculation(self):
        candles_by_timeframe = {
            "1h": _make_candles(CANDLE_COUNT, timeframe="1h"),
            "4h": _make_candles(CANDLE_COUNT, timeframe="4h"),
        }
        result = IndicatorCalculator().calculate_multiple_timeframes(
            candles_by_timeframe
        )
        assert set(result.keys()) == {"1h", "4h"}
        for snapshot in result.values():
            assert isinstance(snapshot, IndicatorSnapshot)

    def test_one_timeframe_failure_rejects_whole_calculation(self):
        candles_by_timeframe = {
            "1h": _make_candles(CANDLE_COUNT, timeframe="1h"),
            "4h": _make_candles(5, timeframe="4h"),
        }
        with pytest.raises(IndicatorCalculationError):
            IndicatorCalculator().calculate_multiple_timeframes(candles_by_timeframe)
