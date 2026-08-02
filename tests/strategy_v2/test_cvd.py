"""
Unit tests for app.strategy_v2.cvd (Check 5: CVD Confirmation).
"""

from datetime import datetime, timedelta, timezone

from app.models.candle import Candle
from app.strategy_v2.cvd import calculate_cvd_series, evaluate_cvd_confirmation

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(index: int, open_: float, close: float, volume: float = 10.0) -> Candle:
    high = max(open_, close) + 0.5
    low = min(open_, close) - 0.5
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        symbol="BTC-USDT",
        timeframe="15m",
    )


def _bullish(index: int, volume: float = 10.0) -> Candle:
    return _candle(index, 100.0, 101.0, volume)


def _bearish(index: int, volume: float = 10.0) -> Candle:
    return _candle(index, 101.0, 100.0, volume)


def _doji(index: int, volume: float = 10.0) -> Candle:
    return _candle(index, 100.0, 100.0, volume)


class TestCalculateCvdSeries:
    def test_bullish_candle_adds_positive_delta(self):
        series = calculate_cvd_series([_bullish(0, volume=10.0)])
        assert series[0].cvd == 10.0

    def test_bearish_candle_adds_negative_delta(self):
        series = calculate_cvd_series([_bearish(0, volume=10.0)])
        assert series[0].cvd == -10.0

    def test_doji_contributes_zero(self):
        series = calculate_cvd_series([_bullish(0, volume=10.0), _doji(1, volume=50.0)])
        assert series[1].cvd == 10.0

    def test_running_cumulative_total(self):
        series = calculate_cvd_series(
            [_bullish(0, volume=10.0), _bullish(1, volume=5.0), _bearish(2, volume=3.0)]
        )
        assert [p.cvd for p in series] == [10.0, 15.0, 12.0]

    def test_empty_candles_yields_empty_series(self):
        assert calculate_cvd_series([]) == []


class TestEvaluateCvdConfirmation:
    def test_empty_candles_fails(self):
        result = evaluate_cvd_confirmation([], expected_direction="BUY")
        assert result.passed is False

    def test_insufficient_swings_fails_for_buy(self):
        # Monotonically rising CVD: no swing lows at all.
        candles = [_bullish(i) for i in range(10)]
        result = evaluate_cvd_confirmation(candles, expected_direction="BUY")
        assert result.passed is False
        assert "fewer than two" in result.reason.lower()

    def test_insufficient_swings_fails_for_sell(self):
        candles = [_bearish(i) for i in range(10)]
        result = evaluate_cvd_confirmation(candles, expected_direction="SELL")
        assert result.passed is False
        assert "fewer than two" in result.reason.lower()

    def test_explicit_higher_low_passes(self):
        # Hand-crafted deterministic CVD series via direct deltas:
        # index: 0  1  2  3  4  5  6  7  8  9  10 11 12 13
        # delta: +5 +5 -8 -8 -8 +5 +5 +5 +5 -4 -4 -4 +5 +5
        # cvd:   5 10  2 -6-14 -9 -4  1  6  2 -2 -6 -1  4
        deltas = [5, 5, -8, -8, -8, 5, 5, 5, 5, -4, -4, -4, 5, 5]
        candles = [
            _bullish(i, volume=d) if d > 0 else _bearish(i, volume=-d) for i, d in enumerate(deltas)
        ]
        series = calculate_cvd_series(candles)
        values = [p.cvd for p in series]
        # cvd = [5, 10, 2, -6, -14, -9, -4, 1, 6, 2, -2, -6, -1, 4]
        assert values == [5, 10, 2, -6, -14, -9, -4, 1, 6, 2, -2, -6, -1, 4]

        result = evaluate_cvd_confirmation(candles, expected_direction="BUY", left_strength=2, right_strength=2)
        # Swing low at index 4 (-14) is a confirmed local min with
        # left_strength=2/right_strength=2; swing low at index 11 (-6)
        # is higher than -14 -> higher low -> BUY confirmed.
        assert result.passed is True
        assert "higher low" in result.reason.lower()

    def test_explicit_lower_high_passes(self):
        deltas = [-5, -5, 8, 8, 8, -5, -5, -5, -5, 4, 4, 4, -5, -5]
        candles = [
            _bullish(i, volume=d) if d > 0 else _bearish(i, volume=-d) for i, d in enumerate(deltas)
        ]
        series = calculate_cvd_series(candles)
        values = [p.cvd for p in series]
        assert values == [-5, -10, -2, 6, 14, 9, 4, -1, -6, -2, 2, 6, 1, -4]

        result = evaluate_cvd_confirmation(candles, expected_direction="SELL", left_strength=2, right_strength=2)
        # Swing high at index 4 (14) then swing high at index 11 (6):
        # 6 < 14 -> lower high -> SELL confirmed.
        assert result.passed is True
        assert "lower high" in result.reason.lower()

    def test_explicit_lower_low_fails_buy(self):
        # Mirror of the higher-low case but the second low is LOWER, not higher.
        deltas = [5, 5, -6, -6, -6, 5, 5, 5, 5, -8, -8, -8, 5, 5]
        candles = [
            _bullish(i, volume=d) if d > 0 else _bearish(i, volume=-d) for i, d in enumerate(deltas)
        ]
        series = calculate_cvd_series(candles)
        values = [p.cvd for p in series]
        # cvd = [5, 10, 4, -2, -8, -3, 2, 7, 12, 4, -4, -12, -7, -2]
        assert values == [5, 10, 4, -2, -8, -3, 2, 7, 12, 4, -4, -12, -7, -2]

        result = evaluate_cvd_confirmation(candles, expected_direction="BUY", left_strength=2, right_strength=2)
        # Swing low at index 4 (-8), swing low at index 11 (-12): -12 < -8 -> lower low, not higher -> fails.
        assert result.passed is False
        assert "did not form a higher low" in result.reason.lower()
