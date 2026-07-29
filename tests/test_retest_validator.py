"""
Unit tests for app.validators.retest_confirmation.RetestConfirmationValidator.
"""

from datetime import datetime, timedelta, timezone

from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType
from app.validators.retest_confirmation import RetestConfirmationValidator
from app.zones.retest_results import RejectionCandleDirection, RetestResult, RetestStatus

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _zone(direction="BUY", status=ZoneStatus.FRESH, zone_id="zone-1", **overrides) -> TradeZone:
    fields = dict(
        zone_id=zone_id,
        symbol="BTC-USDT",
        timeframe="15m",
        zone_type=ZoneType.ORDER_BLOCK,
        direction=direction,
        lower_price=95.0,
        upper_price=100.0,
        created_at=UTC_NOW,
        source_candle_timestamp=UTC_NOW,
        source_candle_index=0,
        status=status,
        touch_count=0,
    )
    fields.update(overrides)
    return TradeZone(**fields)


def _retest(
    zone: TradeZone,
    status=RetestStatus.CONFIRMED,
    zone_interaction_confirmed=True,
    rejection_candle_confirmed=True,
    volume_confirmed=True,
) -> RetestResult:
    return RetestResult(
        retest_id="retest-1",
        symbol="BTC-USDT",
        timeframe="15m",
        zone=zone,
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
        zone_interaction_confirmed=zone_interaction_confirmed,
        rejection_candle_confirmed=rejection_candle_confirmed,
        status=status,
        reason="test retest",
    )


class TestRetestConfirmationValidator:
    def test_valid_confirmed_retest_passes(self):
        zone = _zone()
        retest = _retest(zone)
        result = RetestConfirmationValidator().validate(retest, "BUY", zone)
        assert result.passed is True
        assert result.score == 0.0

    def test_missing_retest_fails(self):
        zone = _zone()
        result = RetestConfirmationValidator().validate(None, "BUY", zone)
        assert result.passed is False
        assert result.rejection_code == "RETEST_MISSING"

    def test_rejected_retest_fails(self):
        zone = _zone()
        retest = _retest(
            zone,
            status=RetestStatus.REJECTED,
            zone_interaction_confirmed=False,
            rejection_candle_confirmed=False,
            volume_confirmed=False,
        )
        result = RetestConfirmationValidator().validate(retest, "BUY", zone)
        assert result.passed is False
        assert result.rejection_code == "RETEST_NOT_CONFIRMED"

    def test_zone_id_mismatch_fails(self):
        zone = _zone(zone_id="zone-1")
        other_zone = _zone(zone_id="zone-2")
        retest = _retest(zone)
        result = RetestConfirmationValidator().validate(retest, "BUY", other_zone)
        assert result.passed is False
        assert result.rejection_code == "RETEST_ZONE_MISMATCH"

    def test_direction_mismatch_fails(self):
        zone = _zone(direction="BUY")
        retest = _retest(zone)
        result = RetestConfirmationValidator().validate(retest, "SELL", zone)
        assert result.passed is False
        assert result.rejection_code == "RETEST_DIRECTION_MISMATCH"

    def test_missing_interaction_fails(self):
        zone = _zone()
        retest = _retest(zone, zone_interaction_confirmed=False, rejection_candle_confirmed=False, volume_confirmed=False, status=RetestStatus.REJECTED)
        result = RetestConfirmationValidator().validate(retest, "BUY", zone)
        assert result.passed is False
        assert result.rejection_code == "RETEST_NOT_CONFIRMED"

    def test_missing_rejection_fails(self):
        zone = _zone()
        retest = _retest(zone, rejection_candle_confirmed=False, volume_confirmed=False, status=RetestStatus.REJECTED)
        result = RetestConfirmationValidator().validate(retest, "BUY", zone)
        assert result.passed is False
        assert result.rejection_code == "RETEST_NOT_CONFIRMED"

    def test_missing_volume_confirmation_fails(self):
        zone = _zone()
        retest = _retest(zone, volume_confirmed=False, status=RetestStatus.REJECTED)
        result = RetestConfirmationValidator().validate(retest, "BUY", zone)
        assert result.passed is False
        assert result.rejection_code == "RETEST_NOT_CONFIRMED"

    def test_mitigated_selected_zone_fails(self):
        zone = _zone()
        retest = _retest(zone)
        mitigated_zone = zone.model_copy(
            update={"status": ZoneStatus.MITIGATED, "mitigation_timestamp": UTC_NOW + timedelta(minutes=1)}
        )
        result = RetestConfirmationValidator().validate(retest, "BUY", mitigated_zone)
        assert result.passed is False
        assert result.rejection_code == "RETEST_ZONE_MITIGATED"

    def test_invalidated_selected_zone_fails(self):
        zone = _zone()
        retest = _retest(zone)
        invalidated_zone = zone.model_copy(
            update={"status": ZoneStatus.INVALIDATED, "invalidation_timestamp": UTC_NOW + timedelta(minutes=1)}
        )
        result = RetestConfirmationValidator().validate(retest, "BUY", invalidated_zone)
        assert result.passed is False
        assert result.rejection_code == "RETEST_ZONE_INVALIDATED"

    def test_score_remains_zero(self):
        zone = _zone()
        retest = _retest(zone)
        passing = RetestConfirmationValidator().validate(retest, "BUY", zone)
        assert passing.score == 0.0

        failing = RetestConfirmationValidator().validate(None, "BUY", zone)
        assert failing.score == 0.0

    def test_no_signal_generation(self):
        zone = _zone()
        retest = _retest(zone)
        result = RetestConfirmationValidator().validate(retest, "BUY", zone)
        result_fields = set(type(result).model_fields.keys())
        assert "signal_type" not in result_fields
        assert "entry_price" not in result_fields
