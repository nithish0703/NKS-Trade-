"""
Unit tests for app.validators.volume_confirmation.VolumeConfirmationValidator.
"""

from datetime import datetime, timedelta, timezone

from app.market_structure.shift_results import BreakDirection, DisplacementResult
from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType
from app.validators.volume_confirmation import VolumeConfirmationValidator
from app.zones.retest_results import RejectionCandleDirection, RetestResult, RetestStatus

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _displacement(confirmed=True, volume_confirmed=True) -> DisplacementResult:
    return DisplacementResult(
        symbol="BTC-USDT",
        timeframe="15m",
        candle_timestamp=UTC_NOW,
        candle_index=5,
        direction=BreakDirection.BULLISH,
        body_ratio=0.8,
        candle_range=10.0,
        body_size=8.0,
        close_location_value=0.9,
        volume_confirmed=volume_confirmed,
        strong_close=True,
        confirmed=confirmed,
        reason="test",
    )


def _zone() -> TradeZone:
    return TradeZone(
        zone_id="zone-1",
        symbol="BTC-USDT",
        timeframe="15m",
        zone_type=ZoneType.ORDER_BLOCK,
        direction="BUY",
        lower_price=95.0,
        upper_price=100.0,
        created_at=UTC_NOW,
        source_candle_timestamp=UTC_NOW,
        source_candle_index=0,
        status=ZoneStatus.FRESH,
        touch_count=0,
    )


def _retest(volume_confirmed=True) -> RetestResult:
    return RetestResult(
        retest_id="retest-1",
        symbol="BTC-USDT",
        timeframe="15m",
        zone=_zone(),
        retest_candle_timestamp=UTC_NOW + timedelta(minutes=1),
        retest_candle_index=1,
        interaction_price=99.0,
        rejection_direction=RejectionCandleDirection.BULLISH,
        body_ratio=0.7,
        upper_wick_ratio=0.1,
        lower_wick_ratio=0.3,
        close_location_value=0.8,
        rejection_strength=0.7,
        volume=150.0,
        volume_ema=100.0,
        volume_ratio=1.5,
        volume_confirmed=volume_confirmed,
        zone_interaction_confirmed=True,
        rejection_candle_confirmed=True,
        status=RetestStatus.CONFIRMED if volume_confirmed else RetestStatus.REJECTED,
        reason="test retest",
    )


class TestVolumeConfirmationValidator:
    def test_displacement_and_retest_volume_confirmation_passes(self):
        result = VolumeConfirmationValidator().validate(_displacement(), _retest())
        assert result.passed is True

    def test_missing_displacement_fails(self):
        result = VolumeConfirmationValidator().validate(None, _retest())
        assert result.passed is False
        assert result.rejection_code == "VOLUME_DATA_MISSING"

    def test_unconfirmed_displacement_volume_fails(self):
        result = VolumeConfirmationValidator().validate(
            _displacement(volume_confirmed=False), _retest()
        )
        assert result.passed is False
        assert result.rejection_code == "DISPLACEMENT_VOLUME_NOT_CONFIRMED"

    def test_missing_retest_fails(self):
        result = VolumeConfirmationValidator().validate(_displacement(), None)
        assert result.passed is False
        assert result.rejection_code == "VOLUME_DATA_MISSING"

    def test_retest_volume_failure_fails(self):
        result = VolumeConfirmationValidator().validate(
            _displacement(), _retest(volume_confirmed=False)
        )
        assert result.passed is False
        assert result.rejection_code == "RETEST_VOLUME_NOT_CONFIRMED"

    def test_score_remains_zero(self):
        passing = VolumeConfirmationValidator().validate(_displacement(), _retest())
        assert passing.score == 0.0
        failing = VolumeConfirmationValidator().validate(None, None)
        assert failing.score == 0.0
