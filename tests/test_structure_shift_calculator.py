"""
Unit tests for app.market_structure.shift_calculator.StructureShiftCalculator.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.liquidity.results import (
    LiquidityLevel,
    LiquiditySide,
    LiquiditySweepResult,
    LiquidityStrength,
    LiquidityType,
    SweepDirection,
)
from app.market_structure.bos_detector import BOSDetector
from app.market_structure.choch_detector import CHOCHDetector
from app.market_structure.displacement import DisplacementDetector, StructureShiftCalculationError
from app.market_structure.mss_detector import MSSDetector
from app.market_structure.results import MarketStructureResult, SwingPoint, SwingType, TrendDirection
from app.market_structure.shift_calculator import StructureShiftCalculator
from app.market_structure.shift_results import StructureBreakType
from app.models.candle import Candle

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=200.0,
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


def _sweep(direction: SweepDirection, timestamp, price=110.0, symbol="BTC-USDT", timeframe="15m") -> LiquiditySweepResult:
    level = LiquidityLevel(
        liquidity_id=f"level-{price}-{timestamp.isoformat()}",
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


def _structure(trend: TrendDirection, symbol="BTC-USDT", timeframe="15m") -> MarketStructureResult:
    return MarketStructureResult(
        symbol=symbol,
        timeframe=timeframe,
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


def _make_calculator() -> StructureShiftCalculator:
    return StructureShiftCalculator(
        displacement_detector=DisplacementDetector(
            minimum_body_ratio=0.60,
            bullish_close_location_minimum=0.75,
            bearish_close_location_maximum=0.25,
        ),
        bos_detector=BOSDetector(),
        choch_detector=CHOCHDetector(),
        mss_detector=MSSDetector(),
    )


def _prefix_candles(count: int, descending: bool) -> list[Candle]:
    return [
        _candle(
            i,
            100 - i if descending else 100 + i,
            101 - i if descending else 101 + i,
            99 - i if descending else 99 + i,
            100 - i if descending else 100 + i,
        )
        for i in range(count)
    ]


class TestCalculate:
    def test_complete_calculation_produces_choch_and_mss(self):
        structure = _structure(TrendDirection.BEARISH)
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        break_candle = _candle(10, open_=104, high=112, low=103, close=110)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        candles = _prefix_candles(10, descending=True) + [break_candle]
        volume_ema = [100.0] * len(candles)

        result = _make_calculator().calculate(
            candles, [lower_high], structure, [sweep], volume_ema
        )

        assert len(result.choch_results) == 1
        assert len(result.mss_results) == 1
        assert result.latest_confirmed_break is not None

    def test_sweep_must_precede_break(self):
        structure = _structure(TrendDirection.BEARISH)
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        break_candle = _candle(10, open_=104, high=112, low=103, close=110)
        late_sweep = _sweep(SweepDirection.BULLISH, break_candle.timestamp + timedelta(minutes=1))
        candles = _prefix_candles(10, descending=True) + [break_candle]
        volume_ema = [100.0] * len(candles)

        result = _make_calculator().calculate(
            candles, [lower_high], structure, [late_sweep], volume_ema
        )
        assert result.choch_results == []
        assert result.latest_confirmed_break is None

    def test_latest_result_priority_mss_over_choch_over_bos(self):
        structure = _structure(TrendDirection.BEARISH)
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        break_candle = _candle(10, open_=104, high=112, low=103, close=110)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        candles = _prefix_candles(10, descending=True) + [break_candle]
        volume_ema = [100.0] * len(candles)

        result = _make_calculator().calculate(
            candles, [lower_high], structure, [sweep], volume_ema
        )
        # CHOCH and MSS share the same break candle timestamp; MSS must win.
        assert result.latest_confirmed_break.break_type == StructureBreakType.MSS

    def test_combined_output_deterministic(self):
        structure = _structure(TrendDirection.BEARISH)
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        break_candle = _candle(10, open_=104, high=112, low=103, close=110)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        candles = _prefix_candles(10, descending=True) + [break_candle]
        volume_ema = [100.0] * len(candles)
        calculator = _make_calculator()

        result_one = calculator.calculate(candles, [lower_high], structure, [sweep], volume_ema)
        result_two = calculator.calculate(candles, [lower_high], structure, [sweep], volume_ema)

        assert [b.break_id for b in result_one.all_breaks] == [
            b.break_id for b in result_two.all_breaks
        ]

    def test_symbol_consistency_validation(self):
        structure = _structure(TrendDirection.BEARISH, symbol="ETH-USDT")
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        candles = _prefix_candles(10, descending=True)
        with pytest.raises(StructureShiftCalculationError):
            _make_calculator().calculate(candles, [lower_high], structure, [], [None] * 10)

    def test_timeframe_consistency_validation(self):
        structure = _structure(TrendDirection.BEARISH, timeframe="1h")
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        candles = _prefix_candles(10, descending=True)
        with pytest.raises(StructureShiftCalculationError):
            _make_calculator().calculate(candles, [lower_high], structure, [], [None] * 10)

    def test_latest_confirmed_shift_helper(self):
        structure = _structure(TrendDirection.BEARISH)
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        break_candle = _candle(10, open_=104, high=112, low=103, close=110)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        candles = _prefix_candles(10, descending=True) + [break_candle]
        volume_ema = [100.0] * len(candles)

        latest = _make_calculator().calculate_latest_confirmed_shift(
            candles, [lower_high], structure, [sweep], volume_ema
        )
        assert latest is not None
        assert latest.break_type == StructureBreakType.MSS

    def test_no_partial_silent_failure_returns_empty_results(self):
        structure = _structure(TrendDirection.RANGE)
        candles = _prefix_candles(10, descending=True)
        volume_ema = [100.0] * len(candles)

        result = _make_calculator().calculate(candles, [], structure, [], volume_ema)
        assert result.choch_results == []
        assert result.mss_results == []
        assert result.latest_confirmed_break is None

    def test_input_data_not_mutated(self):
        structure = _structure(TrendDirection.BEARISH)
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        break_candle = _candle(10, open_=104, high=112, low=103, close=110)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        candles = _prefix_candles(10, descending=True) + [break_candle]
        volume_ema = [100.0] * len(candles)

        candles_snapshot = [c.model_copy() for c in candles]
        swing_snapshot = lower_high.model_copy()

        _make_calculator().calculate(candles, [lower_high], structure, [sweep], volume_ema)

        assert candles == candles_snapshot
        assert lower_high == swing_snapshot

    def test_no_entry_sl_tp_risk_score_or_signal_fields(self):
        structure = _structure(TrendDirection.BEARISH)
        lower_high = _swing(0, SwingType.HIGH, 105.0)
        break_candle = _candle(10, open_=104, high=112, low=103, close=110)
        sweep = _sweep(SweepDirection.BULLISH, UTC_NOW + timedelta(minutes=5))
        candles = _prefix_candles(10, descending=True) + [break_candle]
        volume_ema = [100.0] * len(candles)

        result = _make_calculator().calculate(candles, [lower_high], structure, [sweep], volume_ema)
        result_fields = set(type(result).model_fields.keys())
        forbidden = {
            "entry_price",
            "stop_loss",
            "take_profit",
            "risk_reward_ratio",
            "confidence_score",
            "signal_type",
        }
        assert result_fields.isdisjoint(forbidden)
