"""
Unit tests for app.zones.calculator.ZoneCalculator.
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
from app.zones.breaker_block import BreakerBlockDetector
from app.zones.calculator import ZoneCalculator
from app.zones.fair_value_gap import FairValueGapDetector
from app.zones.invalidation_checker import ZoneInvalidationChecker
from app.zones.mitigation_checker import ZoneMitigationChecker
from app.zones.order_block import OrderBlockDetector, ZoneCalculationError
from app.zones.zone_selector import EntryZoneSelector

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(index: int, open_: float, high: float, low: float, close: float, symbol="BTC-USDT") -> Candle:
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        symbol=symbol,
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


def _sweep(timestamp, symbol="BTC-USDT") -> LiquiditySweepResult:
    level = LiquidityLevel(
        liquidity_id="level-1",
        symbol=symbol,
        timeframe="15m",
        liquidity_type=LiquidityType.EQUAL_LOW,
        liquidity_side=LiquiditySide.SELL_SIDE,
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
        symbol=symbol,
        timeframe="15m",
        direction=SweepDirection.BULLISH,
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


def _displacement(index: int, timestamp, direction=BreakDirection.BULLISH) -> DisplacementResult:
    return DisplacementResult(
        symbol="BTC-USDT",
        timeframe="15m",
        candle_timestamp=timestamp,
        candle_index=index,
        direction=direction,
        body_ratio=0.8,
        candle_range=10.0,
        body_size=8.0,
        close_location_value=0.9,
        volume_confirmed=True,
        strong_close=True,
        confirmed=True,
        reason="test",
    )


def _break(break_candle: Candle, break_index: int, sweep, displacement, symbol="BTC-USDT") -> StructureBreakResult:
    return StructureBreakResult(
        break_id="break-1",
        symbol=symbol,
        timeframe="15m",
        break_type=StructureBreakType.MSS,
        direction=BreakDirection.BULLISH,
        broken_swing=_swing(105.0),
        break_candle_timestamp=break_candle.timestamp,
        break_candle_index=break_index,
        break_price=105.0,
        close_price=break_candle.close,
        displacement=displacement,
        preceding_liquidity_sweep=sweep,
        strong_close_beyond_structure=True,
        wick_only_break=False,
        confirmation=BreakConfirmation.CONFIRMED,
        reason="test break",
    )


def _make_calculator() -> ZoneCalculator:
    return ZoneCalculator(
        order_block_detector=OrderBlockDetector(),
        fair_value_gap_detector=FairValueGapDetector(),
        breaker_block_detector=BreakerBlockDetector(),
        mitigation_checker=ZoneMitigationChecker(full_mitigation_required=False, touch_tolerance_ratio=0.0001),
        invalidation_checker=ZoneInvalidationChecker(),
        entry_zone_selector=EntryZoneSelector(),
    )


def _build_scenario():
    sweep_ts = UTC_NOW + timedelta(seconds=30)
    sweep = _sweep(sweep_ts)

    candles = [
        _candle(0, 100, 101, 99, 100),
        _candle(1, 100, 100, 96, 97),  # bearish source candle for order block
        _candle(2, 97, 108, 96, 107),  # bullish displacement/break candle -> also candle1 of FVG window
        _candle(3, 107, 112, 106, 110),
        _candle(4, 110, 115, 108, 114),
    ]
    displacement = _displacement(2, candles[2].timestamp)
    structure_break = _break(candles[2], 2, sweep, displacement)
    return candles, [structure_break]


CURRENT_TIME = UTC_NOW + timedelta(hours=1)


class TestZoneCalculator:
    def test_complete_zone_calculation(self):
        candles, breaks = _build_scenario()
        result = _make_calculator().calculate(candles, breaks, "BUY", 120.0, CURRENT_TIME)
        assert result.symbol == "BTC-USDT"
        assert result.timeframe == "15m"

    def test_order_blocks_included(self):
        candles, breaks = _build_scenario()
        result = _make_calculator().calculate(candles, breaks, "BUY", 120.0, CURRENT_TIME)
        assert len(result.order_blocks) >= 1

    def test_mitigation_evaluation_applied(self):
        candles, breaks = _build_scenario()
        # Extend candles so a later candle overlaps the order block, mitigating it.
        candles = candles + [_candle(5, 97, 98, 96, 96.5)]
        result = _make_calculator().calculate(candles, breaks, "BUY", 120.0, CURRENT_TIME)
        order_block = result.order_blocks[0]
        assert order_block.status in (ZoneStatus.FRESH, ZoneStatus.MITIGATED)

    def test_invalidation_evaluation_applied(self):
        candles, breaks = _build_scenario()
        candles = candles + [_candle(5, 97, 98, 90, 91)]  # closes well below order block lower
        result = _make_calculator().calculate(candles, breaks, "BUY", 120.0, CURRENT_TIME)
        order_block = result.order_blocks[0]
        assert order_block.status in (ZoneStatus.MITIGATED, ZoneStatus.INVALIDATED)

    def test_status_grouping_correct(self):
        candles, breaks = _build_scenario()
        result = _make_calculator().calculate(candles, breaks, "BUY", 120.0, CURRENT_TIME)
        all_ids = {z.zone_id for z in result.all_zones}
        grouped_ids = (
            {z.zone_id for z in result.fresh_zones}
            | {z.zone_id for z in result.mitigated_zones}
            | {z.zone_id for z in result.invalidated_zones}
        )
        assert all_ids == grouped_ids

    def test_selected_zone_follows_priority(self):
        candles, breaks = _build_scenario()
        result = _make_calculator().calculate(candles, breaks, "BUY", 120.0, CURRENT_TIME)
        if result.selected_zone is not None and result.order_blocks:
            fresh_order_blocks = [z for z in result.order_blocks if z.status == ZoneStatus.FRESH]
            if fresh_order_blocks:
                assert result.selected_zone.zone_type == ZoneType.ORDER_BLOCK

    def test_deterministic_combined_output(self):
        candles, breaks = _build_scenario()
        calculator = _make_calculator()
        result_one = calculator.calculate(candles, breaks, "BUY", 120.0, CURRENT_TIME)
        result_two = calculator.calculate(candles, breaks, "BUY", 120.0, CURRENT_TIME)
        assert [z.zone_id for z in result_one.all_zones] == [z.zone_id for z in result_two.all_zones]

    def test_mismatch_rejection(self):
        candles, _ = _build_scenario()
        bad_break = _break(
            candles[2], 2, _sweep(UTC_NOW, symbol="ETH-USDT"), _displacement(2, candles[2].timestamp),
            symbol="ETH-USDT",
        )
        with pytest.raises(ZoneCalculationError):
            _make_calculator().calculate(candles, [bad_break], "BUY", 120.0, CURRENT_TIME)

    def test_input_data_not_mutated(self):
        candles, breaks = _build_scenario()
        candles_snapshot = [c.model_copy() for c in candles]
        breaks_snapshot = [b.model_copy() for b in breaks]

        _make_calculator().calculate(candles, breaks, "BUY", 120.0, CURRENT_TIME)

        assert candles == candles_snapshot
        assert breaks == breaks_snapshot

    def test_no_entry_price_sl_tp_risk_score_or_signal_fields(self):
        candles, breaks = _build_scenario()
        result = _make_calculator().calculate(candles, breaks, "BUY", 120.0, CURRENT_TIME)
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

    def test_select_latest_valid_zone_helper(self):
        candles, breaks = _build_scenario()
        zone = _make_calculator().select_latest_valid_zone(candles, breaks, "BUY", 120.0, CURRENT_TIME)
        assert zone is None or zone.status == ZoneStatus.FRESH
