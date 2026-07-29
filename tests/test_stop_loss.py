"""
Unit tests for app.risk.stop_loss.DynamicStopLossCalculator.
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
from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType
from app.risk.results import StopLossSource
from app.risk.stop_loss import DynamicStopLossCalculator, RiskCalculationError

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _zone(direction: str, lower: float, upper: float) -> TradeZone:
    return TradeZone(
        zone_id="zone-1",
        symbol="BTC-USDT",
        timeframe="15m",
        zone_type=ZoneType.ORDER_BLOCK,
        direction=direction,
        lower_price=lower,
        upper_price=upper,
        created_at=UTC_NOW,
        source_candle_timestamp=UTC_NOW,
        source_candle_index=0,
        status=ZoneStatus.FRESH,
        touch_count=0,
    )


def _sweep(direction: SweepDirection, sweep_price: float) -> LiquiditySweepResult:
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
        start_timestamp=UTC_NOW - timedelta(minutes=20),
        end_timestamp=UTC_NOW - timedelta(minutes=20),
        source_timestamps=[UTC_NOW - timedelta(minutes=20)],
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
        sweep_candle_timestamp=UTC_NOW - timedelta(minutes=10),
        sweep_candle_index=0,
        sweep_price=sweep_price,
        close_price=91.0,
        penetration_distance=1.0,
        penetration_ratio=0.01,
        reclaimed_level=True,
        confirmed=True,
        reason="test sweep",
    )


def _calculator(atr_multiplier=1.5, buffer=0.0005) -> DynamicStopLossCalculator:
    return DynamicStopLossCalculator(atr_multiplier=atr_multiplier, structural_buffer_ratio=buffer)


class TestBuyStopLoss:
    def test_buy_atr_stop_candidate(self):
        zone = _zone("BUY", lower=90.0, upper=95.0)
        sweep = _sweep(SweepDirection.BULLISH, sweep_price=85.0)
        result = _calculator().calculate("BUY", entry_price=100.0, atr=2.0, selected_zone=zone, liquidity_sweep=sweep)
        atr_candidate = next(c for c in result.candidates if c.source == StopLossSource.ATR)
        assert atr_candidate.price == pytest.approx(100.0 - 2.0 * 1.5)

    def test_buy_zone_stop_candidate(self):
        zone = _zone("BUY", lower=90.0, upper=95.0)
        sweep = _sweep(SweepDirection.BULLISH, sweep_price=85.0)
        result = _calculator().calculate("BUY", entry_price=100.0, atr=2.0, selected_zone=zone, liquidity_sweep=sweep)
        zone_candidate = next(c for c in result.candidates if c.source == StopLossSource.ENTRY_ZONE)
        assert zone_candidate.price == pytest.approx(90.0 - 90.0 * 0.0005)

    def test_buy_sweep_stop_candidate(self):
        zone = _zone("BUY", lower=90.0, upper=95.0)
        sweep = _sweep(SweepDirection.BULLISH, sweep_price=85.0)
        result = _calculator().calculate("BUY", entry_price=100.0, atr=2.0, selected_zone=zone, liquidity_sweep=sweep)
        sweep_candidate = next(c for c in result.candidates if c.source == StopLossSource.LIQUIDITY_SWEEP)
        assert sweep_candidate.price == pytest.approx(85.0 - 85.0 * 0.0005)

    def test_buy_selects_lowest_valid_stop(self):
        zone = _zone("BUY", lower=90.0, upper=95.0)
        sweep = _sweep(SweepDirection.BULLISH, sweep_price=80.0)  # lowest candidate
        result = _calculator().calculate("BUY", entry_price=100.0, atr=2.0, selected_zone=zone, liquidity_sweep=sweep)
        assert result.valid is True
        assert result.selected_source == StopLossSource.LIQUIDITY_SWEEP
        assert result.selected_stop_loss == pytest.approx(80.0 - 80.0 * 0.0005)


class TestSellStopLoss:
    def test_sell_selects_highest_valid_stop(self):
        zone = _zone("SELL", lower=100.0, upper=105.0)
        sweep = _sweep(SweepDirection.BEARISH, sweep_price=110.0)  # highest candidate
        result = _calculator().calculate("SELL", entry_price=95.0, atr=2.0, selected_zone=zone, liquidity_sweep=sweep)
        assert result.valid is True
        assert result.selected_source == StopLossSource.LIQUIDITY_SWEEP
        assert result.selected_stop_loss == pytest.approx(110.0 + 110.0 * 0.0005)


class TestGuardConditions:
    def test_fixed_percentage_stop_not_used(self):
        zone = _zone("BUY", lower=90.0, upper=95.0)
        sweep = _sweep(SweepDirection.BULLISH, sweep_price=85.0)
        result = _calculator().calculate("BUY", entry_price=100.0, atr=2.0, selected_zone=zone, liquidity_sweep=sweep)
        sources = {c.source for c in result.candidates}
        assert sources == {StopLossSource.ATR, StopLossSource.ENTRY_ZONE, StopLossSource.LIQUIDITY_SWEEP}

    def test_invalid_atr_rejected(self):
        zone = _zone("BUY", lower=90.0, upper=95.0)
        sweep = _sweep(SweepDirection.BULLISH, sweep_price=85.0)
        result = _calculator().calculate("BUY", entry_price=100.0, atr=0.0, selected_zone=zone, liquidity_sweep=sweep)
        assert result.valid is False
        assert result.metadata["rejection_code"] == "INVALID_ATR"

    def test_zone_direction_mismatch(self):
        zone = _zone("SELL", lower=90.0, upper=95.0)  # wrong direction for BUY trade
        sweep = _sweep(SweepDirection.BULLISH, sweep_price=85.0)
        result = _calculator().calculate("BUY", entry_price=100.0, atr=2.0, selected_zone=zone, liquidity_sweep=sweep)
        assert result.valid is False
        assert result.metadata["rejection_code"] == "ZONE_DIRECTION_MISMATCH"

    def test_sweep_direction_mismatch(self):
        zone = _zone("BUY", lower=90.0, upper=95.0)
        sweep = _sweep(SweepDirection.BEARISH, sweep_price=110.0)  # wrong direction for BUY
        result = _calculator().calculate("BUY", entry_price=100.0, atr=2.0, selected_zone=zone, liquidity_sweep=sweep)
        assert result.valid is False
        assert result.metadata["rejection_code"] == "SWEEP_DIRECTION_MISMATCH"

    def test_wrong_side_candidates_rejected(self):
        zone = _zone("BUY", lower=90.0, upper=95.0)
        # Sweep price above entry -- would produce an invalid (wrong-side) candidate.
        sweep = _sweep(SweepDirection.BULLISH, sweep_price=150.0)
        result = _calculator().calculate("BUY", entry_price=100.0, atr=2.0, selected_zone=zone, liquidity_sweep=sweep)
        sweep_candidate = next(c for c in result.candidates if c.source == StopLossSource.LIQUIDITY_SWEEP)
        assert sweep_candidate.valid_for_direction is False
        # Overall result should still be valid via the other two candidates.
        assert result.valid is True
        assert result.selected_source != StopLossSource.LIQUIDITY_SWEEP

    def test_all_candidates_retained(self):
        zone = _zone("BUY", lower=90.0, upper=95.0)
        sweep = _sweep(SweepDirection.BULLISH, sweep_price=150.0)
        result = _calculator().calculate("BUY", entry_price=100.0, atr=2.0, selected_zone=zone, liquidity_sweep=sweep)
        assert len(result.candidates) == 3

    def test_deterministic_result(self):
        zone = _zone("BUY", lower=90.0, upper=95.0)
        sweep = _sweep(SweepDirection.BULLISH, sweep_price=80.0)
        result_one = _calculator().calculate("BUY", 100.0, 2.0, zone, sweep)
        result_two = _calculator().calculate("BUY", 100.0, 2.0, zone, sweep)
        assert result_one.selected_stop_loss == result_two.selected_stop_loss
        assert result_one.selected_source == result_two.selected_source

    def test_inputs_not_mutated(self):
        zone = _zone("BUY", lower=90.0, upper=95.0)
        sweep = _sweep(SweepDirection.BULLISH, sweep_price=80.0)
        zone_snapshot = zone.model_copy()
        sweep_snapshot = sweep.model_copy()

        _calculator().calculate("BUY", 100.0, 2.0, zone, sweep)

        assert zone == zone_snapshot
        assert sweep == sweep_snapshot

    def test_constructor_validation(self):
        with pytest.raises(RiskCalculationError):
            DynamicStopLossCalculator(atr_multiplier=0, structural_buffer_ratio=0.0005)
        with pytest.raises(RiskCalculationError):
            DynamicStopLossCalculator(atr_multiplier=1.5, structural_buffer_ratio=-0.01)
