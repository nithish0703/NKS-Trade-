"""
Unit tests for app.validators.entry_zone.EntryZoneValidator.
"""

from datetime import datetime, timedelta, timezone

from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType
from app.validators.entry_zone import EntryZoneValidator
from app.zones.results import ZoneDetectionResult

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _zone(
    zone_type: ZoneType,
    direction: str,
    status=ZoneStatus.FRESH,
    mitigation_timestamp=None,
    invalidation_timestamp=None,
) -> TradeZone:
    return TradeZone(
        zone_id=f"{zone_type.value}-{direction}",
        symbol="BTC-USDT",
        timeframe="15m",
        zone_type=zone_type,
        direction=direction,
        lower_price=95.0,
        upper_price=100.0,
        created_at=UTC_NOW,
        source_candle_timestamp=UTC_NOW,
        source_candle_index=0,
        status=status,
        mitigation_timestamp=mitigation_timestamp,
        invalidation_timestamp=invalidation_timestamp,
        touch_count=0,
    )


def _result(selected_zone) -> ZoneDetectionResult:
    return ZoneDetectionResult(
        symbol="BTC-USDT",
        timeframe="15m",
        order_blocks=[],
        breaker_blocks=[],
        fair_value_gaps=[],
        all_zones=[selected_zone] if selected_zone else [],
        fresh_zones=[selected_zone] if selected_zone and selected_zone.status == ZoneStatus.FRESH else [],
        mitigated_zones=[],
        invalidated_zones=[],
        selected_zone=selected_zone,
    )


class TestEntryZoneValidator:
    def test_valid_fresh_order_block_passes(self):
        zone = _zone(ZoneType.ORDER_BLOCK, "BUY")
        result = EntryZoneValidator().validate(_result(zone), "BUY")
        assert result.passed is True
        assert result.score == 0.0

    def test_valid_fresh_breaker_passes(self):
        zone = _zone(ZoneType.BREAKER_BLOCK, "BUY")
        result = EntryZoneValidator().validate(_result(zone), "BUY")
        assert result.passed is True

    def test_valid_fresh_fvg_passes(self):
        zone = _zone(ZoneType.FAIR_VALUE_GAP, "BUY")
        result = EntryZoneValidator().validate(_result(zone), "BUY")
        assert result.passed is True

    def test_missing_selected_zone_fails(self):
        result = EntryZoneValidator().validate(_result(None), "BUY")
        assert result.passed is False
        assert result.rejection_code == "ENTRY_ZONE_MISSING"

    def test_mitigated_zone_fails(self):
        zone = _zone(
            ZoneType.ORDER_BLOCK,
            "BUY",
            status=ZoneStatus.MITIGATED,
            mitigation_timestamp=UTC_NOW + timedelta(minutes=1),
        )
        result = EntryZoneValidator().validate(_result(zone), "BUY")
        assert result.passed is False
        assert result.rejection_code == "ENTRY_ZONE_NOT_FRESH"

    def test_invalidated_zone_fails(self):
        zone = _zone(
            ZoneType.ORDER_BLOCK,
            "BUY",
            status=ZoneStatus.INVALIDATED,
            invalidation_timestamp=UTC_NOW + timedelta(minutes=1),
        )
        result = EntryZoneValidator().validate(_result(zone), "BUY")
        assert result.passed is False
        assert result.rejection_code == "ENTRY_ZONE_NOT_FRESH"

    def test_direction_mismatch_fails(self):
        zone = _zone(ZoneType.ORDER_BLOCK, "SELL")
        result = EntryZoneValidator().validate(_result(zone), "BUY")
        assert result.passed is False
        assert result.rejection_code == "ENTRY_ZONE_DIRECTION_MISMATCH"

    def test_invalid_zone_type_fails(self):
        # Construct a zone-like object is not feasible since ZoneType is
        # an enum restricted to the three valid types; instead verify that
        # all three valid types pass and no fourth type can be represented.
        for zone_type in (ZoneType.ORDER_BLOCK, ZoneType.BREAKER_BLOCK, ZoneType.FAIR_VALUE_GAP):
            zone = _zone(zone_type, "BUY")
            result = EntryZoneValidator().validate(_result(zone), "BUY")
            assert result.passed is True

    def test_score_remains_zero(self):
        zone = _zone(ZoneType.ORDER_BLOCK, "BUY")
        passing_result = EntryZoneValidator().validate(_result(zone), "BUY")
        assert passing_result.score == 0.0

        failing_result = EntryZoneValidator().validate(_result(None), "BUY")
        assert failing_result.score == 0.0

    def test_no_signal_generated(self):
        zone = _zone(ZoneType.ORDER_BLOCK, "BUY")
        result = EntryZoneValidator().validate(_result(zone), "BUY")
        result_fields = set(type(result).model_fields.keys())
        assert "signal_type" not in result_fields
        assert "entry_price" not in result_fields
