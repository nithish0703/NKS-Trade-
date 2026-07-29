"""
Unit tests for app.market_structure.choch_detector.
"""

from datetime import datetime, timedelta, timezone

from app.liquidity.results import (
    LiquidityLevel,
    LiquiditySide,
    LiquiditySweepResult,
    LiquidityStrength,
    LiquidityType,
    SweepDirection,
)
from app.market_structure.choch_detector import CHOCHDetector
from app.market_structure.results import MarketStructureResult, SwingPoint, SwingType, TrendDirection
from app.market_structure.shift_results import BreakConfirmation, BreakDirection, DisplacementResult
from app.models.candle import Candle

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        symbol="BTC-USDT",
        timeframe="15m",
    )


def _swing(index: int, swing_type: SwingType, price: float) -> SwingPoint:
    candle_index = index + 3
    return SwingPoint(
        swing_id=f"swing-{swing_type.value}-{index}",
        symbol="BTC-USDT",
        timeframe="15m",
        timestamp=UTC_NOW + timedelta(minutes=index),
        candle_index=candle_index,
        swing_type=swing_type,
        price=price,
        left_strength=3,
        right_strength=3,
        confirmed=True,
    )


def _displacement(candle: Candle, index: int, direction: BreakDirection, confirmed=True) -> DisplacementResult:
    return DisplacementResult(
        symbol=candle.symbol,
        timeframe=candle.timeframe,
        candle_timestamp=candle.timestamp,
        candle_index=index,
        direction=direction,
        body_ratio=0.8,
        candle_range=candle.candle_range,
        body_size=candle.body_size,
        close_location_value=0.9 if direction == BreakDirection.BULLISH else 0.1,
        volume_confirmed=True,
        strong_close=True,
        confirmed=confirmed,
        reason="test",
    )


def _sweep(direction: SweepDirection, timestamp, price: float = 110.0) -> LiquiditySweepResult:
    level = LiquidityLevel(
        liquidity_id=f"level-{price}-{timestamp.isoformat()}",
        symbol="BTC-USDT",
        timeframe="15m",
        liquidity_type=(
            LiquidityType.EQUAL_HIGH if direction == SweepDirection.BEARISH else LiquidityType.EQUAL_LOW
        ),
        liquidity_side=(
            LiquiditySide.BUY_SIDE if direction == SweepDirection.BEARISH else LiquiditySide.SELL_SIDE
        ),
        price=price,
        start_timestamp=timestamp - timedelta(minutes=10),
        end_timestamp=timestamp - timedelta(minutes=10),
        source_timestamps=[timestamp - timedelta(minutes=10)],
        touch_count=2,
        strength=LiquidityStrength.STRONG,
        active=True,
    )
    return LiquiditySweepResult(
        sweep_id=f"sweep-{direction.value}-{timestamp.isoformat()}",
        symbol="BTC-USDT",
        timeframe="15m",
        direction=direction,
        liquidity_level=level,
        sweep_candle_timestamp=timestamp,
        sweep_candle_index=0,
        sweep_price=price + 1 if direction == SweepDirection.BEARISH else price - 1,
        close_price=price - 1 if direction == SweepDirection.BEARISH else price + 1,
        penetration_distance=1.0,
        penetration_ratio=0.01,
        reclaimed_level=True,
        confirmed=True,
        reason="test sweep",
    )


def _structure(trend: TrendDirection) -> MarketStructureResult:
    return MarketStructureResult(
        symbol="BTC-USDT",
        timeframe="15m",
        swings=[],
        classified_swings=[],
        latest_swing_high=None,
        latest_swing_low=None,
        trend_direction=trend,
        higher_high_count=0,
        higher_low_count=0,
        lower_high_count=0,
        lower_low_count=0,
        equal_high_count=0,
        equal_low_count=0,
    )


def _prefix_candles(count: int, descending: bool) -> list[Candle]:
    return [
        _candle(i, 100 - i if descending else 100 + i, 101 - i if descending else 101 + i,
                99 - i if descending else 99 + i, 100 - i if descending else 100 + i)
        for i in range(count)
    ]


