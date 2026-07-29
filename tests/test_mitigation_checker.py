"""
Unit tests for app.zones.mitigation_checker.
"""

from datetime import datetime, timedelta, timezone

from app.models.candle import Candle
from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType
from app.zones.mitigation_checker import ZoneMitigationChecker

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


class TestBuyZoneMitigation:
    def test_overlap_mitigation(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candles = [_candle(1, 100, 101, 97, 98)]  # overlaps zone [95,100]

        checker = ZoneMitigationChecker(full_mitigation_required=False, touch_tolerance_ratio=0.0001)
        result = checker.evaluate(zone, candles)
        assert result.status == ZoneStatus.MITIGATED
        assert result.mitigation_timestamp == candles[0].timestamp

    def test_full_mitigation(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candles = [_candle(1, 99, 99, 94, 95)]  # low <= 95 -> full mitigation

        checker = ZoneMitigationChecker(full_mitigation_required=True, touch_tolerance_ratio=0.0001)
        result = checker.evaluate(zone, candles)
        assert result.status == ZoneStatus.MITIGATED

    def test_partial_overlap_insufficient_for_full_mitigation(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candles = [_candle(1, 99, 99, 97, 98)]  # overlaps but low=97 > 95

        checker = ZoneMitigationChecker(full_mitigation_required=True, touch_tolerance_ratio=0.0001)
        result = checker.evaluate(zone, candles)
        assert result.status == ZoneStatus.FRESH


class TestSellZoneMitigation:
    def test_overlap_mitigation(self):
        zone = _zone("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        candles = [_candle(1, 100, 103, 99, 101)]  # overlaps zone [100,105]

        checker = ZoneMitigationChecker(full_mitigation_required=False, touch_tolerance_ratio=0.0001)
        result = checker.evaluate(zone, candles)
        assert result.status == ZoneStatus.MITIGATED

    def test_full_mitigation(self):
        zone = _zone("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        candles = [_candle(1, 101, 106, 100, 105)]  # high >= 105 -> full mitigation

        checker = ZoneMitigationChecker(full_mitigation_required=True, touch_tolerance_ratio=0.0001)
        result = checker.evaluate(zone, candles)
        assert result.status == ZoneStatus.MITIGATED


class TestGuardConditions:
    def test_no_touch_remains_fresh(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candles = [_candle(1, 110, 112, 108, 109)]  # nowhere near zone

        checker = ZoneMitigationChecker(full_mitigation_required=False, touch_tolerance_ratio=0.0001)
        result = checker.evaluate(zone, candles)
        assert result.status == ZoneStatus.FRESH
        assert result.mitigation_timestamp is None

    def test_creation_candle_cannot_mitigate(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        creation_candle = _candle(0, 99, 100, 96, 97)  # timestamp == created_at

        checker = ZoneMitigationChecker(full_mitigation_required=False, touch_tolerance_ratio=0.0001)
        result = checker.evaluate(zone, [creation_candle])
        assert result.status == ZoneStatus.FRESH

    def test_first_mitigation_timestamp_retained(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candles = [
            _candle(1, 99, 101, 97, 98),
            _candle(2, 98, 99, 96, 97),
        ]

        checker = ZoneMitigationChecker(full_mitigation_required=False, touch_tolerance_ratio=0.0001)
        result = checker.evaluate(zone, candles)
        assert result.mitigation_timestamp == candles[0].timestamp

    def test_touch_count(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candles = [
            _candle(1, 99, 101, 97, 98),
            _candle(2, 98, 99, 96, 97),
            _candle(3, 110, 112, 108, 109),  # not overlapping
        ]

        checker = ZoneMitigationChecker(full_mitigation_required=False, touch_tolerance_ratio=0.0001)
        result = checker.evaluate(zone, candles)
        assert result.touch_count == 2

    def test_evaluation_time_respected(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candles = [_candle(5, 99, 101, 97, 98)]
        evaluation_time = UTC_NOW + timedelta(minutes=1)  # before the mitigating candle

        checker = ZoneMitigationChecker(full_mitigation_required=False, touch_tolerance_ratio=0.0001)
        result = checker.evaluate(zone, candles, evaluation_time_utc=evaluation_time)
        assert result.status == ZoneStatus.FRESH

    def test_input_zone_not_mutated(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        zone_snapshot = zone.model_copy()
        candles = [_candle(1, 99, 101, 97, 98)]

        checker = ZoneMitigationChecker(full_mitigation_required=False, touch_tolerance_ratio=0.0001)
        checker.evaluate(zone, candles)

        assert zone == zone_snapshot

    def test_evaluate_multiple(self):
        zone_a = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        zone_b = _zone("SELL", lower=110.0, upper=115.0, created_at=UTC_NOW)
        candles = [_candle(1, 99, 101, 97, 98)]

        checker = ZoneMitigationChecker(full_mitigation_required=False, touch_tolerance_ratio=0.0001)
        results = checker.evaluate_multiple([zone_a, zone_b], candles)
        assert results[0].status == ZoneStatus.MITIGATED
        assert results[1].status == ZoneStatus.FRESH
