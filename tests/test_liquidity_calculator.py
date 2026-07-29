"""
Unit tests for app.liquidity.calculator.LiquidityCalculator.
"""

from datetime import datetime, timedelta, timezone

from app.liquidity.calculator import LiquidityCalculator
from app.liquidity.equal_high_low import EqualHighLowDetector
from app.liquidity.level_selector import InstitutionalLiquiditySelector
from app.liquidity.previous_levels import PreviousPeriodLevelDetector
from app.liquidity.results import LiquidityDetectionResult
from app.liquidity.session_levels import SessionLiquidityDetector
from app.liquidity.sweep_detector import LiquiditySweepDetector
from app.liquidity.swing_liquidity import SwingLiquidityDetector
from app.market_structure.results import SwingPoint, SwingType
from app.market_structure.swing_detector import SwingDetector
from app.models.candle import Candle

UTC_NOW = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)


def _make_calculator(
    min_touches=2, min_swing_strength=3, min_penetration=0.0001, max_reclaim=2
) -> LiquidityCalculator:
    return LiquidityCalculator(
        equal_high_low_detector=EqualHighLowDetector(
            equality_tolerance=0.001, minimum_touches=min_touches, maximum_group_span=100
        ),
        previous_period_level_detector=PreviousPeriodLevelDetector(),
        swing_liquidity_detector=SwingLiquidityDetector(minimum_swing_strength=min_swing_strength),
        session_liquidity_detector=SessionLiquidityDetector(),
        institutional_liquidity_selector=InstitutionalLiquiditySelector(),
        liquidity_sweep_detector=LiquiditySweepDetector(
            minimum_penetration_ratio=min_penetration, maximum_reclaim_candles=max_reclaim
        ),
    )


def _hourly_candles_for_day(day: datetime, base_price: float, symbol="BTC-USDT") -> list[Candle]:
    candles = []
    for hour in range(24):
        ts = day.replace(hour=hour, minute=0, second=0, microsecond=0)
        price = base_price + hour * 0.1
        candles.append(
            Candle(
                timestamp=ts,
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=100.0,
                symbol=symbol,
                timeframe="1h",
            )
        )
    return candles


def _swing(index: int, swing_type: SwingType, price: float) -> SwingPoint:
    candle_index = index + 3
    return SwingPoint(
        swing_id=f"swing-{swing_type.value}-{index}",
        symbol="BTC-USDT",
        timeframe="1h",
        timestamp=UTC_NOW - timedelta(hours=48) + timedelta(minutes=index),
        candle_index=candle_index,
        swing_type=swing_type,
        price=price,
        left_strength=3,
        right_strength=3,
        confirmed=True,
    )


