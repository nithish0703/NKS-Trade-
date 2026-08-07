"""
Unit tests for app.market_structure.swing_detector.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.market_structure.results import SwingType
from app.market_structure.swing_detector import (
    MarketStructureCalculationError,
    SwingDetector,
)
from app.models.candle import Candle

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(index: int, high: float, low: float, symbol="BTC-USDT", timeframe="15m") -> Candle:
    close = (high + low) / 2
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=close,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        symbol=symbol,
        timeframe=timeframe,
    )


def _make_detector(left=3, right=3, tolerance=0.001) -> SwingDetector:
    return SwingDetector(left_strength=left, right_strength=right, equality_tolerance=tolerance)


def _flat_series(count: int, base_high=100.0, base_low=90.0) -> list[Candle]:
    return [_candle(i, base_high, base_low) for i in range(count)]


class TestDetectSwings:
    def test_valid_confirmed_swing_high(self):
        highs = [100, 101, 102, 110, 102, 101, 100]
        lows = [h - 10 for h in highs]
        candles = [_candle(i, h, l) for i, (h, l) in enumerate(zip(highs, lows))]
        detector = _make_detector(left=3, right=3)
        swings = detector.detect_swings(candles)
        high_swings = [s for s in swings if s.swing_type == SwingType.HIGH]
        assert len(high_swings) == 1
        assert high_swings[0].candle_index == 3
        assert high_swings[0].price == 110

    def test_valid_confirmed_swing_low(self):
        lows = [90, 89, 88, 80, 88, 89, 90]
        highs = [l + 10 for l in lows]
        candles = [_candle(i, h, l) for i, (h, l) in enumerate(zip(highs, lows))]
        detector = _make_detector(left=3, right=3)
        swings = detector.detect_swings(candles)
        low_swings = [s for s in swings if s.swing_type == SwingType.LOW]
        assert len(low_swings) == 1
        assert low_swings[0].candle_index == 3
        assert low_swings[0].price == 80

    def test_first_edge_candles_not_confirmed(self):
        highs = [110, 101, 102, 103, 104, 105, 106]
        lows = [h - 10 for h in highs]
        candles = [_candle(i, h, l) for i, (h, l) in enumerate(zip(highs, lows))]
        detector = _make_detector(left=3, right=3)
        swings = detector.detect_swings(candles)
        assert all(s.candle_index != 0 for s in swings)

    def test_final_edge_candles_not_confirmed(self):
        highs = [100, 101, 102, 103, 104, 105, 110]
        lows = [h - 10 for h in highs]
        candles = [_candle(i, h, l) for i, (h, l) in enumerate(zip(highs, lows))]
        detector = _make_detector(left=3, right=3)
        swings = detector.detect_swings(candles)
        assert all(s.candle_index != len(candles) - 1 for s in swings)

    def test_deterministic_swing_id(self):
        highs = [100, 101, 102, 110, 102, 101, 100]
        lows = [h - 10 for h in highs]
        candles = [_candle(i, h, l) for i, (h, l) in enumerate(zip(highs, lows))]
        detector = _make_detector(left=3, right=3)
        swings_one = detector.detect_swings(candles)
        swings_two = detector.detect_swings(candles)
        assert [s.swing_id for s in swings_one] == [s.swing_id for s in swings_two]

    def test_ascending_result_order(self):
        highs = [100, 101, 102, 110, 90, 91, 92, 80, 93, 94, 95]
        lows = [h - 10 for h in highs]
        candles = [_candle(i, h, l) for i, (h, l) in enumerate(zip(highs, lows))]
        detector = _make_detector(left=3, right=3)
        swings = detector.detect_swings(candles)
        timestamps = [s.timestamp for s in swings]
        assert timestamps == sorted(timestamps)

    def test_no_duplicate_swings(self):
        highs = [100, 101, 102, 110, 102, 101, 100]
        lows = [h - 10 for h in highs]
        candles = [_candle(i, h, l) for i, (h, l) in enumerate(zip(highs, lows))]
        detector = _make_detector(left=3, right=3)
        swings = detector.detect_swings(candles)
        ids = [s.swing_id for s in swings]
        assert len(ids) == len(set(ids))

    def test_mixed_symbol_rejection(self):
        candles = [
            _candle(0, 100, 90, symbol="BTC-USDT"),
            _candle(1, 101, 91, symbol="ETH-USDT"),
        ]
        detector = _make_detector()
        with pytest.raises(MarketStructureCalculationError):
            detector.detect_swings(candles)

    def test_mixed_timeframe_rejection(self):
        candles = [
            _candle(0, 100, 90, timeframe="15m"),
            _candle(1, 101, 91, timeframe="1h"),
        ]
        detector = _make_detector()
        with pytest.raises(MarketStructureCalculationError):
            detector.detect_swings(candles)

    def test_non_ascending_timestamp_rejection(self):
        c0 = _candle(0, 100, 90)
        c1 = _candle(1, 101, 91)
        detector = _make_detector()
        with pytest.raises(MarketStructureCalculationError):
            detector.detect_swings([c1, c0])

    def test_empty_candle_rejection(self):
        detector = _make_detector()
        with pytest.raises(MarketStructureCalculationError):
            detector.detect_swings([])

    def test_one_candle_satisfying_both_swing_rules(self):
        # A single spike candle with a higher high AND lower low than all
        # neighbours satisfies both the swing-high and swing-low rule.
        candles = [
            _candle(0, 100, 95),
            _candle(1, 101, 94),
            _candle(2, 102, 93),
            _candle(3, 120, 80),
            _candle(4, 102, 93),
            _candle(5, 101, 94),
            _candle(6, 100, 95),
        ]
        detector = _make_detector(left=3, right=3)
        swings = detector.detect_swings(candles)
        at_index_3 = [s for s in swings if s.candle_index == 3]
        assert {s.swing_type for s in at_index_3} == {SwingType.HIGH, SwingType.LOW}

    def test_input_candles_not_mutated(self):
        highs = [100, 101, 102, 110, 102, 101, 100]
        lows = [h - 10 for h in highs]
        candles = [_candle(i, h, l) for i, (h, l) in enumerate(zip(highs, lows))]
        snapshot = [c.model_copy() for c in candles]
        detector = _make_detector(left=3, right=3)
        detector.detect_swings(candles)
        assert candles == snapshot

    def test_constructor_validation(self):
        with pytest.raises(MarketStructureCalculationError):
            SwingDetector(left_strength=0, right_strength=3, equality_tolerance=0.001)
        with pytest.raises(MarketStructureCalculationError):
            SwingDetector(left_strength=3, right_strength=0, equality_tolerance=0.001)
        with pytest.raises(MarketStructureCalculationError):
            SwingDetector(left_strength=3, right_strength=3, equality_tolerance=-0.01)


class TestDeterministicSwingId:
    def test_swing_id_stable_across_shifted_window(self):
        """
        DuplicateGuard lifecycle regression: the same real swing candle
        must produce the same swing_id whether it's near the start or
        the end of the fetched candle window -- since the window rolls
        forward every scan cycle, a swing_id tied to array position
        (rather than the candle's own timestamp) would make the same
        real setup look "new" on every scan and defeat duplicate
        suppression.
        """
        highs = [100, 99, 98, 97, 110, 97, 98, 99, 100, 101, 102, 103]
        lows = [h - 10 for h in highs]
        base_candles = [_candle(i, h, l) for i, (h, l) in enumerate(zip(highs, lows))]

        # Same real candles, but "window 2" starts one candle later than
        # "window 1" -- simulating a later scan cycle's freshly fetched,
        # rolled-forward window. Both windows keep enough left/right
        # context around the peak candle to still confirm it as a swing.
        window_one = base_candles[0:9]
        window_two = base_candles[1:10]

        detector = _make_detector(left=3, right=3)
        swings_one = detector.detect_swings(window_one)
        swings_two = detector.detect_swings(window_two)

        by_timestamp_one = {s.timestamp: s.swing_id for s in swings_one}
        by_timestamp_two = {s.timestamp: s.swing_id for s in swings_two}
        common_timestamps = set(by_timestamp_one) & set(by_timestamp_two)

        assert common_timestamps, "expected at least one swing common to both windows"
        for timestamp in common_timestamps:
            assert by_timestamp_one[timestamp] == by_timestamp_two[timestamp]


class TestLatestSwings:
    def test_latest_confirmed_swing_high(self):
        highs = [100, 101, 102, 110, 90, 91, 92, 105, 93, 94, 95]
        lows = [h - 10 for h in highs]
        candles = [_candle(i, h, l) for i, (h, l) in enumerate(zip(highs, lows))]
        detector = _make_detector(left=3, right=3)
        latest = detector.detect_latest_swing_high(candles)
        assert latest is not None
        assert latest.swing_type == SwingType.HIGH

    def test_latest_confirmed_swing_low(self):
        lows = [90, 89, 88, 80, 100, 99, 98, 70, 97, 96, 95]
        highs = [l + 10 for l in lows]
        candles = [_candle(i, h, l) for i, (h, l) in enumerate(zip(highs, lows))]
        detector = _make_detector(left=3, right=3)
        latest = detector.detect_latest_swing_low(candles)
        assert latest is not None
        assert latest.swing_type == SwingType.LOW

    def test_no_swings_returns_none(self):
        candles = _flat_series(4)
        detector = _make_detector(left=3, right=3)
        assert detector.detect_latest_swing_high(candles) is None
        assert detector.detect_latest_swing_low(candles) is None
