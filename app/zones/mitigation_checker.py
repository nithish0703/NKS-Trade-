"""
Checks whether zones have been mitigated by price action.
"""

from datetime import datetime
from typing import Optional, Sequence

from app.models.candle import Candle
from app.models.trade_zone import TradeZone, ZoneStatus

from app.zones.order_block import ZoneCalculationError


class ZoneMitigationChecker:
    """
    Evaluates whether a TradeZone has been mitigated by subsequent price
    action, using simple geometric overlap (or full-mitigation) rules.
    """

    def __init__(self, full_mitigation_required: bool, touch_tolerance_ratio: float) -> None:
        if touch_tolerance_ratio < 0:
            raise ZoneCalculationError(
                f"touch_tolerance_ratio cannot be negative, got {touch_tolerance_ratio}."
            )
        self._full_mitigation_required = full_mitigation_required
        self._touch_tolerance_ratio = touch_tolerance_ratio

    def evaluate(
        self,
        zone: TradeZone,
        candles: Sequence[Candle],
        evaluation_time_utc: Optional[datetime] = None,
    ) -> TradeZone:
        """
        Evaluate mitigation for a single zone and return a replacement
        TradeZone reflecting the result. The input zone is never mutated.

        Only candles strictly after `zone.created_at` are inspected (the
        creation candle itself cannot mitigate its own zone), and no
        candle at or after `evaluation_time_utc` is ever inspected.
        """
        candidate_candles = [
            c
            for c in candles
            if c.timestamp > zone.created_at
            and (evaluation_time_utc is None or c.timestamp <= evaluation_time_utc)
        ]

        qualifying_timestamps: list[datetime] = []

        for candle in candidate_candles:
            overlaps = candle.low <= zone.upper_price and candle.high >= zone.lower_price
            if not overlaps:
                continue

            if self._full_mitigation_required:
                if zone.direction == "BUY":
                    fully_mitigated = candle.low <= zone.lower_price
                else:
                    fully_mitigated = candle.high >= zone.upper_price
                if not fully_mitigated:
                    continue

            qualifying_timestamps.append(candle.timestamp)

        if not qualifying_timestamps:
            return zone

        return zone.model_copy(
            update={
                "status": ZoneStatus.MITIGATED,
                "mitigation_timestamp": qualifying_timestamps[0],
                "touch_count": len(qualifying_timestamps),
            }
        )

    def evaluate_multiple(
        self,
        zones: Sequence[TradeZone],
        candles: Sequence[Candle],
        evaluation_time_utc: Optional[datetime] = None,
    ) -> list[TradeZone]:
        """Evaluate mitigation independently for each zone."""
        return [self.evaluate(zone, candles, evaluation_time_utc) for zone in zones]