class TestDetectLevels:
    def test_complete_liquidity_detection_result(self):
        previous_day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        candles = _hourly_candles_for_day(previous_day, 100.0)
        swings = [
            _swing(0, SwingType.HIGH, 110.0),
            _swing(1, SwingType.HIGH, 110.02),
        ]

        result = _make_calculator().detect_levels(candles, swings, UTC_NOW)
        assert isinstance(result, LiquidityDetectionResult)

    def test_equal_highs_and_lows_included(self):
        candles = []
        swings = [
            _swing(0, SwingType.HIGH, 110.0),
            _swing(1, SwingType.HIGH, 110.02),
            _swing(2, SwingType.LOW, 90.0),
            _swing(3, SwingType.LOW, 90.02),
        ]
        result = _make_calculator().detect_levels(candles, swings, UTC_NOW)
        assert len(result.equal_highs) == 1
        assert len(result.equal_lows) == 1

    def test_previous_day_levels_included(self):
        previous_day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        candles = _hourly_candles_for_day(previous_day, 100.0)
        result = _make_calculator().detect_levels(candles, [], UTC_NOW)
        assert len(result.previous_day_levels) == 2

    def test_previous_week_levels_included(self):
        reference = datetime(2026, 1, 12, 10, 0, tzinfo=timezone.utc)
        candles = []
        for day_offset in range(7):
            day = datetime(2026, 1, 5, tzinfo=timezone.utc) + timedelta(days=day_offset)
            candles.extend(_hourly_candles_for_day(day, 100.0 + day_offset))
        result = _make_calculator().detect_levels(candles, [], reference)
        assert len(result.previous_week_levels) == 2

    def test_major_swings_included(self):
        swings = [_swing(0, SwingType.HIGH, 110.0)]
        result = _make_calculator(min_swing_strength=3).detect_levels([], swings, UTC_NOW)
        assert len(result.major_swing_levels) == 1

    def test_session_levels_included(self):
        day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        candles = [
            Candle(
                timestamp=day.replace(hour=h),
                open=100 + h,
                high=101 + h,
                low=99 + h,
                close=100 + h,
                volume=100.0,
                symbol="BTC-USDT",
                timeframe="1h",
            )
            for h in range(8)
        ]
        reference = day + timedelta(hours=9)
        result = _make_calculator().detect_levels(candles, [], reference)
        assert len(result.session_levels) >= 2

    def test_active_levels_selected(self):
        previous_day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        candles = _hourly_candles_for_day(previous_day, 100.0)
        result = _make_calculator().detect_levels(candles, [], UTC_NOW)
        assert len(result.active_levels) > 0
        assert len(result.active_levels) <= len(result.all_levels)

    def test_unavailable_previous_period_captured_in_metadata(self):
        result = _make_calculator().detect_levels([], [], UTC_NOW)
        assert result.metadata is not None
        assert result.metadata.get("previous_day_levels_unavailable") is True
        assert result.metadata.get("previous_week_levels_unavailable") is True

    def test_input_candles_and_swings_not_mutated(self):
        previous_day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        candles = _hourly_candles_for_day(previous_day, 100.0)
        swings = [_swing(0, SwingType.HIGH, 110.0)]
        candles_snapshot = [c.model_copy() for c in candles]
        swings_snapshot = [s.model_copy() for s in swings]

        _make_calculator().detect_levels(candles, swings, UTC_NOW)

        assert candles == candles_snapshot
        assert swings == swings_snapshot

    def test_no_trade_decision_fields_exist(self):
        result = _make_calculator().detect_levels([], [], UTC_NOW)
        result_fields = set(type(result).model_fields.keys())
        forbidden = {
            "direction",
            "entry_price",
            "stop_loss",
            "take_profit",
            "confidence_score",
            "signal_type",
        }
        assert result_fields.isdisjoint(forbidden)


class TestDetectSweeps:
    def _build_swing_and_candles(self):
        swing_ts = UTC_NOW - timedelta(hours=2)
        swing = SwingPoint(
            swing_id="swing-high-0",
            symbol="BTC-USDT",
            timeframe="1h",
            timestamp=swing_ts,
            candle_index=3,
            swing_type=SwingType.HIGH,
            price=110.0,
            left_strength=3,
            right_strength=3,
            confirmed=True,
        )
        # A context candle before the sweep, priced below the level, so
        # detect_levels() has a current_price to select active levels with.
        context_candle = Candle(
            timestamp=swing_ts,
            open=105.0,
            high=106.0,
            low=104.0,
            close=105.0,
            volume=100.0,
            symbol="BTC-USDT",
            timeframe="1h",
        )
        sweep_candle = Candle(
            timestamp=swing_ts + timedelta(hours=1),
            open=109.0,
            high=112.0,
            low=108.0,
            close=109.0,
            volume=100.0,
            symbol="BTC-USDT",
            timeframe="1h",
        )
        return swing, [context_candle, sweep_candle]

    def test_sweep_detection_uses_active_levels(self):
        swing, candles = self._build_swing_and_candles()
        calculator = _make_calculator(min_swing_strength=3)
        detection = calculator.detect_levels(candles, [swing], UTC_NOW)

        sweeps = calculator.detect_sweeps(candles, detection)
        assert len(sweeps) == 1
        assert sweeps[0].confirmed is True

    def test_latest_sweep_retrieval(self):
        swing, candles = self._build_swing_and_candles()
        calculator = _make_calculator(min_swing_strength=3)
        detection = calculator.detect_levels(candles, [swing], UTC_NOW)

        latest = calculator.detect_latest_sweep(candles, detection)
        assert latest is not None
        assert latest.confirmed is True

    def test_deterministic_combined_ordering(self):
        previous_day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        candles = _hourly_candles_for_day(previous_day, 100.0)
        calculator = _make_calculator()
        result_one = calculator.detect_levels(candles, [], UTC_NOW)
        result_two = calculator.detect_levels(candles, [], UTC_NOW)
        assert [l.liquidity_id for l in result_one.active_levels] == [
            l.liquidity_id for l in result_two.active_levels
        ]
