"""
Unit tests for app.zones.setup_confirmation.ZoneSetupConfirmationCalculator.
"""

from datetime import datetime, timedelta, timezone

from app.market_structure.results import SwingPoint, SwingType
from app.models.candle import Candle
from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType
from app.validators.premium_discount import PremiumDiscountValidator
from app.validators.retest_confirmation import RetestConfirmationValidator
from app.zones.dealing_range import DealingRangeCalculator
from app.zones.retest_confirmation import RetestConfirmationDetector
from app.zones.setup_confirmation import ZoneSetupConfirmationCalculator

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _swing(index: int, swing_type: SwingType, price: float) -> SwingPoint:
    candle_index = index + 3
    return SwingPoint(
        swing_id=f"swing-{swing_type.value}-{index}",
        symbol="BTC-USDT",
        timeframe="1h",
        timestamp=UTC_NOW - timedelta(hours=10) + timedelta(minutes=index),
        candle_index=candle_index,
        swing_type=swing_type,
        price=price,
        left_strength=3,
        right_strength=3,
        confirmed=True,
    )


def _candle(index: int, open_: float, high: float, low: float, close: float, volume=100.0) -> Candle:
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        symbol="BTC-USDT",
        timeframe="15m",
    )


def _zone(direction: str, lower: float, upper: float, created_at) -> TradeZone:
    return TradeZone(
        zone_id="zone-1",
        symbol="BTC-USDT",
        timeframe="15m",
        zone_type=ZoneType.ORDER_BLOCK,
        direction=direction,
        lower_price=lower,
        upper_price=upper,
        created_at=created_at,
        source_candle_timestamp=created_at,
        source_candle_index=0,
        status=ZoneStatus.FRESH,
        touch_count=0,
    )


def _make_calculator(min_wick=0.15) -> ZoneSetupConfirmationCalculator:
    return ZoneSetupConfirmationCalculator(
        dealing_range_calculator=DealingRangeCalculator(
            equilibrium_tolerance_ratio=0.001, middle_zone_tolerance_ratio=0.05
        ),
        premium_discount_validator=PremiumDiscountValidator(),
        retest_confirmation_detector=RetestConfirmationDetector(
            minimum_rejection_body_ratio=0.50,
            bullish_close_location_minimum=0.65,
            bearish_close_location_maximum=0.35,
            minimum_rejection_wick_ratio=min_wick,
            maximum_confirmation_candles=3,
        ),
        retest_confirmation_validator=RetestConfirmationValidator(),
    )


CURRENT_TIME = UTC_NOW + timedelta(hours=1)


