"""
Unit tests for app.zones.breaker_block.
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
from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType
from app.zones.breaker_block import BreakerBlockDetector

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


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


def _sweep(direction: SweepDirection, timestamp) -> LiquiditySweepResult:
    level = LiquidityLevel(
        liquidity_id="level-1",
        symbol="BTC-USDT",
        timeframe="15m",
        liquidity_type=(
            LiquidityType.EQUAL_LOW if direction == SweepDirection.BULLISH else LiquidityType.EQUAL_HIGH
        ),
        liquidity_side=(
            LiquiditySide.SELL_SIDE if direction == SweepDirection.BULLISH else LiquiditySide.BUY_SIDE
        ),
        price=90.0,
        start_timestamp=timestamp - timedelta(minutes=20),
        end_timestamp=timestamp - timedelta(minutes=20),
        source_timestamps=[timestamp - timedelta(minutes=20)],
        touch_count=2,
        strength=LiquidityStrength.STRONG,
        active=True,
    )
    return LiquiditySweepResult(
        sweep_id="sweep-1",
        symbol="BTC-USDT",
        timeframe="15m",
        direction=direction,
        liquidity_level=level,
        sweep_candle_timestamp=timestamp,
        sweep_candle_index=0,
        sweep_price=89.0,
        close_price=91.0,
        penetration_distance=1.0,
        penetration_ratio=0.01,
        reclaimed_level=True,
        confirmed=True,
        reason="test sweep",
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


def _order_block(direction: str, lower: float, upper: float, created_at) -> TradeZone:
    return TradeZone(
        zone_id=f"ob-{direction}-{lower}-{upper}",
        symbol="BTC-USDT",
        timeframe="15m",
        zone_type=ZoneType.ORDER_BLOCK,
        direction=direction,
        lower_price=lower,
        upper_price=upper,
        created_at=created_at,
        source_candle_timestamp=created_at,
        source_candle_index=0,
        status=ZoneStatus.FRESH,
        touch_count=0,
    )


def _break(
    direction: BreakDirection,
    timestamp,
    close_price: float,
    index: int,
    wick_only=False,
    confirmation=BreakConfirmation.CONFIRMED,
    break_id="break-1",
) -> StructureBreakResult:
    sweep_direction = SweepDirection.BULLISH if direction == BreakDirection.BULLISH else SweepDirection.BEARISH
    sweep = _sweep(sweep_direction, timestamp - timedelta(minutes=1))
    displacement = _displacement(index, timestamp, direction)
    return StructureBreakResult(
        break_id=break_id,
        symbol="BTC-USDT",
        timeframe="15m",
        break_type=StructureBreakType.MSS,
        direction=direction,
        broken_swing=_swing(105.0),
        break_candle_timestamp=timestamp,
        break_candle_index=index,
        break_price=105.0,
        close_price=close_price,
        displacement=displacement,
        preceding_liquidity_sweep=sweep,
        strong_close_beyond_structure=not wick_only,
        wick_only_break=wick_only,
        confirmation=confirmation,
        reason="test break",
    )


class TestBullishBreaker:
    def test_bearish_order_block_invalidated_into_bullish_breaker(self):
        order_block = _order_block("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        confirming_break = _break(
            BreakDirection.BULLISH, UTC_NOW + timedelta(minutes=5), close_price=110.0, index=5
        )

        zones = BreakerBlockDetector().detect([], [order_block], [confirming_break])
        assert len(zones) == 1
        assert zones[0].zone_type == ZoneType.BREAKER_BLOCK
        assert zones[0].direction == "BUY"
        assert zones[0].lower_price == 100.0
        assert zones[0].upper_price == 105.0
        assert zones[0].status == ZoneStatus.FRESH

    def test_wick_only_invalidation_creates_no_breaker(self):
        order_block = _order_block("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        confirming_break = _break(
            BreakDirection.BULLISH,
            UTC_NOW + timedelta(minutes=5),
            close_price=110.0,
            index=5,
            wick_only=True,
            confirmation=BreakConfirmation.REJECTED,
        )

        zones = BreakerBlockDetector().detect([], [order_block], [confirming_break])
        assert zones == []

    def test_confirmed_close_beyond_boundary_required(self):
        order_block = _order_block("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        # Close does not exceed the order block's upper boundary.
        confirming_break = _break(
            BreakDirection.BULLISH, UTC_NOW + timedelta(minutes=5), close_price=104.0, index=5
        )

        zones = BreakerBlockDetector().detect([], [order_block], [confirming_break])
        assert zones == []

    def test_original_block_must_precede_confirmation(self):
        order_block = _order_block("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        # Break occurs before the order block was created.
        confirming_break = _break(
            BreakDirection.BULLISH, UTC_NOW - timedelta(minutes=5), close_price=110.0, index=0
        )

        zones = BreakerBlockDetector().detect([], [order_block], [confirming_break])
        assert zones == []

    def test_original_order_block_id_retained(self):
        order_block = _order_block("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        confirming_break = _break(
            BreakDirection.BULLISH, UTC_NOW + timedelta(minutes=5), close_price=110.0, index=5
        )

        zones = BreakerBlockDetector().detect([], [order_block], [confirming_break])
        assert zones[0].metadata["original_order_block_id"] == order_block.zone_id

    def test_confirming_break_id_retained(self):
        order_block = _order_block("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        confirming_break = _break(
            BreakDirection.BULLISH,
            UTC_NOW + timedelta(minutes=5),
            close_price=110.0,
            index=5,
            break_id="break-xyz",
        )

        zones = BreakerBlockDetector().detect([], [order_block], [confirming_break])
        assert zones[0].originating_break_id == "break-xyz"


class TestBearishBreaker:
    def test_bullish_order_block_invalidated_into_bearish_breaker(self):
        order_block = _order_block("BUY", lower=100.0, upper=105.0, created_at=UTC_NOW)
        confirming_break = _break(
            BreakDirection.BEARISH, UTC_NOW + timedelta(minutes=5), close_price=95.0, index=5
        )

        zones = BreakerBlockDetector().detect([], [order_block], [confirming_break])
        assert len(zones) == 1
        assert zones[0].direction == "SELL"
        assert zones[0].lower_price == 100.0
        assert zones[0].upper_price == 105.0


class TestBreakerGuardConditions:
    def test_structure_break_required(self):
        order_block = _order_block("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        zones = BreakerBlockDetector().detect([], [order_block], [])
        assert zones == []

    def test_deterministic_id(self):
        order_block = _order_block("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        confirming_break = _break(
            BreakDirection.BULLISH, UTC_NOW + timedelta(minutes=5), close_price=110.0, index=5
        )

        zones_one = BreakerBlockDetector().detect([], [order_block], [confirming_break])
        zones_two = BreakerBlockDetector().detect([], [order_block], [confirming_break])
        assert zones_one[0].zone_id == zones_two[0].zone_id

    def test_no_duplicates(self):
        order_block = _order_block("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        confirming_break = _break(
            BreakDirection.BULLISH, UTC_NOW + timedelta(minutes=5), close_price=110.0, index=5
        )

        zones = BreakerBlockDetector().detect(
            [], [order_block, order_block], [confirming_break, confirming_break]
        )
        assert len(zones) == 1

    def test_input_data_not_mutated(self):
        order_block = _order_block("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        confirming_break = _break(
            BreakDirection.BULLISH, UTC_NOW + timedelta(minutes=5), close_price=110.0, index=5
        )
        order_block_snapshot = order_block.model_copy()
        break_snapshot = confirming_break.model_copy()

        BreakerBlockDetector().detect([], [order_block], [confirming_break])

        assert order_block == order_block_snapshot
        assert confirming_break == break_snapshot
