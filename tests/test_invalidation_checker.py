"""
Unit tests for app.zones.invalidation_checker.
"""

from datetime import datetime, timedelta, timezone

from app.models.candle import Candle
from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType
from app.zones.invalidation_checker import ZoneInvalidationChecker

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
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


class TestBuyZoneInvalidation:
    def test_close_below_lower_boundary_invalidates(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candles = [_candle(1, 96, 97, 90, 92)]  # closes strictly below 95

        result = ZoneInvalidationChecker().evaluate(zone, candles)
        assert result.status == ZoneStatus.INVALIDATED
        assert result.invalidation_timestamp == candles[0].timestamp

    def test_wick_only_breach_does_not_invalidate(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candles = [_candle(1, 97, 98, 90, 96)]  # wicks below 95 but closes above

        result = ZoneInvalidationChecker().evaluate(zone, candles)
        assert result.status == ZoneStatus.FRESH


class TestSellZoneInvalidation:
    def test_close_above_upper_boundary_invalidates(self):
        zone = _zone("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        candles = [_candle(1, 104, 110, 103, 108)]  # closes strictly above 105

        result = ZoneInvalidationChecker().evaluate(zone, candles)
        assert result.status == ZoneStatus.INVALIDATED

    def test_wick_only_breach_does_not_invalidate(self):
        zone = _zone("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        candles = [_candle(1, 103, 110, 102, 104)]  # wicks above 105 but closes below

        result = ZoneInvalidationChecker().evaluate(zone, candles)
        assert result.status == ZoneStatus.FRESH


class TestGuardConditions:
    def test_first_invalidation_timestamp_retained(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candles = [
            _candle(1, 96, 97, 90, 92),
            _candle(2, 92, 93, 85, 88),
        ]

        result = ZoneInvalidationChecker().evaluate(zone, candles)
        assert result.invalidation_timestamp == candles[0].timestamp

    def test_invalidation_overrides_mitigation_status(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        # Simulate a zone that was previously mitigated.
        mitigated_zone = zone.model_copy(
            update={"status": ZoneStatus.MITIGATED, "mitigation_timestamp": UTC_NOW + timedelta(minutes=1)}
        )
        candles = [_candle(2, 92, 93, 85, 88)]  # closes below lower boundary

        result = ZoneInvalidationChecker().evaluate(mitigated_zone, candles)
        assert result.status == ZoneStatus.INVALIDATED

    def test_evaluation_time_respected(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candles = [_candle(5, 96, 97, 90, 92)]
        evaluation_time = UTC_NOW + timedelta(minutes=1)  # before the invalidating candle

        result = ZoneInvalidationChecker().evaluate(zone, candles, evaluation_time_utc=evaluation_time)
        assert result.status == ZoneStatus.FRESH

    def test_input_zone_not_mutated(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        zone_snapshot = zone.model_copy()
        candles = [_candle(1, 96, 97, 90, 92)]

        ZoneInvalidationChecker().evaluate(zone, candles)

        assert zone == zone_snapshot

    def test_evaluate_multiple(self):
        zone_a = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        zone_b = _zone("SELL", lower=110.0, upper=115.0, created_at=UTC_NOW)
        candles = [_candle(1, 96, 97, 90, 92)]

        results = ZoneInvalidationChecker().evaluate_multiple([zone_a, zone_b], candles)
        assert results[0].status == ZoneStatus.INVALIDATED
        assert results[1].status == ZoneStatus.FRESH
