"""
Unit tests for app.zones.dealing_range.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.market_structure.results import SwingPoint, SwingType
from app.zones.dealing_range import (
    DealingRangeCalculationError,
    DealingRangeCalculator,
    DealingRangePosition,
)

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _swing(
    index: int, swing_type: SwingType, price: float, confirmed=True, symbol="BTC-USDT", timeframe="1h"
) -> SwingPoint:
    candle_index = index + 3
    return SwingPoint(
        swing_id=f"swing-{swing_type.value}-{index}",
        symbol=symbol,
        timeframe=timeframe,
        timestamp=UTC_NOW + timedelta(minutes=index),
        candle_index=candle_index,
        swing_type=swing_type,
        price=price,
        left_strength=3,
        right_strength=3,
        confirmed=confirmed,
    )


def _calculator(eq_tol=0.001, mid_tol=0.05) -> DealingRangeCalculator:
    return DealingRangeCalculator(equilibrium_tolerance_ratio=eq_tol, middle_zone_tolerance_ratio=mid_tol)


LOW_SWING = _swing(0, SwingType.LOW, 100.0)
HIGH_SWING = _swing(1, SwingType.HIGH, 200.0)


class TestEquilibrium:
    def test_equilibrium_is_exactly_50_percent(self):
        result = _calculator().calculate([LOW_SWING, HIGH_SWING], 150.0, "BUY")
        assert result.equilibrium_price == pytest.approx(150.0)


class TestDirectionValidity:
    def test_buy_in_discount_is_valid(self):
        result = _calculator().calculate([LOW_SWING, HIGH_SWING], 110.0, "BUY")
        assert result.position == DealingRangePosition.DISCOUNT
        assert result.valid_for_direction is True

    def test_buy_in_premium_is_invalid(self):
        result = _calculator().calculate([LOW_SWING, HIGH_SWING], 190.0, "BUY")
        assert result.position == DealingRangePosition.PREMIUM
        assert result.valid_for_direction is False

    def test_sell_in_premium_is_valid(self):
        result = _calculator().calculate([LOW_SWING, HIGH_SWING], 190.0, "SELL")
        assert result.position == DealingRangePosition.PREMIUM
        assert result.valid_for_direction is True

    def test_sell_in_discount_is_invalid(self):
        result = _calculator().calculate([LOW_SWING, HIGH_SWING], 110.0, "SELL")
        assert result.position == DealingRangePosition.DISCOUNT
        assert result.valid_for_direction is False


class TestPositionClassification:
    def test_price_at_equilibrium(self):
        result = _calculator(eq_tol=0.01).calculate([LOW_SWING, HIGH_SWING], 150.0, "BUY")
        assert result.position == DealingRangePosition.EQUILIBRIUM
        assert result.valid_for_direction is False

    def test_price_in_middle_band(self):
        # range_span=100, middle tolerance 0.05 -> band=5 around 150 => (147, 153),
        # but equilibrium tolerance is tighter (0.001 -> 0.1), so 152 lands in MIDDLE not EQUILIBRIUM.
        result = _calculator(eq_tol=0.001, mid_tol=0.05).calculate([LOW_SWING, HIGH_SWING], 152.0, "BUY")
        assert result.position == DealingRangePosition.MIDDLE
        assert result.valid_for_direction is False

    def test_price_below_range(self):
        result = _calculator().calculate([LOW_SWING, HIGH_SWING], 90.0, "BUY")
        assert result.position == DealingRangePosition.OUTSIDE_RANGE
        assert result.valid_for_direction is False

    def test_price_above_range(self):
        result = _calculator().calculate([LOW_SWING, HIGH_SWING], 210.0, "SELL")
        assert result.position == DealingRangePosition.OUTSIDE_RANGE
        assert result.valid_for_direction is False


class TestSwingSelection:
    def test_missing_swing_high(self):
        result = _calculator().calculate([LOW_SWING], 150.0, "BUY")
        assert result.position == DealingRangePosition.UNKNOWN
        assert result.range_low is None
        assert result.range_high is None

    def test_missing_swing_low(self):
        result = _calculator().calculate([HIGH_SWING], 150.0, "BUY")
        assert result.position == DealingRangePosition.UNKNOWN

    def test_unconfirmed_swing_ignored(self):
        unconfirmed_high = _swing(1, SwingType.HIGH, 200.0, confirmed=False)
        result = _calculator().calculate([LOW_SWING, unconfirmed_high], 150.0, "BUY")
        assert result.position == DealingRangePosition.UNKNOWN

    def test_latest_valid_swing_high_and_low_selected(self):
        old_low = _swing(0, SwingType.LOW, 100.0)
        new_low = _swing(2, SwingType.LOW, 120.0)
        old_high = _swing(1, SwingType.HIGH, 200.0)
        new_high = _swing(3, SwingType.HIGH, 210.0)

        result = _calculator().calculate([old_low, new_low, old_high, new_high], 165.0, "BUY")
        assert result.source_swing_low.swing_id == new_low.swing_id
        assert result.source_swing_high.swing_id == new_high.swing_id

    def test_invalid_range_rejected(self):
        # Latest low is above the latest high -> not a valid dealing range.
        inverted_low = _swing(2, SwingType.LOW, 250.0)
        inverted_high = _swing(0, SwingType.HIGH, 200.0)
        result = _calculator().calculate([inverted_low, inverted_high], 150.0, "BUY")
        assert result.position == DealingRangePosition.UNKNOWN

    def test_input_swings_not_mutated(self):
        swings = [LOW_SWING, HIGH_SWING]
        snapshot = [s.model_copy() for s in swings]
        _calculator().calculate(swings, 150.0, "BUY")
        assert swings == snapshot


class TestNoTradeFields:
    def test_no_entry_sl_tp_risk_score_or_signal_fields(self):
        result = _calculator().calculate([LOW_SWING, HIGH_SWING], 150.0, "BUY")
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


class TestConstructorValidation:
    def test_negative_equilibrium_tolerance_rejected(self):
        with pytest.raises(DealingRangeCalculationError):
            DealingRangeCalculator(equilibrium_tolerance_ratio=-0.01, middle_zone_tolerance_ratio=0.05)

    def test_negative_middle_tolerance_rejected(self):
        with pytest.raises(DealingRangeCalculationError):
            DealingRangeCalculator(equilibrium_tolerance_ratio=0.001, middle_zone_tolerance_ratio=-0.01)

    def test_middle_tolerance_over_half_rejected(self):
        with pytest.raises(DealingRangeCalculationError):
            DealingRangeCalculator(equilibrium_tolerance_ratio=0.001, middle_zone_tolerance_ratio=0.6)
