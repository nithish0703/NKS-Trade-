"""
Unit tests for app.validators.premium_discount.PremiumDiscountValidator.
"""

from datetime import datetime, timedelta, timezone

from app.market_structure.results import SwingPoint, SwingType
from app.validators.premium_discount import PremiumDiscountValidator
from app.zones.dealing_range import DealingRangeCalculator

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _swing(index: int, swing_type: SwingType, price: float, confirmed=True) -> SwingPoint:
    candle_index = index + 3
    return SwingPoint(
        swing_id=f"swing-{swing_type.value}-{index}",
        symbol="BTC-USDT",
        timeframe="1h",
        timestamp=UTC_NOW + timedelta(minutes=index),
        candle_index=candle_index,
        swing_type=swing_type,
        price=price,
        left_strength=3,
        right_strength=3,
        confirmed=confirmed,
    )


LOW_SWING = _swing(0, SwingType.LOW, 100.0)
HIGH_SWING = _swing(1, SwingType.HIGH, 200.0)


def _calculator() -> DealingRangeCalculator:
    return DealingRangeCalculator(equilibrium_tolerance_ratio=0.001, middle_zone_tolerance_ratio=0.05)


class TestPremiumDiscountValidator:
    def test_buy_discount_passes(self):
        result = _calculator().calculate([LOW_SWING, HIGH_SWING], 110.0, "BUY")
        validation = PremiumDiscountValidator().validate(result, "BUY")
        assert validation.passed is True
        assert validation.score == 0.0

    def test_buy_premium_fails(self):
        result = _calculator().calculate([LOW_SWING, HIGH_SWING], 190.0, "BUY")
        validation = PremiumDiscountValidator().validate(result, "BUY")
        assert validation.passed is False
        assert validation.rejection_code == "BUY_NOT_IN_DISCOUNT"

    def test_sell_premium_passes(self):
        result = _calculator().calculate([LOW_SWING, HIGH_SWING], 190.0, "SELL")
        validation = PremiumDiscountValidator().validate(result, "SELL")
        assert validation.passed is True

    def test_sell_discount_fails(self):
        result = _calculator().calculate([LOW_SWING, HIGH_SWING], 110.0, "SELL")
        validation = PremiumDiscountValidator().validate(result, "SELL")
        assert validation.passed is False
        assert validation.rejection_code == "SELL_NOT_IN_PREMIUM"

    def test_equilibrium_fails(self):
        result = _calculator().calculate([LOW_SWING, HIGH_SWING], 150.0, "BUY")
        validation = PremiumDiscountValidator().validate(result, "BUY")
        assert validation.passed is False
        assert validation.rejection_code == "PRICE_AT_EQUILIBRIUM"

    def test_middle_range_fails(self):
        result = _calculator().calculate([LOW_SWING, HIGH_SWING], 152.0, "BUY")
        validation = PremiumDiscountValidator().validate(result, "BUY")
        assert validation.passed is False
        assert validation.rejection_code == "PRICE_IN_MIDDLE_OF_RANGE"

    def test_outside_range_fails(self):
        result = _calculator().calculate([LOW_SWING, HIGH_SWING], 90.0, "BUY")
        validation = PremiumDiscountValidator().validate(result, "BUY")
        assert validation.passed is False
        assert validation.rejection_code == "PRICE_OUTSIDE_DEALING_RANGE"

    def test_missing_range_fails(self):
        result = _calculator().calculate([LOW_SWING], 110.0, "BUY")
        validation = PremiumDiscountValidator().validate(result, "BUY")
        assert validation.passed is False
        assert validation.rejection_code == "DEALING_RANGE_MISSING"

    def test_direction_mismatch_fails(self):
        # Position is valid geometrically, but expected_direction passed
        # to validate() disagrees with the direction baked into `result`.
        result = _calculator().calculate([LOW_SWING, HIGH_SWING], 110.0, "BUY")
        validation = PremiumDiscountValidator().validate(result, "SELL")
        assert validation.passed is False
        assert validation.rejection_code == "SELL_NOT_IN_PREMIUM"

    def test_score_remains_zero(self):
        passing = _calculator().calculate([LOW_SWING, HIGH_SWING], 110.0, "BUY")
        passing_validation = PremiumDiscountValidator().validate(passing, "BUY")
        assert passing_validation.score == 0.0

        failing = _calculator().calculate([LOW_SWING], 110.0, "BUY")
        failing_validation = PremiumDiscountValidator().validate(failing, "BUY")
        assert failing_validation.score == 0.0

    def test_no_signal_generated(self):
        result = _calculator().calculate([LOW_SWING, HIGH_SWING], 110.0, "BUY")
        validation = PremiumDiscountValidator().validate(result, "BUY")
        validation_fields = set(type(validation).model_fields.keys())
        assert "signal_type" not in validation_fields
        assert "entry_price" not in validation_fields
