"""
Unit tests for app.validators.fake_breakout_filter.FakeBreakoutFilter.
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
from app.validators.fake_breakout_filter import FakeBreakoutFilter

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
        swing_id=f"swing-{price}",
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


def _break(
    direction: BreakDirection, break_timestamp, broken_price: float, sweep: LiquiditySweepResult
) -> StructureBreakResult:
    displacement = DisplacementResult(
        symbol="BTC-USDT",
        timeframe="15m",
        candle_timestamp=break_timestamp,
        candle_index=5,
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
    return StructureBreakResult(
        break_id="break-1",
        symbol="BTC-USDT",
        timeframe="15m",
        break_type=StructureBreakType.BOS,
        direction=direction,
        broken_swing=_swing(broken_price, SwingType.HIGH if direction == BreakDirection.BULLISH else SwingType.LOW),
        break_candle_timestamp=break_timestamp,
        break_candle_index=5,
        break_price=broken_price,
        close_price=broken_price + 5 if direction == BreakDirection.BULLISH else broken_price - 5,
        displacement=displacement,
        preceding_liquidity_sweep=sweep,
        strong_close_beyond_structure=True,
        wick_only_break=False,
        confirmation=BreakConfirmation.CONFIRMED,
        reason="test break",
    )


def _filter(max_reversal=3, tolerance=0.0001) -> FakeBreakoutFilter:
    return FakeBreakoutFilter(
        maximum_reversal_candles=max_reversal, return_inside_tolerance_ratio=tolerance
    )


class TestBullishFakeBreakout:
    def test_close_back_below_broken_high_fails(self):
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW - timedelta(minutes=10))
        break_ts = UTC_NOW
        structure_break = _break(BreakDirection.BULLISH, break_ts, broken_price=100.0, sweep=sweep)
        reversal = _candle(1, open_=104, high=105, low=90, close=95)  # closes back below 100

        result = _filter().validate([reversal], sweep, structure_break)
        assert result.passed is False
        assert result.rejection_code == "FAKE_BREAKOUT_DETECTED"

    def test_wick_only_return_does_not_count(self):
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW - timedelta(minutes=10))
        structure_break = _break(BreakDirection.BULLISH, UTC_NOW, broken_price=100.0, sweep=sweep)
        # Wicks below 100 but closes back above.
        reversal = _candle(1, open_=104, high=105, low=95, close=102)

        result = _filter().validate([reversal], sweep, structure_break)
        assert result.passed is True

    def test_retest_staying_beyond_structure_passes(self):
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW - timedelta(minutes=10))
        structure_break = _break(BreakDirection.BULLISH, UTC_NOW, broken_price=100.0, sweep=sweep)
        retest = _candle(1, open_=104, high=106, low=101, close=105)  # stays above 100

        result = _filter().validate([retest], sweep, structure_break)
        assert result.passed is True

    def test_return_after_maximum_window_does_not_count(self):
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW - timedelta(minutes=10))
        structure_break = _break(BreakDirection.BULLISH, UTC_NOW, broken_price=100.0, sweep=sweep)
        filler1 = _candle(1, open_=104, high=105, low=103, close=104)
        filler2 = _candle(2, open_=104, high=105, low=103, close=104)
        filler3 = _candle(3, open_=104, high=105, low=103, close=104)
        too_late_reversal = _candle(4, open_=103, high=104, low=90, close=92)

        result = _filter(max_reversal=3).validate(
            [filler1, filler2, filler3, too_late_reversal], sweep, structure_break
        )
        assert result.passed is True


class TestBearishFakeBreakout:
    def test_close_back_above_broken_low_fails(self):
        sweep = _sweep(SweepDirection.BEARISH, UTC_NOW - timedelta(minutes=10))
        structure_break = _break(BreakDirection.BEARISH, UTC_NOW, broken_price=100.0, sweep=sweep)
        reversal = _candle(1, open_=96, high=110, low=95, close=105)  # closes back above 100

        result = _filter().validate([reversal], sweep, structure_break)
        assert result.passed is False
        assert result.rejection_code == "FAKE_BREAKOUT_DETECTED"


class TestGuardConditions:
    def test_sweep_after_break_is_invalid(self):
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=10))
        structure_break = _break(BreakDirection.BULLISH, UTC_NOW, broken_price=100.0, sweep=sweep)
        reversal = _candle(1, open_=104, high=105, low=90, close=95)

        result = _filter().validate([reversal], sweep, structure_break)
        assert result.passed is False
        assert result.rejection_code == "INVALID_SWEEP_BREAK_ORDER"

    def test_missing_sweep_fails_analysis(self):
        structure_break = _break(
            BreakDirection.BULLISH, UTC_NOW, broken_price=100.0, sweep=_sweep(SweepDirection.BULLISH, UTC_NOW - timedelta(minutes=10))
        )
        result = _filter().validate([], None, structure_break)
        assert result.passed is False
        assert result.rejection_code == "INVALID_SWEEP_BREAK_ORDER"

    def test_missing_structure_break_fails(self):
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW - timedelta(minutes=10))
        result = _filter().validate([], sweep, None)
        assert result.passed is False
        assert result.rejection_code == "STRUCTURE_BREAK_DATA_MISSING"

    def test_deterministic_reversal_candle_selected(self):
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW - timedelta(minutes=10))
        structure_break = _break(BreakDirection.BULLISH, UTC_NOW, broken_price=100.0, sweep=sweep)
        reversal = _candle(1, open_=104, high=105, low=90, close=95)

        result_one = _filter().evaluate([reversal], sweep, structure_break)
        result_two = _filter().evaluate([reversal], sweep, structure_break)
        assert result_one.reversal_candle_timestamp == result_two.reversal_candle_timestamp

    def test_inputs_not_mutated(self):
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW - timedelta(minutes=10))
        structure_break = _break(BreakDirection.BULLISH, UTC_NOW, broken_price=100.0, sweep=sweep)
        reversal = _candle(1, open_=104, high=105, low=90, close=95)
        candles_snapshot = [reversal.model_copy()]
        sweep_snapshot = sweep.model_copy()
        break_snapshot = structure_break.model_copy()

        _filter().validate([reversal], sweep, structure_break)

        assert [reversal] == candles_snapshot
        assert sweep == sweep_snapshot
        assert structure_break == break_snapshot

    def test_score_remains_zero(self):
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW - timedelta(minutes=10))
        structure_break = _break(BreakDirection.BULLISH, UTC_NOW, broken_price=100.0, sweep=sweep)
        passing = _filter().validate(
            [_candle(1, open_=104, high=106, low=101, close=105)], sweep, structure_break
        )
        assert passing.score == 0.0
        failing = _filter().validate(
            [_candle(1, open_=104, high=105, low=90, close=95)], sweep, structure_break
        )
        assert failing.score == 0.0
