"""
Determines whether trade zones have been invalidated by a closing price
decisively beyond their boundary.
"""

from datetime import datetime
from typing import Optional, Sequence

from app.models.candle import Candle
from app.models.trade_zone import TradeZone, ZoneStatus


class ZoneInvalidationChecker:
    """
    Evaluates whether a TradeZone has been invalidated: a later candle
    closes strictly beyond the zone's boundary on the side that
    invalidates it. Wick-only breaches never invalidate a zone.
    """

    def evaluate(
        self,
        zone: TradeZone,
        candles: Sequence[Candle],
        evaluation_time_utc: Optional[datetime] = None,
    ) -> TradeZone:
        """
        Evaluate invalidation for a single zone and return a replacement
        TradeZone reflecting the result. The input zone is never mutated.

        A BUY zone is invalidated when a later candle closes strictly
        below `zone.lower_price`. A SELL zone is invalidated when a
        later candle closes strictly above `zone.upper_price`. Only
        candles strictly after `zone.created_at` are inspected, and no
        candle at or after `evaluation_time_utc` is ever inspected.
        Invalidation overrides any mitigation status once a qualifying
        close occurs.
        """
        candidate_candles = [
            c
            for c in candles
            if c.timestamp > zone.created_at
            and (evaluation_time_utc is None or c.timestamp <= evaluation_time_utc)
        ]

        for candle in candidate_candles:
            if zone.direction == "BUY" and candle.close < zone.lower_price:
                return zone.model_copy(
                    update={
                        "status": ZoneStatus.INVALIDATED,
                        "invalidation_timestamp": candle.timestamp,
                    }
                )
            if zone.direction == "SELL" and candle.close > zone.upper_price:
                return zone.model_copy(
                    update={
                        "status": ZoneStatus.INVALIDATED,
                        "invalidation_timestamp": candle.timestamp,
                    }
                )

        return zone

    def evaluate_multiple(
        self,
        zones: Sequence[TradeZone],
        candles: Sequence[Candle],
        evaluation_time_utc: Optional[datetime] = None,
    ) -> list[TradeZone]:
        """Evaluate invalidation independently for each zone."""
        return [self.evaluate(zone, candles, evaluation_time_utc) for zone in zones]
