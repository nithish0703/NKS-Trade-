"""
Selects the most relevant zone(s) for trade evaluation.
"""

from datetime import datetime, timezone
from typing import Optional, Sequence

from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType

from app.zones.order_block import ZoneCalculationError
from app.zones.results import ZoneValidationResult

_TYPE_PRIORITY = {
    ZoneType.ORDER_BLOCK: 0,
    ZoneType.BREAKER_BLOCK: 1,
    ZoneType.FAIR_VALUE_GAP: 2,
}


class EntryZoneSelector:
    """
    Selects the single highest-priority valid entry zone from a set of
    candidate zones. Does not calculate entries, does not apply
    premium/discount filtering, and does not perform retest confirmation.
    """

    def select(
        self,
        zones: Sequence[TradeZone],
        expected_direction: str,
        current_price: float,
        current_time_utc: datetime,
    ) -> Optional[TradeZone]:
        """
        Select the highest-priority valid zone, or None if no valid zone
        exists.

        Priority: ORDER_BLOCK, then BREAKER_BLOCK, then FAIR_VALUE_GAP.
        Within the same type: nearest to current price, then most
        recently created, then zone_id for determinism.
        """
        if current_price <= 0:
            raise ZoneCalculationError("current_price must be positive.")
        if current_time_utc.tzinfo is None or current_time_utc.tzinfo.utcoffset(current_time_utc) is None:
            raise ZoneCalculationError("current_time_utc must be timezone-aware UTC.")
        if current_time_utc.utcoffset() != timezone.utc.utcoffset(current_time_utc):
            raise ZoneCalculationError("current_time_utc must be in UTC.")

        candidates = [
            zone
            for zone in zones
            if zone.status == ZoneStatus.FRESH
            and zone.mitigation_timestamp is None
            and zone.invalidation_timestamp is None
            and zone.direction == expected_direction
            and zone.created_at < current_time_utc
        ]

        if not candidates:
            return None

        def sort_key(zone: TradeZone):
            distance = (
                abs(current_price - zone.upper_price)
                if zone.direction == "BUY"
                else abs(current_price - zone.lower_price)
            )
            return (
                _TYPE_PRIORITY[zone.zone_type],
                distance,
                -zone.created_at.timestamp(),
                zone.zone_id,
            )

        return min(candidates, key=sort_key)

    def validate_zone(self, zone: TradeZone, expected_direction: str) -> ZoneValidationResult:
        """Validate a single zone as a candidate entry zone, with an explicit reason."""
        fresh = zone.status == ZoneStatus.FRESH
        unmitigated = zone.mitigation_timestamp is None
        not_invalidated = zone.invalidation_timestamp is None
        direction_aligned = zone.direction == expected_direction

        if zone.status != ZoneStatus.FRESH:
            if zone.status == ZoneStatus.INVALIDATED:
                reason = "ZONE_INVALIDATED"
            else:
                reason = "ZONE_ALREADY_MITIGATED"
            valid = False
        elif not unmitigated:
            reason = "ZONE_ALREADY_MITIGATED"
            valid = False
        elif not not_invalidated:
            reason = "ZONE_INVALIDATED"
            valid = False
        elif not direction_aligned:
            reason = "ZONE_DIRECTION_MISMATCH"
            valid = False
        else:
            reason = "ZONE_VALID"
            valid = True

        return ZoneValidationResult(
            zone=zone,
            fresh=fresh,
            unmitigated=unmitigated and not_invalidated,
            direction_aligned=direction_aligned,
            valid=valid,
            reason=reason,
        )
