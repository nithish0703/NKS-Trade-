"""
Unit tests for app.zones.order_block.
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
from app.market_structure.results import SwingPoint, SwingType
from app.market_structure.shift_results import (
    BreakConfirmation,
    BreakDirection,
    DisplacementResult,
    StructureBreakResult,
    StructureBreakType,
)
from app.models.candle import Candle
from app.models.trade_zone import ZoneStatus, ZoneType
from app.zones.order_block import OrderBlockDetector

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


def _swing(price: float, swing_type=SwingType.HIGH) -> SwingPoint:
    return SwingPoint(
        swing_id=f"swing-{swing_type.value}-{price}",
        symbol="BTC-USDT",
        timeframe="15m",
        timestamp=UTC_NOW,
        candle_index=3,
        swing_type=swing_type,
        price=price,
        left_strength=3,
        right_strength=3,
        confirmed=True,
    )


def _sweep(direction: SweepDirection, timestamp, price=110.0) -> LiquiditySweepResult:
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
        start_timestamp=timestamp - timedelta(minutes=20),
        end_timestamp=timestamp - timedelta(minutes=20),
        source_timestamps=[timestamp - timedelta(minutes=20)],
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


def _displacement(index: int, timestamp, direction: BreakDirection, confirmed=True) -> DisplacementResult:
    return DisplacementResult(
        symbol="BTC-USDT",
        timeframe="15m",
        candle_timestamp=timestamp,
        candle_index=index,
        direction=direction,
        body_ratio=0.8,
        candle_range=10.0,
        body_size=8.0,
        close_location_value=0.9 if direction == BreakDirection.BULLISH else 0.1,
        volume_confirmed=True,
        strong_close=True,
        confirmed=confirmed,
        reason="test",
    )


def _break(
    direction: BreakDirection,
    break_candle: Candle,
    break_index: int,
    sweep,
    displacement,
    wick_only=False,
    confirmation=BreakConfirmation.CONFIRMED,
    break_type=StructureBreakType.MSS,
) -> StructureBreakResult:
    broken_swing = _swing(105.0, SwingType.HIGH if direction == BreakDirection.BULLISH else SwingType.LOW)
    return StructureBreakResult(
        break_id=f"break-{direction.value}-{break_index}",
        symbol="BTC-USDT",
        timeframe="15m",
        break_type=break_type,
        direction=direction,
        broken_swing=broken_swing,
        break_candle_timestamp=break_candle.timestamp,
        break_candle_index=break_index,
        break_price=105.0,
        close_price=break_candle.close,
        displacement=displacement,
        preceding_liquidity_sweep=sweep,
        strong_close_beyond_structure=not wick_only,
        wick_only_break=wick_only,
        confirmation=confirmation,
        reason="test break",
    )


class TestBullishOrderBlock:
    def test_bullish_order_block_from_last_bearish_candle(self):
        sweep_ts = UTC_NOW + timedelta(minutes=2)
        sweep = _sweep(SweepDirection.BULLISH, sweep_ts)

        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 101, 99, 100),
            _candle(2, 100, 101, 99, 100),
            _candle(3, 100, 100, 96, 97),  # last bearish candle (source)
            _candle(4, 97, 112, 96, 110),  # bullish displacement / break candle
        ]
        displacement = _displacement(4, candles[4].timestamp, BreakDirection.BULLISH)
        structure_break = _break(BreakDirection.BULLISH, candles[4], 4, sweep, displacement)

        zones = OrderBlockDetector().detect(candles, [structure_break])
        assert len(zones) == 1
        assert zones[0].zone_type == ZoneType.ORDER_BLOCK
        assert zones[0].direction == "BUY"
        assert zones[0].status == ZoneStatus.FRESH
        assert zones[0].lower_price == candles[3].low
        assert zones[0].upper_price == candles[3].open

    def test_source_candle_occurs_after_sweep(self):
        sweep_ts = UTC_NOW + timedelta(minutes=2, seconds=30)
        sweep = _sweep(SweepDirection.BULLISH, sweep_ts)

        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 100, 96, 97),  # bearish, BEFORE sweep -> ineligible
            _candle(2, 97, 101, 96, 100),
            _candle(3, 100, 100, 98, 99),  # bearish, AFTER sweep -> should be selected
            _candle(4, 99, 112, 98, 110),
        ]
        displacement = _displacement(4, candles[4].timestamp, BreakDirection.BULLISH)
        structure_break = _break(BreakDirection.BULLISH, candles[4], 4, sweep, displacement)

        zones = OrderBlockDetector().detect(candles, [structure_break])
        assert len(zones) == 1
        assert zones[0].source_candle_timestamp == candles[3].timestamp


class TestBearishOrderBlock:
    def test_bearish_order_block_from_last_bullish_candle(self):
        sweep_ts = UTC_NOW + timedelta(minutes=2)
        sweep = _sweep(SweepDirection.BEARISH, sweep_ts)

        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 101, 99, 100),
            _candle(2, 100, 101, 99, 100),
            _candle(3, 100, 104, 100, 103),  # last bullish candle (source)
            _candle(4, 103, 104, 88, 90),  # bearish displacement / break candle
        ]
        displacement = _displacement(4, candles[4].timestamp, BreakDirection.BEARISH)
        structure_break = _break(BreakDirection.BEARISH, candles[4], 4, sweep, displacement)

        zones = OrderBlockDetector().detect(candles, [structure_break])
        assert len(zones) == 1
        assert zones[0].direction == "SELL"
        assert zones[0].lower_price == candles[3].open
        assert zones[0].upper_price == candles[3].high


class TestOrderBlockGuardConditions:
    def test_break_without_sweep_creates_no_order_block(self):
        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 100, 96, 97),
            _candle(2, 97, 112, 96, 110),
        ]
        displacement = _displacement(2, candles[2].timestamp, BreakDirection.BULLISH)
        structure_break = _break(
            BreakDirection.BULLISH, candles[2], 2, None, displacement, confirmation=BreakConfirmation.REJECTED
        )
        zones = OrderBlockDetector().detect(candles, [structure_break])
        assert zones == []

    def test_unconfirmed_break_creates_no_order_block(self):
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=1))
        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 100, 96, 97),
            _candle(2, 97, 112, 96, 110),
        ]
        displacement = _displacement(2, candles[2].timestamp, BreakDirection.BULLISH)
        structure_break = _break(
            BreakDirection.BULLISH, candles[2], 2, sweep, displacement, confirmation=BreakConfirmation.REJECTED
        )
        zones = OrderBlockDetector().detect(candles, [structure_break])
        assert zones == []

    def test_wick_only_break_creates_no_order_block(self):
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=1))
        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 100, 96, 97),
            _candle(2, 97, 112, 96, 98),
        ]
        displacement = _displacement(2, candles[2].timestamp, BreakDirection.BULLISH)
        structure_break = _break(
            BreakDirection.BULLISH,
            candles[2],
            2,
            sweep,
            displacement,
            wick_only=True,
            confirmation=BreakConfirmation.REJECTED,
        )
        zones = OrderBlockDetector().detect(candles, [structure_break])
        assert zones == []

    def test_deterministic_zone_id(self):
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(seconds=30))
        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 100, 96, 97),
            _candle(2, 97, 112, 96, 110),
        ]
        displacement = _displacement(2, candles[2].timestamp, BreakDirection.BULLISH)
        structure_break = _break(BreakDirection.BULLISH, candles[2], 2, sweep, displacement)

        zones_one = OrderBlockDetector().detect(candles, [structure_break])
        zones_two = OrderBlockDetector().detect(candles, [structure_break])
        assert zones_one[0].zone_id == zones_two[0].zone_id

    def test_duplicate_prevention(self):
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(seconds=30))
        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 100, 96, 97),
            _candle(2, 97, 112, 96, 110),
        ]
        displacement = _displacement(2, candles[2].timestamp, BreakDirection.BULLISH)
        structure_break = _break(BreakDirection.BULLISH, candles[2], 2, sweep, displacement)

        zones = OrderBlockDetector().detect(candles, [structure_break, structure_break])
        assert len(zones) == 1

    def test_chronological_output(self):
        sweep_a = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=1))
        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 100, 96, 97),
            _candle(2, 97, 112, 96, 110),
            _candle(3, 110, 111, 109, 110),
            _candle(4, 110, 110, 105, 106),
            _candle(5, 106, 120, 105, 118),
        ]
        displacement_a = _displacement(2, candles[2].timestamp, BreakDirection.BULLISH)
        break_a = _break(BreakDirection.BULLISH, candles[2], 2, sweep_a, displacement_a)

        sweep_b = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=3, seconds=30))
        displacement_b = _displacement(5, candles[5].timestamp, BreakDirection.BULLISH)
        break_b = _break(BreakDirection.BULLISH, candles[5], 5, sweep_b, displacement_b)

        zones = OrderBlockDetector().detect(candles, [break_b, break_a])
        timestamps = [z.created_at for z in zones]
        assert timestamps == sorted(timestamps)

    def test_no_future_candle_used_to_select_block(self):
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(seconds=30))
        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 100, 96, 97),  # last bearish before break
            _candle(2, 97, 112, 96, 110),  # break candle
            _candle(3, 110, 130, 109, 129),  # bearish-looking candle AFTER break must be ignored
        ]
        displacement = _displacement(2, candles[2].timestamp, BreakDirection.BULLISH)
        structure_break = _break(BreakDirection.BULLISH, candles[2], 2, sweep, displacement)

        zones = OrderBlockDetector().detect(candles, [structure_break])
        assert len(zones) == 1
        assert zones[0].source_candle_timestamp == candles[1].timestamp

    def test_input_data_not_mutated(self):
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=1))
        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 100, 96, 97),
            _candle(2, 97, 112, 96, 110),
        ]
        displacement = _displacement(2, candles[2].timestamp, BreakDirection.BULLISH)
        structure_break = _break(BreakDirection.BULLISH, candles[2], 2, sweep, displacement)
        candles_snapshot = [c.model_copy() for c in candles]
        break_snapshot = structure_break.model_copy()

        OrderBlockDetector().detect(candles, [structure_break])

        assert candles == candles_snapshot
        assert structure_break == break_snapshot
