"""
Unit tests for app.liquidity.equal_high_low.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.liquidity.equal_high_low import EqualHighLowDetector, LiquidityCalculationError
from app.liquidity.results import LiquidityStrength, LiquiditySide, LiquidityType
from app.market_structure.results import SwingPoint, SwingType

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _swing(
    index: int,
    swing_type: SwingType,
    price: float,
    symbol="BTC-USDT",
    timeframe="15m",
) -> SwingPoint:
    candle_index = index + 3
    return SwingPoint(
        swing_id=f"swing-{swing_type.value}-{index}",
        symbol=symbol,
        timeframe=timeframe,
        timestamp=UTC_NOW + timedelta(minutes=index),
        candle_index=candle_index,
        swing_type=swing_type,
        price=price,
        left_strength=3,
        right_strength=3,
        confirmed=True,
    )


def _detector(tolerance=0.001, min_touches=2, max_span=100) -> EqualHighLowDetector:
    return EqualHighLowDetector(
        equality_tolerance=tolerance, minimum_touches=min_touches, maximum_group_span=max_span
    )


class TestDetectEqualHighs:
    def test_two_equal_highs_create_strong_buy_side(self):
        swings = [
            _swing(0, SwingType.HIGH, 100.0),
            _swing(1, SwingType.HIGH, 100.05),
        ]
        levels = _detector().detect_equal_highs(swings)
        assert len(levels) == 1
        assert levels[0].liquidity_type == LiquidityType.EQUAL_HIGH
        assert levels[0].liquidity_side == LiquiditySide.BUY_SIDE
        assert levels[0].strength == LiquidityStrength.STRONG
        assert levels[0].touch_count == 2

    def test_three_equal_highs_create_institutional(self):
        swings = [
            _swing(0, SwingType.HIGH, 100.0),
            _swing(1, SwingType.HIGH, 100.02),
            _swing(2, SwingType.HIGH, 100.01),
        ]
        levels = _detector().detect_equal_highs(swings)
        assert len(levels) == 1
        assert levels[0].strength == LiquidityStrength.INSTITUTIONAL
        assert levels[0].touch_count == 3

    def test_highs_outside_tolerance_not_grouped(self):
        swings = [
            _swing(0, SwingType.HIGH, 100.0),
            _swing(1, SwingType.HIGH, 110.0),
        ]
        levels = _detector(tolerance=0.001).detect_equal_highs(swings)
        assert levels == []

    def test_single_swing_no_equal_liquidity(self):
        swings = [_swing(0, SwingType.HIGH, 100.0)]
        levels = _detector().detect_equal_highs(swings)
        assert levels == []

    def test_mean_price_used(self):
        swings = [
            _swing(0, SwingType.HIGH, 100.0),
            _swing(1, SwingType.HIGH, 100.02),
        ]
        levels = _detector().detect_equal_highs(swings)
        assert levels[0].price == pytest.approx((100.0 + 100.02) / 2)

    def test_source_timestamps_retained(self):
        swings = [
            _swing(0, SwingType.HIGH, 100.0),
            _swing(1, SwingType.HIGH, 100.02),
        ]
        levels = _detector().detect_equal_highs(swings)
        assert set(levels[0].source_timestamps) == {s.timestamp for s in swings}

    def test_mixed_symbol_rejection(self):
        swings = [
            _swing(0, SwingType.HIGH, 100.0, symbol="BTC-USDT"),
            _swing(1, SwingType.HIGH, 100.02, symbol="ETH-USDT"),
        ]
        with pytest.raises(LiquidityCalculationError):
            _detector().detect_equal_highs(swings)

    def test_mixed_timeframe_rejection(self):
        swings = [
            _swing(0, SwingType.HIGH, 100.0, timeframe="15m"),
            _swing(1, SwingType.HIGH, 100.02, timeframe="1h"),
        ]
        with pytest.raises(LiquidityCalculationError):
            _detector().detect_equal_highs(swings)

    def test_no_duplicate_groups(self):
        swings = [
            _swing(0, SwingType.HIGH, 100.0),
            _swing(1, SwingType.HIGH, 100.02),
            _swing(2, SwingType.HIGH, 100.01),
        ]
        levels = _detector().detect_equal_highs(swings)
        touched_ids = set()
        for level in levels:
            for ts in level.source_timestamps:
                assert ts not in touched_ids
                touched_ids.add(ts)

    def test_deterministic_ids(self):
        swings = [
            _swing(0, SwingType.HIGH, 100.0),
            _swing(1, SwingType.HIGH, 100.02),
        ]
        levels_one = _detector().detect_equal_highs(swings)
        levels_two = _detector().detect_equal_highs(swings)
        assert [l.liquidity_id for l in levels_one] == [l.liquidity_id for l in levels_two]

    def test_input_swings_not_mutated(self):
        swings = [
            _swing(0, SwingType.HIGH, 100.0),
            _swing(1, SwingType.HIGH, 100.02),
        ]
        snapshot = [s.model_copy() for s in swings]
        _detector().detect_equal_highs(swings)
        assert swings == snapshot


class TestDetectEqualLows:
    def test_two_equal_lows_create_strong_sell_side(self):
        swings = [
            _swing(0, SwingType.LOW, 90.0),
            _swing(1, SwingType.LOW, 90.05),
        ]
        levels = _detector().detect_equal_lows(swings)
        assert len(levels) == 1
        assert levels[0].liquidity_type == LiquidityType.EQUAL_LOW
        assert levels[0].liquidity_side == LiquiditySide.SELL_SIDE
        assert levels[0].strength == LiquidityStrength.STRONG

    def test_lows_outside_tolerance_not_grouped(self):
        swings = [
            _swing(0, SwingType.LOW, 90.0),
            _swing(1, SwingType.LOW, 80.0),
        ]
        levels = _detector(tolerance=0.001).detect_equal_lows(swings)
        assert levels == []
