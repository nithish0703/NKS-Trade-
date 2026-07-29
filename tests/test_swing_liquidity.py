"""
Unit tests for app.liquidity.swing_liquidity.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.liquidity.results import LiquidityStrength, LiquidityType
from app.liquidity.equal_high_low import LiquidityCalculationError
from app.liquidity.swing_liquidity import SwingLiquidityDetector
from app.market_structure.results import SwingPoint, SwingType

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _swing(
    index: int,
    swing_type: SwingType,
    price: float,
    left_strength: int,
    right_strength: int,
    confirmed: bool = True,
) -> SwingPoint:
    candle_index = index + right_strength
    return SwingPoint(
        swing_id=f"swing-{swing_type.value}-{index}-{left_strength}-{right_strength}",
        symbol="BTC-USDT",
        timeframe="15m",
        timestamp=UTC_NOW + timedelta(minutes=index),
        candle_index=candle_index,
        swing_type=swing_type,
        price=price,
        left_strength=left_strength,
        right_strength=right_strength,
        confirmed=confirmed,
    )


class TestSwingLiquidityDetector:
    def test_qualifying_swing_high_creates_major_swing_high(self):
        swings = [_swing(0, SwingType.HIGH, 110, left_strength=3, right_strength=3)]
        detector = SwingLiquidityDetector(minimum_swing_strength=3)
        levels = detector.detect_major_swing_levels(swings)
        assert len(levels) == 1
        assert levels[0].liquidity_type == LiquidityType.MAJOR_SWING_HIGH

    def test_qualifying_swing_low_creates_major_swing_low(self):
        swings = [_swing(0, SwingType.LOW, 90, left_strength=3, right_strength=3)]
        detector = SwingLiquidityDetector(minimum_swing_strength=3)
        levels = detector.detect_major_swing_levels(swings)
        assert len(levels) == 1
        assert levels[0].liquidity_type == LiquidityType.MAJOR_SWING_LOW

    def test_weak_swing_excluded(self):
        swings = [_swing(0, SwingType.HIGH, 110, left_strength=2, right_strength=2)]
        detector = SwingLiquidityDetector(minimum_swing_strength=3)
        levels = detector.detect_major_swing_levels(swings)
        assert levels == []

    def test_institutional_strength_assignment(self):
        swings = [_swing(0, SwingType.HIGH, 110, left_strength=5, right_strength=5)]
        detector = SwingLiquidityDetector(minimum_swing_strength=3)
        levels = detector.detect_major_swing_levels(swings)
        assert levels[0].strength == LiquidityStrength.INSTITUTIONAL

    def test_strong_strength_assignment(self):
        swings = [_swing(0, SwingType.HIGH, 110, left_strength=3, right_strength=3)]
        detector = SwingLiquidityDetector(minimum_swing_strength=3)
        levels = detector.detect_major_swing_levels(swings)
        assert levels[0].strength == LiquidityStrength.STRONG

    def test_deterministic_ids(self):
        swings = [_swing(0, SwingType.HIGH, 110, left_strength=3, right_strength=3)]
        detector = SwingLiquidityDetector(minimum_swing_strength=3)
        levels_one = detector.detect_major_swing_levels(swings)
        levels_two = detector.detect_major_swing_levels(swings)
        assert levels_one[0].liquidity_id == levels_two[0].liquidity_id

    def test_no_duplicates(self):
        swing = _swing(0, SwingType.HIGH, 110, left_strength=3, right_strength=3)
        detector = SwingLiquidityDetector(minimum_swing_strength=3)
        levels = detector.detect_major_swing_levels([swing, swing])
        assert len(levels) == 1

    def test_chronological_ordering(self):
        swings = [
            _swing(2, SwingType.HIGH, 130, left_strength=3, right_strength=3),
            _swing(0, SwingType.HIGH, 110, left_strength=3, right_strength=3),
            _swing(1, SwingType.LOW, 90, left_strength=3, right_strength=3),
        ]
        detector = SwingLiquidityDetector(minimum_swing_strength=3)
        levels = detector.detect_major_swing_levels(swings)
        timestamps = [level.start_timestamp for level in levels]
        assert timestamps == sorted(timestamps)

    def test_input_swings_not_mutated(self):
        swings = [_swing(0, SwingType.HIGH, 110, left_strength=3, right_strength=3)]
        snapshot = [s.model_copy() for s in swings]
        SwingLiquidityDetector(minimum_swing_strength=3).detect_major_swing_levels(swings)
        assert swings == snapshot

    def test_unconfirmed_swing_excluded(self):
        swings = [
            _swing(
                0, SwingType.HIGH, 110, left_strength=3, right_strength=3, confirmed=False
            )
        ]
        detector = SwingLiquidityDetector(minimum_swing_strength=3)
        levels = detector.detect_major_swing_levels(swings)
        assert levels == []

    def test_invalid_minimum_strength(self):
        with pytest.raises(LiquidityCalculationError):
            SwingLiquidityDetector(minimum_swing_strength=0)
