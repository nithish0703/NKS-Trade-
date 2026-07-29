"""
Unit tests for app.market_structure.bos_detector.
"""

from datetime import datetime, timedelta, timezone

from app.liquidity.results import LiquidityLevel, LiquiditySide, LiquidityStrength, LiquidityType, SweepDirection, LiquiditySweepResult
from app.market_structure.bos_detector import BOSDetector
from app.market_structure.results import SwingPoint, SwingType
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


def _displacement(
    candle: Candle, index: int, direction: BreakDirection, confirmed: bool = True
) -> DisplacementResult:
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


def _sweep(
    direction: SweepDirection, timestamp, price: float = 110.0, symbol="BTC-USDT", timeframe="15m"
) -> LiquiditySweepResult:
    level = LiquidityLevel(
        liquidity_id=f"level-{price}",
        symbol=symbol,
        timeframe=timeframe,
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
        symbol=symbol,
        timeframe=timeframe,
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


class TestBullishBOS:
    def test_bullish_bos_above_prior_swing_high(self):
        swing = _swing(0, SwingType.HIGH, 110.0)
        break_candle = _candle(10, open_=109, high=115, low=108, close=113)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))

        candles = [swing, break_candle]  # placeholder unused; real candles built below
        all_candles = [
            _candle(i, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(10)
        ] + [break_candle]

        results = BOSDetector().detect(all_candles, [swing], [displacement], [sweep])
        assert len(results) == 1
        assert results[0].direction == BreakDirection.BULLISH
        assert results[0].confirmation == BreakConfirmation.CONFIRMED

    def test_wick_only_bullish_break_rejected(self):
        swing = _swing(0, SwingType.HIGH, 110.0)
        # High wicks above 110 but closes back below it.
        break_candle = _candle(10, open_=108, high=115, low=107, close=109)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        all_candles = [
            _candle(i, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(10)
        ] + [break_candle]

        results = BOSDetector().detect(all_candles, [swing], [displacement], [sweep])
        assert results == []  # close did not exceed swing price -> no broken swing found

    def test_no_displacement_means_no_bos(self):
        swing = _swing(0, SwingType.HIGH, 110.0)
        break_candle = _candle(10, open_=109, high=115, low=108, close=113)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        all_candles = [
            _candle(i, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(10)
        ] + [break_candle]

        results = BOSDetector().detect(all_candles, [swing], [], [sweep])
        assert results == []

    def test_unconfirmed_displacement_means_no_bos(self):
        swing = _swing(0, SwingType.HIGH, 110.0)
        break_candle = _candle(10, open_=109, high=115, low=108, close=113)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH, confirmed=False)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        all_candles = [
            _candle(i, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(10)
        ] + [break_candle]

        results = BOSDetector().detect(all_candles, [swing], [displacement], [sweep])
        assert results == []

    def test_no_prior_sweep_means_no_confirmed_bos(self):
        swing = _swing(0, SwingType.HIGH, 110.0)
        break_candle = _candle(10, open_=109, high=115, low=108, close=113)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        all_candles = [
            _candle(i, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(10)
        ] + [break_candle]

        results = BOSDetector().detect(all_candles, [swing], [displacement], [])
        assert results == []

    def test_sweep_after_break_cannot_support_bos(self):
        swing = _swing(0, SwingType.HIGH, 110.0)
        break_candle = _candle(10, open_=109, high=115, low=108, close=113)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        late_sweep = _sweep(SweepDirection.BULLISH, break_candle.timestamp + timedelta(minutes=5))
        all_candles = [
            _candle(i, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(10)
        ] + [break_candle]

        results = BOSDetector().detect(all_candles, [swing], [displacement], [late_sweep])
        assert results == []

    def test_correct_sweep_direction_required(self):
        swing = _swing(0, SwingType.HIGH, 110.0)
        break_candle = _candle(10, open_=109, high=115, low=108, close=113)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        wrong_direction_sweep = _sweep(SweepDirection.BEARISH, UTC_NOW + timedelta(minutes=5))
        all_candles = [
            _candle(i, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(10)
        ] + [break_candle]

        results = BOSDetector().detect(
            all_candles, [swing], [displacement], [wrong_direction_sweep]
        )
        assert results == []

    def test_latest_prior_valid_sweep_used(self):
        swing = _swing(0, SwingType.HIGH, 110.0)
        break_candle = _candle(10, open_=109, high=115, low=108, close=113)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        early_sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=1))
        late_sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=8))
        all_candles = [
            _candle(i, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(10)
        ] + [break_candle]

        results = BOSDetector().detect(
            all_candles, [swing], [displacement], [early_sweep, late_sweep]
        )
        assert len(results) == 1
        assert results[0].preceding_liquidity_sweep.sweep_id == late_sweep.sweep_id

    def test_deterministic_break_id(self):
        swing = _swing(0, SwingType.HIGH, 110.0)
        break_candle = _candle(10, open_=109, high=115, low=108, close=113)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        all_candles = [
            _candle(i, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(10)
        ] + [break_candle]

        results_one = BOSDetector().detect(all_candles, [swing], [displacement], [sweep])
        results_two = BOSDetector().detect(all_candles, [swing], [displacement], [sweep])
        assert results_one[0].break_id == results_two[0].break_id

    def test_duplicate_prevention(self):
        swing = _swing(0, SwingType.HIGH, 110.0)
        break_candle = _candle(10, open_=109, high=115, low=108, close=113)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        sweep_one = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=1))
        sweep_two = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=2))
        all_candles = [
            _candle(i, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(10)
        ] + [break_candle]

        results = BOSDetector().detect(
            all_candles, [swing], [displacement], [sweep_one, sweep_two]
        )
        assert len(results) == 1

    def test_chronological_ordering(self):
        swing_a = _swing(0, SwingType.HIGH, 110.0)
        swing_b = _swing(1, SwingType.HIGH, 120.0)
        break_candle_a = _candle(10, open_=109, high=115, low=108, close=113)
        break_candle_b = _candle(11, open_=118, high=125, low=117, close=123)
        displacement_a = _displacement(break_candle_a, 10, BreakDirection.BULLISH)
        displacement_b = _displacement(break_candle_b, 11, BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        all_candles = [
            _candle(i, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(10)
        ] + [break_candle_a, break_candle_b]

        results = BOSDetector().detect(
            all_candles, [swing_a, swing_b], [displacement_a, displacement_b], [sweep]
        )
        timestamps = [r.break_candle_timestamp for r in results]
        assert timestamps == sorted(timestamps)

    def test_input_data_not_mutated(self):
        swing = _swing(0, SwingType.HIGH, 110.0)
        break_candle = _candle(10, open_=109, high=115, low=108, close=113)
        displacement = _displacement(break_candle, 10, BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        all_candles = [
            _candle(i, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(10)
        ] + [break_candle]
        candles_snapshot = [c.model_copy() for c in all_candles]
        swing_snapshot = swing.model_copy()

        BOSDetector().detect(all_candles, [swing], [displacement], [sweep])

        assert all_candles == candles_snapshot
        assert swing == swing_snapshot


class TestBearishBOS:
    def test_bearish_bos_below_prior_swing_low(self):
        swing = _swing(0, SwingType.LOW, 90.0)
        break_candle = _candle(10, open_=91, high=92, low=85, close=87)
        displacement = _displacement(break_candle, 10, BreakDirection.BEARISH)
        sweep = _sweep(SweepDirection.BEARISH, UTC_NOW + timedelta(minutes=5))
        all_candles = [
            _candle(i, 100 - i, 101 - i, 99 - i, 100 - i) for i in range(10)
        ] + [break_candle]

        results = BOSDetector().detect(all_candles, [swing], [displacement], [sweep])
        assert len(results) == 1
        assert results[0].direction == BreakDirection.BEARISH

    def test_wick_only_bearish_break_rejected(self):
        swing = _swing(0, SwingType.LOW, 90.0)
        break_candle = _candle(10, open_=92, high=93, low=85, close=91)
        displacement = _displacement(break_candle, 10, BreakDirection.BEARISH)
        sweep = _sweep(SweepDirection.BEARISH, UTC_NOW + timedelta(minutes=5))
        all_candles = [
            _candle(i, 100 - i, 101 - i, 99 - i, 100 - i) for i in range(10)
        ] + [break_candle]

        results = BOSDetector().detect(all_candles, [swing], [displacement], [sweep])
        assert results == []
