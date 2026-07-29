"""
Unit tests for app.market_structure.mss_detector.
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
from app.market_structure.mss_detector import MSSDetector
from app.market_structure.results import SwingPoint, SwingType
from app.market_structure.shift_results import (
    BreakConfirmation,
    BreakDirection,
    DisplacementResult,
    StructureBreakResult,
    StructureBreakType,
)

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


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


def _displacement(direction: BreakDirection, confirmed=True, index=10) -> DisplacementResult:
    return DisplacementResult(
        symbol="BTC-USDT",
        timeframe="15m",
        candle_timestamp=UTC_NOW + timedelta(minutes=index),
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


def _sweep(direction: SweepDirection, timestamp, confirmed=True, price=110.0) -> LiquiditySweepResult:
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
    reclaimed = confirmed
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
        reclaimed_level=reclaimed,
        confirmed=confirmed,
        reason="test sweep",
    )


def _choch(
    direction: BreakDirection,
    broken_swing: SwingPoint,
    displacement: DisplacementResult,
    sweep,
    wick_only=False,
    break_index=10,
    confirmation=BreakConfirmation.CONFIRMED,
) -> StructureBreakResult:
    return StructureBreakResult(
        break_id=f"choch-{direction.value}-{break_index}",
        symbol="BTC-USDT",
        timeframe="15m",
        break_type=StructureBreakType.CHOCH,
        direction=direction,
        broken_swing=broken_swing,
        break_candle_timestamp=UTC_NOW + timedelta(minutes=break_index),
        break_candle_index=break_index,
        break_price=broken_swing.price,
        close_price=broken_swing.price + 5 if direction == BreakDirection.BULLISH else broken_swing.price - 5,
        displacement=displacement,
        preceding_liquidity_sweep=sweep,
        strong_close_beyond_structure=not wick_only,
        wick_only_break=wick_only,
        confirmation=confirmation,
        reason="test choch",
    )


def _bos(
    direction: BreakDirection, broken_swing: SwingPoint, displacement, sweep, break_index=15
) -> StructureBreakResult:
    return StructureBreakResult(
        break_id=f"bos-{direction.value}-{break_index}",
        symbol="BTC-USDT",
        timeframe="15m",
        break_type=StructureBreakType.BOS,
        direction=direction,
        broken_swing=broken_swing,
        break_candle_timestamp=UTC_NOW + timedelta(minutes=break_index),
        break_candle_index=break_index,
        break_price=broken_swing.price,
        close_price=broken_swing.price + 5 if direction == BreakDirection.BULLISH else broken_swing.price - 5,
        displacement=displacement,
        preceding_liquidity_sweep=sweep,
        strong_close_beyond_structure=True,
        wick_only_break=False,
        confirmation=BreakConfirmation.CONFIRMED,
        reason="test bos",
    )


class TestMSSDetector:
    def test_confirmed_bullish_choch_becomes_bullish_mss(self):
        swing = _swing(0, SwingType.HIGH, 105.0)
        displacement = _displacement(BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        choch = _choch(BreakDirection.BULLISH, swing, displacement, sweep)

        results = MSSDetector().detect([], [choch])
        assert len(results) == 1
        assert results[0].break_type == StructureBreakType.MSS
        assert results[0].direction == BreakDirection.BULLISH
        assert results[0].confirmation == BreakConfirmation.CONFIRMED

    def test_confirmed_bearish_choch_becomes_bearish_mss(self):
        swing = _swing(0, SwingType.LOW, 95.0)
        displacement = _displacement(BreakDirection.BEARISH)
        sweep = _sweep(SweepDirection.BEARISH, UTC_NOW + timedelta(minutes=5))
        choch = _choch(BreakDirection.BEARISH, swing, displacement, sweep)

        results = MSSDetector().detect([], [choch])
        assert len(results) == 1
        assert results[0].direction == BreakDirection.BEARISH

    def test_choch_without_sweep_cannot_become_mss(self):
        swing = _swing(0, SwingType.HIGH, 105.0)
        displacement = _displacement(BreakDirection.BULLISH)
        # A CHOCH candidate lacking a supporting sweep can never be
        # CONFIRMED (per the model's own invariant), so it is represented
        # as REJECTED here to model a rejected, sweep-less candidate.
        choch = _choch(
            BreakDirection.BULLISH,
            swing,
            displacement,
            sweep=None,
            confirmation=BreakConfirmation.REJECTED,
        )

        results = MSSDetector().detect([], [choch])
        assert results == []

    def test_choch_with_wick_only_break_cannot_become_mss(self):
        swing = _swing(0, SwingType.HIGH, 105.0)
        displacement = _displacement(BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        # A CONFIRMED result can never have wick_only_break=True (model
        # invariant), so a wick-only candidate is represented as REJECTED.
        choch = _choch(
            BreakDirection.BULLISH,
            swing,
            displacement,
            sweep,
            wick_only=True,
            confirmation=BreakConfirmation.REJECTED,
        )

        results = MSSDetector().detect([], [choch])
        assert results == []

    def test_unconfirmed_displacement_cannot_become_mss(self):
        swing = _swing(0, SwingType.HIGH, 105.0)
        displacement = _displacement(BreakDirection.BULLISH, confirmed=False)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        choch = _choch(BreakDirection.BULLISH, swing, displacement, sweep)

        results = MSSDetector().detect([], [choch])
        assert results == []

    def test_follow_up_bos_retained_as_supporting_metadata(self):
        swing = _swing(0, SwingType.HIGH, 105.0)
        displacement = _displacement(BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        choch = _choch(BreakDirection.BULLISH, swing, displacement, sweep, break_index=10)

        bos_swing = _swing(1, SwingType.HIGH, 120.0)
        bos = _bos(BreakDirection.BULLISH, bos_swing, displacement, sweep, break_index=15)

        results = MSSDetector().detect([bos], [choch])
        assert len(results) == 1
        assert results[0].metadata["supporting_bos_break_id"] == bos.break_id

    def test_mss_preserves_original_sweep(self):
        swing = _swing(0, SwingType.HIGH, 105.0)
        displacement = _displacement(BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        choch = _choch(BreakDirection.BULLISH, swing, displacement, sweep)

        results = MSSDetector().detect([], [choch])
        assert results[0].preceding_liquidity_sweep.sweep_id == sweep.sweep_id

    def test_mss_preserves_original_displacement(self):
        swing = _swing(0, SwingType.HIGH, 105.0)
        displacement = _displacement(BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        choch = _choch(BreakDirection.BULLISH, swing, displacement, sweep)

        results = MSSDetector().detect([], [choch])
        assert results[0].displacement == displacement

    def test_no_duplicate_mss(self):
        swing = _swing(0, SwingType.HIGH, 105.0)
        displacement = _displacement(BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        choch = _choch(BreakDirection.BULLISH, swing, displacement, sweep)

        results = MSSDetector().detect([], [choch, choch])
        assert len(results) == 1

    def test_break_type_is_mss(self):
        swing = _swing(0, SwingType.HIGH, 105.0)
        displacement = _displacement(BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        choch = _choch(BreakDirection.BULLISH, swing, displacement, sweep)

        results = MSSDetector().detect([], [choch])
        assert all(r.break_type == StructureBreakType.MSS for r in results)

    def test_confirmation_is_confirmed(self):
        swing = _swing(0, SwingType.HIGH, 105.0)
        displacement = _displacement(BreakDirection.BULLISH)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        choch = _choch(BreakDirection.BULLISH, swing, displacement, sweep)

        results = MSSDetector().detect([], [choch])
        assert all(r.confirmation == BreakConfirmation.CONFIRMED for r in results)
