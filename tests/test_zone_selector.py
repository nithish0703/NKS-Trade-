"""
Unit tests for app.zones.zone_selector.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType
from app.zones.order_block import ZoneCalculationError
from app.zones.zone_selector import EntryZoneSelector

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _zone(
    zone_type: ZoneType,
    direction: str,
    lower: float,
    upper: float,
    created_at,
    status=ZoneStatus.FRESH,
    mitigation_timestamp=None,
    invalidation_timestamp=None,
    zone_id=None,
) -> TradeZone:
    return TradeZone(
        zone_id=zone_id or f"{zone_type.value}-{direction}-{lower}-{upper}-{created_at.isoformat()}",
        symbol="BTC-USDT",
        timeframe="15m",
        zone_type=zone_type,
        direction=direction,
        lower_price=lower,
        upper_price=upper,
        created_at=created_at,
        source_candle_timestamp=created_at,
        source_candle_index=0,
        status=status,
        mitigation_timestamp=mitigation_timestamp,
        invalidation_timestamp=invalidation_timestamp,
        touch_count=0,
    )


CURRENT_TIME = UTC_NOW + timedelta(hours=1)


class TestPriority:
    def test_fresh_order_block_selected_before_breaker(self):
        order_block = _zone(ZoneType.ORDER_BLOCK, "BUY", 95.0, 100.0, UTC_NOW)
        breaker = _zone(ZoneType.BREAKER_BLOCK, "BUY", 94.0, 99.0, UTC_NOW)

        selected = EntryZoneSelector().select([breaker, order_block], "BUY", 100.0, CURRENT_TIME)
        assert selected.zone_type == ZoneType.ORDER_BLOCK

    def test_fresh_breaker_selected_before_fvg(self):
        breaker = _zone(ZoneType.BREAKER_BLOCK, "BUY", 95.0, 100.0, UTC_NOW)
        fvg = _zone(ZoneType.FAIR_VALUE_GAP, "BUY", 94.0, 99.0, UTC_NOW)

        selected = EntryZoneSelector().select([fvg, breaker], "BUY", 100.0, CURRENT_TIME)
        assert selected.zone_type == ZoneType.BREAKER_BLOCK


class TestExclusions:
    def test_mitigated_order_block_skipped(self):
        mitigated = _zone(
            ZoneType.ORDER_BLOCK,
            "BUY",
            95.0,
            100.0,
            UTC_NOW,
            status=ZoneStatus.MITIGATED,
            mitigation_timestamp=UTC_NOW + timedelta(minutes=5),
        )
        fvg = _zone(ZoneType.FAIR_VALUE_GAP, "BUY", 94.0, 99.0, UTC_NOW)

        selected = EntryZoneSelector().select([mitigated, fvg], "BUY", 100.0, CURRENT_TIME)
        assert selected.zone_type == ZoneType.FAIR_VALUE_GAP

    def test_invalidated_zone_skipped(self):
        invalidated = _zone(
            ZoneType.ORDER_BLOCK,
            "BUY",
            95.0,
            100.0,
            UTC_NOW,
            status=ZoneStatus.INVALIDATED,
            invalidation_timestamp=UTC_NOW + timedelta(minutes=5),
        )
        selected = EntryZoneSelector().select([invalidated], "BUY", 100.0, CURRENT_TIME)
        assert selected is None

    def test_direction_mismatch_skipped(self):
        sell_zone = _zone(ZoneType.ORDER_BLOCK, "SELL", 95.0, 100.0, UTC_NOW)
        selected = EntryZoneSelector().select([sell_zone], "BUY", 100.0, CURRENT_TIME)
        assert selected is None


class TestSameTypeTieBreaking:
    def test_nearest_same_type_zone_selected(self):
        far_zone = _zone(ZoneType.ORDER_BLOCK, "BUY", 80.0, 85.0, UTC_NOW, zone_id="far")
        near_zone = _zone(ZoneType.ORDER_BLOCK, "BUY", 95.0, 99.0, UTC_NOW, zone_id="near")

        selected = EntryZoneSelector().select([far_zone, near_zone], "BUY", 100.0, CURRENT_TIME)
        assert selected.zone_id == "near"

    def test_most_recent_breaks_distance_tie(self):
        older = _zone(
            ZoneType.ORDER_BLOCK, "BUY", 95.0, 99.0, UTC_NOW, zone_id="older"
        )
        newer = _zone(
            ZoneType.ORDER_BLOCK,
            "BUY",
            95.0,
            99.0,
            UTC_NOW + timedelta(minutes=10),
            zone_id="newer",
        )

        selected = EntryZoneSelector().select([older, newer], "BUY", 100.0, CURRENT_TIME)
        assert selected.zone_id == "newer"

    def test_deterministic_id_breaks_remaining_tie(self):
        zone_a = _zone(ZoneType.ORDER_BLOCK, "BUY", 95.0, 99.0, UTC_NOW, zone_id="a-zone")
        zone_b = _zone(ZoneType.ORDER_BLOCK, "BUY", 95.0, 99.0, UTC_NOW, zone_id="b-zone")

        selected = EntryZoneSelector().select([zone_b, zone_a], "BUY", 100.0, CURRENT_TIME)
        assert selected.zone_id == "a-zone"


class TestOtherBehaviors:
    def test_current_price_need_not_be_inside_zone(self):
        zone = _zone(ZoneType.ORDER_BLOCK, "BUY", 50.0, 60.0, UTC_NOW)
        selected = EntryZoneSelector().select([zone], "BUY", 100.0, CURRENT_TIME)
        assert selected is not None
        assert selected.zone_id == zone.zone_id

    def test_no_valid_zone_returns_none(self):
        selected = EntryZoneSelector().select([], "BUY", 100.0, CURRENT_TIME)
        assert selected is None

    def test_invalid_current_price_rejected(self):
        with pytest.raises(ZoneCalculationError):
            EntryZoneSelector().select([], "BUY", 0.0, CURRENT_TIME)

    def test_naive_current_time_rejected(self):
        with pytest.raises(ZoneCalculationError):
            EntryZoneSelector().select([], "BUY", 100.0, datetime(2026, 1, 1))

    def test_no_premium_discount_or_retest_fields_exist(self):
        zone = _zone(ZoneType.ORDER_BLOCK, "BUY", 95.0, 100.0, UTC_NOW)
        zone_fields = set(type(zone).model_fields.keys())
        assert "premium_discount" not in zone_fields
        assert "retest_confirmed" not in zone_fields


class TestValidateZone:
    def test_valid_zone(self):
        zone = _zone(ZoneType.ORDER_BLOCK, "BUY", 95.0, 100.0, UTC_NOW)
        result = EntryZoneSelector().validate_zone(zone, "BUY")
        assert result.valid is True
        assert result.reason == "ZONE_VALID"

    def test_not_fresh_zone(self):
        zone = _zone(
            ZoneType.ORDER_BLOCK,
            "BUY",
            95.0,
            100.0,
            UTC_NOW,
            status=ZoneStatus.MITIGATED,
            mitigation_timestamp=UTC_NOW + timedelta(minutes=1),
        )
        result = EntryZoneSelector().validate_zone(zone, "BUY")
        assert result.valid is False
        assert result.reason == "ZONE_ALREADY_MITIGATED"

    def test_invalidated_zone(self):
        zone = _zone(
            ZoneType.ORDER_BLOCK,
            "BUY",
            95.0,
            100.0,
            UTC_NOW,
            status=ZoneStatus.INVALIDATED,
            invalidation_timestamp=UTC_NOW + timedelta(minutes=1),
        )
        result = EntryZoneSelector().validate_zone(zone, "BUY")
        assert result.valid is False
        assert result.reason == "ZONE_INVALIDATED"

    def test_direction_mismatch(self):
        zone = _zone(ZoneType.ORDER_BLOCK, "SELL", 95.0, 100.0, UTC_NOW)
        result = EntryZoneSelector().validate_zone(zone, "BUY")
        assert result.valid is False
        assert result.reason == "ZONE_DIRECTION_MISMATCH"
