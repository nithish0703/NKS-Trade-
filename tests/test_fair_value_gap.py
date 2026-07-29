"""
Unit tests for app.zones.fair_value_gap.
"""

from datetime import datetime, timedelta, timezone

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
from app.zones.fair_value_gap import FairValueGapDetector

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


def _swing(price: float) -> SwingPoint:
    return SwingPoint(
        swing_id=f"swing-{price}",
        symbol="BTC-USDT",
        timeframe="15m",
        timestamp=UTC_NOW,
        candle_index=3,
        swing_type=SwingType.HIGH,
        price=price,
        left_strength=3,
        right_strength=3,
        confirmed=True,
    )


def _displacement(index: int, timestamp, direction: BreakDirection) -> DisplacementResult:
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
        confirmed=True,
        reason="test",
    )


def _break(direction: BreakDirection, displacement: DisplacementResult, break_id="break-1") -> StructureBreakResult:
    from app.liquidity.results import (
        LiquidityLevel,
        LiquiditySide,
        LiquiditySweepResult,
        LiquidityStrength,
        LiquidityType,
        SweepDirection,
    )

    sweep_direction = SweepDirection.BULLISH if direction == BreakDirection.BULLISH else SweepDirection.BEARISH
    level = LiquidityLevel(
        liquidity_id="level-1",
        symbol="BTC-USDT",
        timeframe="15m",
        liquidity_type=(
            LiquidityType.EQUAL_LOW if sweep_direction == SweepDirection.BULLISH else LiquidityType.EQUAL_HIGH
        ),
        liquidity_side=(
            LiquiditySide.SELL_SIDE if sweep_direction == SweepDirection.BULLISH else LiquiditySide.BUY_SIDE
        ),
        price=90.0,
        start_timestamp=UTC_NOW - timedelta(minutes=20),
        end_timestamp=UTC_NOW - timedelta(minutes=20),
        source_timestamps=[UTC_NOW - timedelta(minutes=20)],
        touch_count=2,
        strength=LiquidityStrength.STRONG,
        active=True,
    )
    sweep = LiquiditySweepResult(
        sweep_id="sweep-1",
        symbol="BTC-USDT",
        timeframe="15m",
        direction=sweep_direction,
        liquidity_level=level,
        sweep_candle_timestamp=UTC_NOW - timedelta(minutes=10),
        sweep_candle_index=0,
        sweep_price=89.0,
        close_price=91.0,
        penetration_distance=1.0,
        penetration_ratio=0.01,
        reclaimed_level=True,
        confirmed=True,
        reason="test sweep",
    )
    return StructureBreakResult(
        break_id=break_id,
        symbol="BTC-USDT",
        timeframe="15m",
        break_type=StructureBreakType.MSS,
        direction=direction,
        broken_swing=_swing(105.0),
        break_candle_timestamp=displacement.candle_timestamp,
        break_candle_index=displacement.candle_index,
        break_price=105.0,
        close_price=110.0 if direction == BreakDirection.BULLISH else 90.0,
        displacement=displacement,
        preceding_liquidity_sweep=sweep,
        strong_close_beyond_structure=True,
        wick_only_break=False,
        confirmation=BreakConfirmation.CONFIRMED,
        reason="test break",
    )


class TestBullishFVG:
    def test_bullish_three_candle_fvg(self):
        candles = [
            _candle(0, 100, 101, 99, 100),  # candle1: high=101
            _candle(1, 100, 108, 99, 107),  # middle bullish displacement
            _candle(2, 107, 112, 105, 110),  # candle3: low=105 > candle1 high 101
        ]
        displacement = _displacement(1, candles[1].timestamp, BreakDirection.BULLISH)
        structure_break = _break(BreakDirection.BULLISH, displacement)

        zones = FairValueGapDetector().detect(candles, [structure_break])
        assert len(zones) == 1
        assert zones[0].zone_type == ZoneType.FAIR_VALUE_GAP
        assert zones[0].direction == "BUY"
        assert zones[0].status == ZoneStatus.FRESH

    def test_bullish_boundaries_correct(self):
        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 108, 99, 107),
            _candle(2, 107, 112, 105, 110),
        ]
        displacement = _displacement(1, candles[1].timestamp, BreakDirection.BULLISH)
        structure_break = _break(BreakDirection.BULLISH, displacement)

        zones = FairValueGapDetector().detect(candles, [structure_break])
        assert zones[0].lower_price == candles[0].high
        assert zones[0].upper_price == candles[2].low