class TestZoneSetupConfirmation:
    def test_complete_valid_buy_setup_confirmation(self):
        low_swing = _swing(0, SwingType.LOW, 100.0)
        high_swing = _swing(1, SwingType.HIGH, 200.0)
        zone = _zone("BUY", lower=108.0, upper=112.0, created_at=UTC_NOW)  # discount side (below 150 equilibrium)
        retest_candle = _candle(1, open_=110, high=118, low=106, close=117, volume=200)

        result = _make_calculator().calculate(
            [retest_candle],
            [low_swing, high_swing],
            zone,
            "BUY",
            [100.0],
            current_price=115.0,
            current_time_utc=CURRENT_TIME,
        )
        assert result.confirmed is True
        assert result.premium_discount_validation.passed is True
        assert result.retest_validation.passed is True

    def test_complete_valid_sell_setup_confirmation(self):
        low_swing = _swing(0, SwingType.LOW, 100.0)
        high_swing = _swing(1, SwingType.HIGH, 200.0)
        zone = _zone("SELL", lower=188.0, upper=192.0, created_at=UTC_NOW)  # premium side
        retest_candle = _candle(1, open_=190, high=194, low=182, close=183, volume=200)

        result = _make_calculator().calculate(
            [retest_candle],
            [low_swing, high_swing],
            zone,
            "SELL",
            [100.0],
            current_price=185.0,
            current_time_utc=CURRENT_TIME,
        )
        assert result.confirmed is True

    def test_premium_discount_failure_stops_retest_validation(self):
        low_swing = _swing(0, SwingType.LOW, 100.0)
        high_swing = _swing(1, SwingType.HIGH, 200.0)
        zone = _zone("BUY", lower=188.0, upper=192.0, created_at=UTC_NOW)  # premium, invalid for BUY
        retest_candle = _candle(1, open_=190, high=196, low=186, close=195, volume=200)

        result = _make_calculator().calculate(
            [retest_candle],
            [low_swing, high_swing],
            zone,
            "BUY",
            [100.0],
            current_price=190.0,  # premium zone -> invalid for BUY
            current_time_utc=CURRENT_TIME,
        )
        assert result.confirmed is False
        assert result.premium_discount_validation.passed is False
        assert result.retest is None
        assert result.retest_validation is None

    def test_missing_retest_causes_confirmed_false(self):
        low_swing = _swing(0, SwingType.LOW, 100.0)
        high_swing = _swing(1, SwingType.HIGH, 200.0)
        zone = _zone("BUY", lower=108.0, upper=112.0, created_at=UTC_NOW)
        far_away_candle = _candle(1, open_=300, high=301, low=299, close=300, volume=200)

        result = _make_calculator().calculate(
            [far_away_candle],
            [low_swing, high_swing],
            zone,
            "BUY",
            [100.0],
            current_price=115.0,
            current_time_utc=CURRENT_TIME,
        )
        assert result.confirmed is False
        assert result.retest_validation.passed is False

    def test_invalid_retest_causes_confirmed_false(self):
        low_swing = _swing(0, SwingType.LOW, 100.0)
        high_swing = _swing(1, SwingType.HIGH, 200.0)
        zone = _zone("BUY", lower=108.0, upper=112.0, created_at=UTC_NOW)
        weak_candle = _candle(1, open_=110, high=111, low=109, close=110.2, volume=50)  # weak, no volume

        result = _make_calculator().calculate(
            [weak_candle],
            [low_swing, high_swing],
            zone,
            "BUY",
            [100.0],
            current_price=115.0,
            current_time_utc=CURRENT_TIME,
        )
        assert result.confirmed is False

    def test_final_confirmation_requires_both_validators(self):
        low_swing = _swing(0, SwingType.LOW, 100.0)
        high_swing = _swing(1, SwingType.HIGH, 200.0)
        zone = _zone("BUY", lower=108.0, upper=112.0, created_at=UTC_NOW)
        retest_candle = _candle(1, open_=110, high=118, low=106, close=117, volume=200)

        result = _make_calculator().calculate(
            [retest_candle],
            [low_swing, high_swing],
            zone,
            "BUY",
            [100.0],
            current_price=115.0,
            current_time_utc=CURRENT_TIME,
        )
        assert result.confirmed == (
            result.premium_discount_validation.passed
            and result.retest_validation is not None
            and result.retest_validation.passed
        )

    def test_current_time_respected(self):
        low_swing = _swing(0, SwingType.LOW, 100.0)
        high_swing = _swing(1, SwingType.HIGH, 200.0)
        zone = _zone("BUY", lower=108.0, upper=112.0, created_at=UTC_NOW)
        retest_candle = _candle(5, open_=110, high=118, low=106, close=117, volume=200)
        early_evaluation_time = UTC_NOW + timedelta(minutes=1)  # before the retest candle

        result = _make_calculator().calculate(
            [retest_candle],
            [low_swing, high_swing],
            zone,
            "BUY",
            [100.0],
            current_price=115.0,
            current_time_utc=early_evaluation_time,
        )
        assert result.confirmed is False

    def test_input_data_not_mutated(self):
        low_swing = _swing(0, SwingType.LOW, 100.0)
        high_swing = _swing(1, SwingType.HIGH, 200.0)
        zone = _zone("BUY", lower=108.0, upper=112.0, created_at=UTC_NOW)
        retest_candle = _candle(1, open_=110, high=118, low=106, close=117, volume=200)

        candles = [retest_candle]
        swings = [low_swing, high_swing]
        candles_snapshot = [c.model_copy() for c in candles]
        swings_snapshot = [s.model_copy() for s in swings]
        zone_snapshot = zone.model_copy()

        _make_calculator().calculate(
            candles, swings, zone, "BUY", [100.0], current_price=115.0, current_time_utc=CURRENT_TIME
        )

        assert candles == candles_snapshot
        assert swings == swings_snapshot
        assert zone == zone_snapshot

    def test_no_risk_scoring_or_signal_generation_fields(self):
        low_swing = _swing(0, SwingType.LOW, 100.0)
        high_swing = _swing(1, SwingType.HIGH, 200.0)
        zone = _zone("BUY", lower=108.0, upper=112.0, created_at=UTC_NOW)
        retest_candle = _candle(1, open_=110, high=118, low=106, close=117, volume=200)

        result = _make_calculator().calculate(
            [retest_candle],
            [low_swing, high_swing],
            zone,
            "BUY",
            [100.0],
            current_price=115.0,
            current_time_utc=CURRENT_TIME,
        )
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