class TestBullishCHOCH:
    def test_bullish_choch_from_bearish_structure(self):
        structure = _structure(TrendDirection.BEARISH)
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        break_candle = _candle(10, open_=104, high=112, low=103, close=110)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        candles = _prefix_candles(10, descending=True) + [break_candle]

        results = CHOCHDetector().detect(
            candles, structure, [lower_high], [displacement], [sweep]
        )
        assert len(results) == 1
        assert results[0].direction == BreakDirection.BULLISH
        assert results[0].confirmation == BreakConfirmation.CONFIRMED

    def test_bullish_choch_breaks_recent_lower_high(self):
        structure = _structure(TrendDirection.BEARISH)
        old_high = _swing(0, SwingType.HIGH, 130.0)  # not broken, too far
        recent_lower_high = _swing(1, SwingType.HIGH, 105.0)
        break_candle = _candle(10, open_=104, high=112, low=103, close=110)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        candles = _prefix_candles(10, descending=True) + [break_candle]

        results = CHOCHDetector().detect(
            candles, structure, [old_high, recent_lower_high], [displacement], [sweep]
        )
        assert len(results) == 1
        assert results[0].broken_swing.swing_id == recent_lower_high.swing_id

    def test_range_structure_does_not_produce_choch(self):
        structure = _structure(TrendDirection.RANGE)
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        break_candle = _candle(10, open_=104, high=112, low=103, close=110)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        candles = _prefix_candles(10, descending=True) + [break_candle]

        results = CHOCHDetector().detect(
            candles, structure, [lower_high], [displacement], [sweep]
        )
        assert results == []

    def test_unknown_structure_does_not_produce_choch(self):
        structure = _structure(TrendDirection.UNKNOWN)
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        break_candle = _candle(10, open_=104, high=112, low=103, close=110)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        candles = _prefix_candles(10, descending=True) + [break_candle]

        results = CHOCHDetector().detect(
            candles, structure, [lower_high], [displacement], [sweep]
        )
        assert results == []

    def test_no_sweep_means_no_choch(self):
        structure = _structure(TrendDirection.BEARISH)
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        break_candle = _candle(10, open_=104, high=112, low=103, close=110)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        candles = _prefix_candles(10, descending=True) + [break_candle]

        results = CHOCHDetector().detect(
            candles, structure, [lower_high], [displacement], []
        )
        assert results == []

    def test_sweep_after_break_invalid(self):
        structure = _structure(TrendDirection.BEARISH)
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        break_candle = _candle(10, open_=104, high=112, low=103, close=110)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        late_sweep = _sweep(SweepDirection.BULLISH, break_candle.timestamp + timedelta(minutes=5))
        candles = _prefix_candles(10, descending=True) + [break_candle]

        results = CHOCHDetector().detect(
            candles, structure, [lower_high], [displacement], [late_sweep]
        )
        assert results == []

    def test_wick_only_break_rejected(self):
        structure = _structure(TrendDirection.BEARISH)
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        # Wicks above 105 but closes back below.
        break_candle = _candle(10, open_=103, high=112, low=102, close=104)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        candles = _prefix_candles(10, descending=True) + [break_candle]

        results = CHOCHDetector().detect(
            candles, structure, [lower_high], [displacement], [sweep]
        )
        assert results == []

    def test_displacement_required(self):
        structure = _structure(TrendDirection.BEARISH)
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        break_candle = _candle(10, open_=104, high=112, low=103, close=110)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        candles = _prefix_candles(10, descending=True) + [break_candle]

        results = CHOCHDetector().detect(candles, structure, [lower_high], [], [sweep])
        assert results == []

    def test_duplicate_prevention(self):
        structure = _structure(TrendDirection.BEARISH)
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        break_candle = _candle(10, open_=104, high=112, low=103, close=110)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        sweep_one = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=1))
        sweep_two = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=2))
        candles = _prefix_candles(10, descending=True) + [break_candle]

        results = CHOCHDetector().detect(
            candles, structure, [lower_high], [displacement], [sweep_one, sweep_two]
        )
        assert len(results) == 1

    def test_deterministic_ids(self):
        structure = _structure(TrendDirection.BEARISH)
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        break_candle = _candle(10, open_=104, high=112, low=103, close=110)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        candles = _prefix_candles(10, descending=True) + [break_candle]

        results_one = CHOCHDetector().detect(
            candles, structure, [lower_high], [displacement], [sweep]
        )
        results_two = CHOCHDetector().detect(
            candles, structure, [lower_high], [displacement], [sweep]
        )
        assert results_one[0].break_id == results_two[0].break_id


class TestBearishCHOCH:
    def test_bearish_choch_from_bullish_structure(self):
        structure = _structure(TrendDirection.BULLISH)
        higher_low = _swing(0, SwingType.LOW, 95.0)
        break_candle = _candle(10, open_=96, high=97, low=88, close=90)
        displacement = _displacement(break_candle, 10, BreakDirection.BEARISH)
        sweep = _sweep(SweepDirection.BEARISH, UTC_NOW + timedelta(minutes=5))
        candles = _prefix_candles(10, descending=False) + [break_candle]

        results = CHOCHDetector().detect(
            candles, structure, [higher_low], [displacement], [sweep]
        )
        assert len(results) == 1
        assert results[0].direction == BreakDirection.BEARISH

    def test_bearish_choch_breaks_recent_higher_low(self):
        structure = _structure(TrendDirection.BULLISH)
        old_low = _swing(0, SwingType.LOW, 70.0)  # too far to be broken
        recent_higher_low = _swing(1, SwingType.LOW, 95.0)
        break_candle = _candle(10, open_=96, high=97, low=88, close=90)
        displacement = _displacement(break_candle, 10, BreakDirection.BEARISH)
        sweep = _sweep(SweepDirection.BEARISH, UTC_NOW + timedelta(minutes=5))
        candles = _prefix_candles(10, descending=False) + [break_candle]

        results = CHOCHDetector().detect(
            candles, structure, [old_low, recent_higher_low], [displacement], [sweep]
        )
        assert len(results) == 1
        assert results[0].broken_swing.swing_id == recent_higher_low.swing_id
