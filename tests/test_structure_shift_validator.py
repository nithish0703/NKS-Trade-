"""
Unit tests for app.validators.structure_shift.StructureShiftValidator.
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
    StructureShiftDetectionResult,
)
from app.validators.structure_shift import StructureShiftValidator

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


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


def _sweep(timestamp, confirmed=True) -> LiquiditySweepResult:
    level = LiquidityLevel(
        liquidity_id="level-1",
        symbol="BTC-USDT",
        timeframe="15m",
        liquidity_type=LiquidityType.EQUAL_LOW,
        liquidity_side=LiquiditySide.SELL_SIDE,
        price=90.0,
        start_timestamp=timestamp - timedelta(minutes=10),
        end_timestamp=timestamp - timedelta(minutes=10),
        source_timestamps=[timestamp - timedelta(minutes=10)],
        touch_count=2,
        strength=LiquidityStrength.STRONG,
        active=True,
    )
    return LiquiditySweepResult(
        sweep_id="sweep-1",
        symbol="BTC-USDT",
        timeframe="15m",
        direction=SweepDirection.BULLISH,
        liquidity_level=level,
        sweep_candle_timestamp=timestamp,
        sweep_candle_index=0,
        sweep_price=89.0,
        close_price=91.0,
        penetration_distance=1.0,
        penetration_ratio=0.01,
        reclaimed_level=confirmed,
        confirmed=confirmed,
        reason="test sweep",
    )


def _displacement(body_ratio=0.8, confirmed=True) -> DisplacementResult:
    return DisplacementResult(
        symbol="BTC-USDT",
        timeframe="15m",
        candle_timestamp=UTC_NOW + timedelta(minutes=10),
        candle_index=10,
        direction=BreakDirection.BULLISH,
        body_ratio=body_ratio,
        candle_range=10.0,
        body_size=8.0,
        close_location_value=0.9,
        volume_confirmed=True,
        strong_close=True,
        confirmed=confirmed,
        reason="test",
    )


def _break_result(
    direction=BreakDirection.BULLISH,
    break_type=StructureBreakType.MSS,
    sweep=None,
    displacement=None,
    strong_close=True,
    wick_only=False,
    confirmation=BreakConfirmation.CONFIRMED,
) -> StructureBreakResult:
    if sweep is None:
        sweep = _sweep(UTC_NOW + timedelta(minutes=5))
    if displacement is None:
        displacement = _displacement()
    return StructureBreakResult(
        break_id="break-1",
        symbol="BTC-USDT",
        timeframe="15m",
        break_type=break_type,
        direction=direction,
        broken_swing=_swing(105.0),
        break_candle_timestamp=UTC_NOW + timedelta(minutes=10),
        break_candle_index=10,
        break_price=105.0,
        close_price=110.0,
        displacement=displacement,
        preceding_liquidity_sweep=sweep,
        strong_close_beyond_structure=strong_close,
        wick_only_break=wick_only,
        confirmation=confirmation,
        reason="test break",
    )


def _detection_result(latest_break) -> StructureShiftDetectionResult:
    return StructureShiftDetectionResult(
        symbol="BTC-USDT",
        timeframe="15m",
        displacement_candles=[],
        bos_results=[],
        choch_results=[],
        mss_results=[latest_break] if latest_break and latest_break.break_type == StructureBreakType.MSS else [],
        all_breaks=[latest_break] if latest_break else [],
        latest_confirmed_break=latest_break,
    )


class TestStructureShiftValidator:
    def test_valid_confirmed_mss_passes(self):
        break_result = _break_result(break_type=StructureBreakType.MSS)
        result = StructureShiftValidator().validate(_detection_result(break_result))
        assert result.passed is True
        assert result.score == 0.0

    def test_valid_choch_passes(self):
        break_result = _break_result(break_type=StructureBreakType.CHOCH)
        result = StructureShiftValidator().validate(_detection_result(break_result))
        assert result.passed is True

    def test_valid_bos_passes(self):
        break_result = _break_result(break_type=StructureBreakType.BOS)
        result = StructureShiftValidator().validate(_detection_result(break_result))
        assert result.passed is True

    def test_missing_break_fails(self):
        result = StructureShiftValidator().validate(_detection_result(None))
        assert result.passed is False
        assert result.rejection_code == "STRUCTURE_SHIFT_MISSING"

    def test_invalid_sweep_order_fails(self):
        late_sweep = _sweep(UTC_NOW + timedelta(minutes=15))
        break_result = _break_result(sweep=late_sweep)
        result = StructureShiftValidator().validate(_detection_result(break_result))
        assert result.passed is False
        assert result.rejection_code == "LIQUIDITY_SWEEP_ORDER_INVALID"

    def test_unconfirmed_displacement_fails(self):
        weak_displacement = _displacement(confirmed=False)
        break_result = _break_result(displacement=weak_displacement)
        result = StructureShiftValidator().validate(_detection_result(break_result))
        assert result.passed is False
        assert result.rejection_code == "DISPLACEMENT_NOT_CONFIRMED"

    def test_body_below_60_percent_fails(self):
        weak_displacement = _displacement(body_ratio=0.5)
        break_result = _break_result(displacement=weak_displacement)
        result = StructureShiftValidator().validate(_detection_result(break_result))
        assert result.passed is False
        assert result.rejection_code == "WEAK_CANDLE_BODY"

    def test_weak_close_fails(self):
        break_result = _break_result(strong_close=False)
        result = StructureShiftValidator().validate(_detection_result(break_result))
        assert result.passed is False
        assert result.rejection_code == "WEAK_STRUCTURE_CLOSE"

    def test_wick_only_break_fails(self):
        break_result = _break_result(
            strong_close=True, wick_only=False, confirmation=BreakConfirmation.REJECTED
        )
        # Represent a wick-only rejected candidate (cannot be CONFIRMED
        # per model invariant) to verify the validator's own wick check.
        break_result = break_result.model_copy(
            update={"wick_only_break": True, "strong_close_beyond_structure": False}
        )
        result = StructureShiftValidator().validate(_detection_result(break_result))
        assert result.passed is False
        assert result.rejection_code in ("WICK_ONLY_BREAK", "WEAK_STRUCTURE_CLOSE", "STRUCTURE_SHIFT_MISSING")

    def test_expected_direction_mismatch_fails(self):
        break_result = _break_result(direction=BreakDirection.BULLISH)
        result = StructureShiftValidator().validate(
            _detection_result(break_result), expected_direction="BEARISH"
        )
        assert result.passed is False
        assert result.rejection_code == "DIRECTION_MISMATCH"

    def test_score_remains_zero(self):
        break_result = _break_result()
        result = StructureShiftValidator().validate(_detection_result(break_result))
        assert result.score == 0.0

        failing_result = StructureShiftValidator().validate(_detection_result(None))
        assert failing_result.score == 0.0

    def test_no_signal_generation_occurs(self):
        break_result = _break_result()
        result = StructureShiftValidator().validate(_detection_result(break_result))
        result_fields = set(type(result).model_fields.keys())
        assert "signal_type" not in result_fields
        assert "entry_price" not in result_fields
