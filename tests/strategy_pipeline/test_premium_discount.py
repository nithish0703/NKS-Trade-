"""
Unit tests for app.strategy_pipeline.premium_discount (Premium/Discount
dealing-range filter).
"""

from datetime import datetime, timezone

from app.market_structure.results import MarketStructureResult, SwingPoint, SwingType, TrendDirection
from app.strategy_pipeline.premium_discount import evaluate_premium_discount_zone

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _swing(price: float, swing_type: SwingType) -> SwingPoint:
    return SwingPoint(
        swing_id=f"swing-{swing_type.value}-{price}",
        symbol="BTC-USDT",
        timeframe="15m",
        timestamp=UTC_NOW,
        candle_index=10,
        swing_type=swing_type,
        price=price,
        left_strength=3,
        right_strength=3,
        confirmed=True,
    )


def _structure(swing_high: float = 150.0, swing_low: float = 100.0) -> MarketStructureResult:
    return MarketStructureResult(
        symbol="BTC-USDT",
        timeframe="15m",
        swings=[],
        classified_swings=[],
        latest_swing_high=_swing(swing_high, SwingType.HIGH),
        latest_swing_low=_swing(swing_low, SwingType.LOW),
        trend_direction=TrendDirection.BULLISH,
        higher_high_count=0,
        higher_low_count=0,
        lower_high_count=0,
        lower_low_count=0,
        equal_high_count=0,
        equal_low_count=0,
    )


class TestEvaluatePremiumDiscountZone:
    def test_buy_from_discount_zone_passes(self):
        result = evaluate_premium_discount_zone(_structure(150.0, 100.0), 110.0, "BUY")
        assert result.passed is True

    def test_buy_from_premium_zone_fails(self):
        result = evaluate_premium_discount_zone(_structure(150.0, 100.0), 140.0, "BUY")
        assert result.passed is False

    def test_sell_from_premium_zone_passes(self):
        result = evaluate_premium_discount_zone(_structure(150.0, 100.0), 140.0, "SELL")
        assert result.passed is True

    def test_sell_from_discount_zone_fails(self):
        result = evaluate_premium_discount_zone(_structure(150.0, 100.0), 110.0, "SELL")
        assert result.passed is False

    def test_price_at_equilibrium_fails_both_directions(self):
        assert evaluate_premium_discount_zone(_structure(150.0, 100.0), 125.0, "BUY").passed is False
        assert evaluate_premium_discount_zone(_structure(150.0, 100.0), 125.0, "SELL").passed is False

    def test_missing_structure_never_passes(self):
        result = evaluate_premium_discount_zone(None, 110.0, "BUY")
        assert result.passed is False

    def test_missing_swings_never_passes(self):
        structure = MarketStructureResult(
            symbol="BTC-USDT",
            timeframe="15m",
            swings=[],
            classified_swings=[],
            latest_swing_high=None,
            latest_swing_low=None,
            trend_direction=TrendDirection.RANGE,
            higher_high_count=0,
            higher_low_count=0,
            lower_high_count=0,
            lower_low_count=0,
            equal_high_count=0,
            equal_low_count=0,
        )
        result = evaluate_premium_discount_zone(structure, 110.0, "BUY")
        assert result.passed is False

    def test_reason_is_human_readable_and_non_empty(self):
        result = evaluate_premium_discount_zone(_structure(150.0, 100.0), 110.0, "BUY")
        assert result.reason
        assert isinstance(result.reason, str)
