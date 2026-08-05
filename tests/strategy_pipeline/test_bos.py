"""
Unit tests for app.strategy_pipeline.bos (Stage 3: BOS).

These tests drive the real DisplacementDetector and BOSDetector over
hand-built candle/swing/sweep fixtures: no detector is mocked and no
StructureBreakResult is hand-constructed, so "trade idea valid" is
proven against genuinely detected structural breaks.
"""

from datetime import datetime, timedelta, timezone

from app.liquidity.results import (
    LiquidityLevel,
    LiquiditySide,
    LiquidityStrength,
    LiquiditySweepResult,
    LiquidityType,
    SweepDirection,
)
from app.market_structure.bos_detector import BOSDetector
from app.market_structure.displacement import DisplacementDetector
from app.market_structure.results import SwingPoint, SwingType
from app.market_structure.shift_results import BreakDirection
from app.strategy_pipeline.bos import evaluate_bos
from app.models.candle import Candle

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
SYMBOL = "BTC-USDT"
TIMEFRAME = "15m"

VOLUME_EMA = 100.0
QUIET_VOLUME = 50.0
DISPLACEMENT_VOLUME = 300.0


def _candle(
    index: int, open_: float, high: float, low: float, close: float, volume: float
) -> Candle:
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
    )


def _swing(swing_id: str, swing_type: SwingType, price: float, candle_index: int) -> SwingPoint:
    return SwingPoint(
        swing_id=swing_id,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        timestamp=UTC_NOW + timedelta(minutes=candle_index),
        candle_index=candle_index,
        swing_type=swing_type,
        price=price,
        left_strength=3,
        right_strength=3,
        confirmed=True,
    )


def _sweep(
    direction: SweepDirection, candle_index: int, price: float = 110.0, sweep_id: str = None
) -> LiquiditySweepResult:
    timestamp = UTC_NOW + timedelta(minutes=candle_index)
    level = LiquidityLevel(
        liquidity_id=f"level-{direction.value}-{candle_index}",
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
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
        sweep_id=sweep_id or f"sweep-{direction.value}-{candle_index}",
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        direction=direction,
        liquidity_level=level,
        sweep_candle_timestamp=timestamp,
        sweep_candle_index=candle_index,
        sweep_price=price + 1 if direction == SweepDirection.BEARISH else price - 1,
        close_price=price - 1 if direction == SweepDirection.BEARISH else price + 1,
        penetration_distance=1.0,
        penetration_ratio=0.01,
        reclaimed_level=True,
        confirmed=True,
        reason="test sweep",
    )


def _displacement_detector() -> DisplacementDetector:
    return DisplacementDetector(
        minimum_body_ratio=0.6,
        bullish_close_location_minimum=0.75,
        bearish_close_location_maximum=0.25,
    )


def _bullish_displacement_candle(index: int, open_: float, close: float) -> Candle:
    return _candle(index, open_=open_, high=close + 1, low=open_ - 1, close=close, volume=DISPLACEMENT_VOLUME)


def _bearish_displacement_candle(index: int, open_: float, close: float) -> Candle:
    return _candle(index, open_=open_, high=open_ + 1, low=close - 1, close=close, volume=DISPLACEMENT_VOLUME)


def _quiet_candle(index: int, price: float) -> Candle:
    return _candle(index, open_=price, high=price + 1, low=price - 1, close=price, volume=QUIET_VOLUME)


def _downtrend_prefix(count: int) -> list[Candle]:
    return [_quiet_candle(i, 100 - i) for i in range(count)]


def _uptrend_prefix(count: int) -> list[Candle]:
    return [_quiet_candle(i, 100 + i) for i in range(count)]


def _evaluate(candles, swings, validated_sweep, expected_direction: str):
    displacements = _displacement_detector().detect(candles, [VOLUME_EMA] * len(candles))
    liquidity_sweeps = [validated_sweep] if validated_sweep is not None else []
    return evaluate_bos(
        candles=candles,
        swings=swings,
        displacement_results=displacements,
        liquidity_sweeps=liquidity_sweeps,
        validated_sweep=validated_sweep,
        bos_detector=BOSDetector(),
        expected_direction=expected_direction,
    )


class TestEvaluateBosBullish:
    def test_confirmed_bullish_bos_anchored_to_the_sweep_passes(self):
        candles = _downtrend_prefix(10)
        candles.append(_bullish_displacement_candle(10, open_=104, close=111))
        swings = [_swing("swing-HIGH", SwingType.HIGH, 105.0, candle_index=3)]
        sweep = _sweep(SweepDirection.BULLISH, candle_index=5)

        result = _evaluate(candles, swings, sweep, "BUY")

        assert result.passed is True
        assert result.structure_break is not None
        assert result.structure_break.direction == BreakDirection.BULLISH

    def test_entry_break_carries_the_validated_sweep(self):
        candles = _downtrend_prefix(10)
        candles.append(_bullish_displacement_candle(10, open_=104, close=111))
        swings = [_swing("swing-HIGH", SwingType.HIGH, 105.0, candle_index=3)]
        sweep = _sweep(SweepDirection.BULLISH, candle_index=5)

        result = _evaluate(candles, swings, sweep, "BUY")

        assert result.structure_break.preceding_liquidity_sweep.sweep_id == sweep.sweep_id