class TestBearishFVG:
    def test_bearish_three_candle_fvg(self):
        candles = [
            _candle(0, 100, 101, 99, 100),  # candle1: low=99
            _candle(1, 100, 101, 92, 93),  # middle bearish displacement
            _candle(2, 93, 95, 88, 90),  # candle3: high=95 < candle1 low 99
        ]
        displacement = _displacement(1, candles[1].timestamp, BreakDirection.BEARISH)
        structure_break = _break(BreakDirection.BEARISH, displacement)

        zones = FairValueGapDetector().detect(candles, [structure_break])
        assert len(zones) == 1
        assert zones[0].direction == "SELL"

    def test_bearish_boundaries_correct(self):
        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 101, 92, 93),
            _candle(2, 93, 95, 88, 90),
        ]
        displacement = _displacement(1, candles[1].timestamp, BreakDirection.BEARISH)
        structure_break = _break(BreakDirection.BEARISH, displacement)

        zones = FairValueGapDetector().detect(candles, [structure_break])
        assert zones[0].lower_price == candles[2].high
        assert zones[0].upper_price == candles[0].low


class TestFVGGuardConditions:
    def test_overlapping_candles_produce_no_fvg(self):
        candles = [
            _candle(0, 100, 105, 99, 100),
            _candle(1, 100, 108, 99, 107),
            _candle(2, 107, 112, 102, 110),  # low=102 overlaps candle1 high=105
        ]
        displacement = _displacement(1, candles[1].timestamp, BreakDirection.BULLISH)
        structure_break = _break(BreakDirection.BULLISH, displacement)

        zones = FairValueGapDetector().detect(candles, [structure_break])
        assert zones == []

    def test_zero_size_gap_excluded(self):
        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 108, 99, 107),
            _candle(2, 107, 112, 101, 110),  # low == candle1 high, gap is zero
        ]
        displacement = _displacement(1, candles[1].timestamp, BreakDirection.BULLISH)
        structure_break = _break(BreakDirection.BULLISH, displacement)

        zones = FairValueGapDetector().detect(candles, [structure_break])
        assert zones == []

    def test_middle_displacement_requirement(self):
        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 108, 99, 107),
            _candle(2, 107, 112, 105, 110),
        ]
        # No structure break references the middle candle as displacement.
        zones = FairValueGapDetector().detect(candles, [])
        assert zones == []

    def test_deterministic_id(self):
        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 108, 99, 107),
            _candle(2, 107, 112, 105, 110),
        ]
        displacement = _displacement(1, candles[1].timestamp, BreakDirection.BULLISH)
        structure_break = _break(BreakDirection.BULLISH, displacement)

        zones_one = FairValueGapDetector().detect(candles, [structure_break])
        zones_two = FairValueGapDetector().detect(candles, [structure_break])
        assert zones_one[0].zone_id == zones_two[0].zone_id

    def test_related_break_id_retained(self):
        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 108, 99, 107),
            _candle(2, 107, 112, 105, 110),
        ]
        displacement = _displacement(1, candles[1].timestamp, BreakDirection.BULLISH)
        structure_break = _break(BreakDirection.BULLISH, displacement, break_id="break-xyz")

        zones = FairValueGapDetector().detect(candles, [structure_break])
        assert zones[0].originating_break_id == "break-xyz"

    def test_chronological_ordering(self):
        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 108, 99, 107),
            _candle(2, 107, 112, 105, 110),
            _candle(3, 110, 111, 109, 110),
            _candle(4, 110, 118, 109, 117),
            _candle(5, 117, 122, 115, 120),
        ]
        displacement_a = _displacement(1, candles[1].timestamp, BreakDirection.BULLISH)
        break_a = _break(BreakDirection.BULLISH, displacement_a, break_id="break-a")
        displacement_b = _displacement(4, candles[4].timestamp, BreakDirection.BULLISH)
        break_b = _break(BreakDirection.BULLISH, displacement_b, break_id="break-b")

        zones = FairValueGapDetector().detect(candles, [break_b, break_a])
        timestamps = [z.created_at for z in zones]
        assert timestamps == sorted(timestamps)

    def test_no_future_candle_beyond_third_used(self):
        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 108, 99, 107),
            _candle(2, 107, 112, 105, 110),
            _candle(3, 110, 200, 105, 190),  # extreme future candle must not affect the gap
        ]
        displacement = _displacement(1, candles[1].timestamp, BreakDirection.BULLISH)
        structure_break = _break(BreakDirection.BULLISH, displacement)

        zones = FairValueGapDetector().detect(candles, [structure_break])
        assert len(zones) == 1
        assert zones[0].upper_price == candles[2].low

    def test_input_data_not_mutated(self):
        candles = [
            _candle(0, 100, 101, 99, 100),
            _candle(1, 100, 108, 99, 107),
            _candle(2, 107, 112, 105, 110),
        ]
        displacement = _displacement(1, candles[1].timestamp, BreakDirection.BULLISH)
        structure_break = _break(BreakDirection.BULLISH, displacement)
        candles_snapshot = [c.model_copy() for c in candles]
        break_snapshot = structure_break.model_copy()

        FairValueGapDetector().detect(candles, [structure_break])

        assert candles == candles_snapshot
        assert structure_break == break_snapshot
