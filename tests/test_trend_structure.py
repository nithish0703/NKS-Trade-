"""
Unit tests for app.market_structure.trend_structure.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.market_structure.results import StructureLabel, SwingPoint, SwingType, TrendDirection
from app.market_structure.swing_detector import MarketStructureCalculationError
from app.market_structure.trend_structure import TrendStructureAnalyzer

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _swing(
    index: int,
    swing_type: SwingType,
    price: float,
    symbol="BTC-USDT",
    timeframe="15m",
    confirmed=True,
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
        confirmed=confirmed,
    )


def _analyzer(tolerance=0.001, minimum=4) -> TrendStructureAnalyzer:
    return TrendStructureAnalyzer(equality_tolerance=tolerance, minimum_confirmed_swings=minimum)


class TestClassifySwings:
    def test_first_swing_high_unclassified(self):
        swings = [_swing(0, SwingType.HIGH, 100)]
        classified = _analyzer().classify_swings(swings)
        assert classified[0].structure_label == StructureLabel.UNCLASSIFIED

    def test_first_swing_low_unclassified(self):
        swings = [_swing(0, SwingType.LOW, 90)]
        classified = _analyzer().classify_swings(swings)
        assert classified[0].structure_label == StructureLabel.UNCLASSIFIED

    def test_higher_high_classification(self):
        swings = [_swing(0, SwingType.HIGH, 100), _swing(1, SwingType.HIGH, 110)]
        classified = _analyzer().classify_swings(swings)
        assert classified[1].structure_label == StructureLabel.HIGHER_HIGH
        assert classified[1].previous_same_type_swing_id == swings[0].swing_id

    def test_lower_high_classification(self):
        swings = [_swing(0, SwingType.HIGH, 110), _swing(1, SwingType.HIGH, 100)]
        classified = _analyzer().classify_swings(swings)
        assert classified[1].structure_label == StructureLabel.LOWER_HIGH

    def test_equal_high_classification(self):
        swings = [_swing(0, SwingType.HIGH, 100), _swing(1, SwingType.HIGH, 100.05)]
        classified = _analyzer(tolerance=0.001).classify_swings(swings)
        assert classified[1].structure_label == StructureLabel.EQUAL_HIGH

    def test_higher_low_classification(self):
        swings = [_swing(0, SwingType.LOW, 90), _swing(1, SwingType.LOW, 95)]
        classified = _analyzer().classify_swings(swings)
        assert classified[1].structure_label == StructureLabel.HIGHER_LOW

    def test_lower_low_classification(self):
        swings = [_swing(0, SwingType.LOW, 95), _swing(1, SwingType.LOW, 90)]
        classified = _analyzer().classify_swings(swings)
        assert classified[1].structure_label == StructureLabel.LOWER_LOW

    def test_equal_low_classification(self):
        swings = [_swing(0, SwingType.LOW, 90), _swing(1, SwingType.LOW, 90.05)]
        classified = _analyzer(tolerance=0.001).classify_swings(swings)
        assert classified[1].structure_label == StructureLabel.EQUAL_LOW

    def test_comparison_uses_previous_same_type_swing_only(self):
        swings = [
            _swing(0, SwingType.HIGH, 100),
            _swing(1, SwingType.LOW, 90),
            _swing(2, SwingType.HIGH, 110),
        ]
        classified = _analyzer().classify_swings(swings)
        high_at_2 = next(cs for cs in classified if cs.swing.swing_id == swings[2].swing_id)
        assert high_at_2.previous_same_type_swing_id == swings[0].swing_id

    def test_mixed_symbol_rejection(self):
        swings = [
            _swing(0, SwingType.HIGH, 100, symbol="BTC-USDT"),
            _swing(1, SwingType.HIGH, 110, symbol="ETH-USDT"),
        ]
        with pytest.raises(MarketStructureCalculationError):
            _analyzer().classify_swings(swings)

    def test_mixed_timeframe_rejection(self):
        swings = [
            _swing(0, SwingType.HIGH, 100, timeframe="15m"),
            _swing(1, SwingType.HIGH, 110, timeframe="1h"),
        ]
        with pytest.raises(MarketStructureCalculationError):
            _analyzer().classify_swings(swings)

    def test_duplicate_swing_id_rejection(self):
        s1 = _swing(0, SwingType.HIGH, 100)
        s2 = s1.model_copy(update={"price": 110})
        with pytest.raises(MarketStructureCalculationError):
            _analyzer().classify_swings([s1, s2])

    def test_input_swings_not_mutated(self):
        swings = [_swing(0, SwingType.HIGH, 100), _swing(1, SwingType.HIGH, 110)]
        snapshot = [s.model_copy() for s in swings]
        _analyzer().classify_swings(swings)
        assert swings == snapshot


def _make_bullish_swings() -> list[SwingPoint]:
    return [
        _swing(0, SwingType.LOW, 90),
        _swing(1, SwingType.HIGH, 100),
        _swing(2, SwingType.LOW, 95),
        _swing(3, SwingType.HIGH, 110),
    ]


def _make_bearish_swings() -> list[SwingPoint]:
    return [
        _swing(0, SwingType.HIGH, 110),
        _swing(1, SwingType.LOW, 100),
        _swing(2, SwingType.HIGH, 105),
        _swing(3, SwingType.LOW, 90),
    ]


class TestAnalyze:
    def test_bullish_structure_result(self):
        swings = _make_bullish_swings()
        result = _analyzer().analyze([], swings)
        assert result.trend_direction == TrendDirection.BULLISH

    def test_bearish_structure_result(self):
        swings = _make_bearish_swings()
        result = _analyzer().analyze([], swings)
        assert result.trend_direction == TrendDirection.BEARISH

    def test_range_structure_result(self):
        swings = [
            _swing(0, SwingType.HIGH, 100),
            _swing(1, SwingType.LOW, 90),
            _swing(2, SwingType.HIGH, 100.02),
            _swing(3, SwingType.LOW, 90.02),
        ]
        result = _analyzer(tolerance=0.001).analyze([], swings)
        assert result.trend_direction == TrendDirection.RANGE

    def test_unknown_result_with_insufficient_swings(self):
        swings = [_swing(0, SwingType.HIGH, 100), _swing(1, SwingType.LOW, 90)]
        result = _analyzer(minimum=4).analyze([], swings)
        assert result.trend_direction == TrendDirection.UNKNOWN

    def test_newer_contradictory_structure_invalidates_older_direction(self):
        # Starts bullish (HH, HL) then reverses with a newer LH/LL.
        swings = [
            _swing(0, SwingType.LOW, 90),
            _swing(1, SwingType.HIGH, 100),
            _swing(2, SwingType.LOW, 95),
            _swing(3, SwingType.HIGH, 110),
            _swing(4, SwingType.LOW, 85),
            _swing(5, SwingType.HIGH, 105),
        ]
        result = _analyzer().analyze([], swings)
        assert result.trend_direction != TrendDirection.BULLISH

    def test_result_has_no_trade_decision_fields(self):
        swings = _make_bullish_swings()
        result = _analyzer().analyze([], swings)
        result_fields = set(type(result).model_fields.keys())
        forbidden = {
            "direction",
            "confidence_score",
            "entry_price",
            "stop_loss",
            "take_profit",
            "accepted",
            "rejected",
        }
        assert result_fields.isdisjoint(forbidden)