class TestEvaluateBosBearish:
    def test_confirmed_bearish_bos_anchored_to_the_sweep_passes(self):
        candles = _uptrend_prefix(10)
        candles.append(_bearish_displacement_candle(10, open_=106, close=95))
        swings = [_swing("swing-LOW", SwingType.LOW, 99.0, candle_index=3)]
        sweep = _sweep(SweepDirection.BEARISH, candle_index=5)

        result = _evaluate(candles, swings, sweep, "SELL")

        assert result.passed is True
        assert result.structure_break.direction == BreakDirection.BEARISH


class TestEvaluateBosNoBreakYet:
    def test_no_displacement_candle_at_all_fails(self):
        candles = _downtrend_prefix(10)
        swings = [_swing("swing-HIGH", SwingType.HIGH, 105.0, candle_index=3)]
        sweep = _sweep(SweepDirection.BULLISH, candle_index=5)

        result = _evaluate(candles, swings, sweep, "BUY")

        assert result.passed is False
        assert result.structure_break is None

    def test_wrong_direction_bos_does_not_satisfy_the_opposite_expected_direction(self):
        candles = _downtrend_prefix(10)
        candles.append(_bullish_displacement_candle(10, open_=104, close=111))
        swings = [_swing("swing-HIGH", SwingType.HIGH, 105.0, candle_index=3)]
        sweep = _sweep(SweepDirection.BULLISH, candle_index=5)

        result = _evaluate(candles, swings, sweep, "SELL")

        assert result.passed is False
        assert result.structure_break is None


class TestEvaluateBosSweepPreconditions:
    def test_missing_sweep_fails_without_crashing(self):
        candles = _downtrend_prefix(10)
        candles.append(_bullish_displacement_candle(10, open_=104, close=111))
        swings = [_swing("swing-HIGH", SwingType.HIGH, 105.0, candle_index=3)]

        result = _evaluate(candles, swings, None, "BUY")

        assert result.passed is False
        assert result.structure_break is None
        assert "no confirmed liquidity sweep" in result.reason.lower()

    def test_unconfirmed_sweep_fails(self):
        candles = _downtrend_prefix(10)
        candles.append(_bullish_displacement_candle(10, open_=104, close=111))
        swings = [_swing("swing-HIGH", SwingType.HIGH, 105.0, candle_index=3)]
        sweep = _sweep(SweepDirection.BULLISH, candle_index=5).model_copy(
            update={"confirmed": False, "reclaimed_level": False}
        )

        result = _evaluate(candles, swings, sweep, "BUY")

        assert result.passed is False

    def test_bos_supported_by_a_different_unrelated_sweep_does_not_count(self):
        """
        A confirmed BOS exists and IS supported by *some* confirmed
        bullish sweep, but not the specific sweep this stage was asked
        to validate against (different sweep_id, different index) --
        the stage must not accept "any sweep will do."
        """
        candles = _downtrend_prefix(10)
        candles.append(_bullish_displacement_candle(10, open_=104, close=111))
        swings = [_swing("swing-HIGH", SwingType.HIGH, 105.0, candle_index=3)]

        # This is the sweep the real BOSDetector will actually attach
        # (closest preceding one it finds independently).
        real_supporting_sweep = _sweep(SweepDirection.BULLISH, candle_index=5, sweep_id="real-sweep")
        # This is a DIFFERENT confirmed bullish sweep object (e.g. from
        # a stale or unrelated cycle) that the stage is asked to
        # validate against instead.
        unrelated_sweep = _sweep(SweepDirection.BULLISH, candle_index=1, sweep_id="unrelated-sweep")

        displacements = _displacement_detector().detect(candles, [VOLUME_EMA] * len(candles))
        bos_results = BOSDetector().detect(
            candles, swings, displacements, [real_supporting_sweep, unrelated_sweep]
        )
        assert bos_results, "fixture must genuinely produce a BOS"
        assert bos_results[0].preceding_liquidity_sweep.sweep_id == "real-sweep"

        result = evaluate_bos(
            candles=candles,
            swings=swings,
            displacement_results=displacements,
            liquidity_sweeps=[real_supporting_sweep, unrelated_sweep],
            validated_sweep=unrelated_sweep,
            bos_detector=BOSDetector(),
            expected_direction="BUY",
        )

        assert result.passed is False
        assert result.structure_break is None


class TestEvaluateBosEmptyInputs:
    def test_empty_candles_fails_without_crashing(self):
        sweep = _sweep(SweepDirection.BULLISH, candle_index=5)
        result = evaluate_bos(
            candles=[],
            swings=[],
            displacement_results=[],
            liquidity_sweeps=[sweep],
            validated_sweep=sweep,
            bos_detector=BOSDetector(),
            expected_direction="BUY",
        )
        assert result.passed is False
        assert result.structure_break is None
