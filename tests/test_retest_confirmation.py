"""
Unit tests for app.zones.retest_confirmation.RetestConfirmationDetector.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.candle import Candle
from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType
from app.zones.retest_confirmation import RetestCalculationError, RetestConfirmationDetector
from app.zones.retest_results import RetestStatus

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


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


def _zone(direction: str, lower: float, upper: float, created_at, status=ZoneStatus.FRESH, **overrides) -> TradeZone:
    fields = dict(
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
        status=status,
        touch_count=0,
    )
    fields.update(overrides)
    return TradeZone(**fields)


def _detector(
    min_body=0.50, bull_min=0.65, bear_max=0.35, min_wick=0.15, max_confirm=3
) -> RetestConfirmationDetector:
    return RetestConfirmationDetector(
        minimum_rejection_body_ratio=min_body,
        bullish_close_location_minimum=bull_min,
        bearish_close_location_maximum=bear_max,
        minimum_rejection_wick_ratio=min_wick,
        maximum_confirmation_candles=max_confirm,
    )


class TestConfirmedRetests:
    def test_confirmed_buy_retest(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        # Candle dips into the zone then rejects upward with a strong bullish close and lower wick.
        candle = _candle(1, open_=97, high=103, low=94, close=102, volume=200)
        results = _detector().detect([candle], zone, [100.0])
        assert len(results) == 1
        assert results[0].status == RetestStatus.CONFIRMED

    def test_confirmed_sell_retest(self):
        zone = _zone("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        candle = _candle(1, open_=103, high=106, low=97, close=98, volume=200)
        results = _detector().detect([candle], zone, [100.0])
        assert len(results) == 1
        assert results[0].status == RetestStatus.CONFIRMED


class TestZoneOverlapRequirement:
    def test_buy_zone_overlap_required(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candle = _candle(1, open_=110, high=112, low=108, close=111, volume=200)  # nowhere near zone
        results = _detector().detect([candle], zone, [100.0])
        assert results[0].status == RetestStatus.REJECTED
        assert results[0].metadata["rejection_code"] == "ZONE_NOT_TOUCHED"

    def test_sell_zone_overlap_required(self):
        zone = _zone("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        candle = _candle(1, open_=80, high=82, low=78, close=79, volume=200)
        results = _detector().detect([candle], zone, [100.0])
        assert results[0].metadata["rejection_code"] == "ZONE_NOT_TOUCHED"

    def test_approach_without_touch_fails(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candle = _candle(1, open_=101, high=102, low=100.5, close=101.5, volume=200)  # low=100.5 > upper=100
        results = _detector().detect([candle], zone, [100.0])
        assert results[0].metadata["rejection_code"] == "ZONE_NOT_TOUCHED"

    def test_creation_candle_cannot_confirm_retest(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        creation_candle = _candle(0, open_=97, high=103, low=94, close=102, volume=200)
        results = _detector().detect([creation_candle], zone, [100.0])
        assert results[0].metadata["rejection_code"] == "ZONE_NOT_TOUCHED"


class TestRejectionCandleRequirements:
    def test_bullish_rejection_required_for_buy(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        # Bearish candle overlapping the zone -> wrong direction.
        candle = _candle(1, open_=99, high=100, low=94, close=96, volume=200)
        results = _detector().detect([candle], zone, [100.0])
        assert results[0].status == RetestStatus.REJECTED
        assert results[0].metadata["rejection_code"] == "REJECTION_CANDLE_MISSING"

    def test_bearish_rejection_required_for_sell(self):
        zone = _zone("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        candle = _candle(1, open_=101, high=106, low=100, close=104, volume=200)  # bullish
        results = _detector().detect([candle], zone, [100.0])
        assert results[0].metadata["rejection_code"] == "REJECTION_CANDLE_MISSING"

    def test_body_ratio_threshold(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        # Bullish but tiny body relative to range.
        candle = _candle(1, open_=97.0, high=103.0, low=94.0, close=97.5, volume=200)
        results = _detector(min_body=0.50).detect([candle], zone, [100.0])
        assert results[0].metadata["rejection_code"] == "WEAK_REJECTION_BODY"

    def test_close_location_threshold(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        # Large body but close not near the high (weak CLV) and below midpoint check avoided by using high min.
        candle = _candle(1, open_=95.0, high=110.0, low=94.0, close=101.0, volume=200)
        results = _detector(min_body=0.10, bull_min=0.95).detect([candle], zone, [100.0])
        assert results[0].metadata["rejection_code"] == "WEAK_REJECTION_CLOSE"

    def test_lower_wick_required_for_buy(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        # Strong bullish close beyond midpoint, good CLV, but no meaningful
        # lower wick and close does not exceed zone.upper_price.
        candle = _candle(1, open_=96.0, high=99.9, low=95.9, close=99.8, volume=200)
        results = _detector(min_body=0.10, bull_min=0.10, min_wick=0.50).detect([candle], zone, [100.0])
        assert results[0].metadata["rejection_code"] == "REJECTION_WICK_MISSING"

    def test_upper_wick_required_for_sell(self):
        zone = _zone("SELL", lower=100.0, upper=105.0, created_at=UTC_NOW)
        candle = _candle(1, open_=104.0, high=104.1, low=100.1, close=100.2, volume=200)
        results = _detector(min_body=0.10, bear_max=0.90, min_wick=0.50).detect([candle], zone, [100.0])
        assert results[0].metadata["rejection_code"] == "REJECTION_WICK_MISSING"

    def test_close_beyond_boundary_confirms_without_wick_threshold(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        # No real lower wick, but close finishes strictly above zone.upper_price.
        candle = _candle(1, open_=96.0, high=105.0, low=95.9, close=104.0, volume=200)
        results = _detector(min_body=0.10, bull_min=0.10, min_wick=0.90).detect([candle], zone, [100.0])
        assert results[0].status == RetestStatus.CONFIRMED


class TestVolumeConfirmation:
    def test_volume_above_ema_confirms(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candle = _candle(1, open_=97, high=103, low=94, close=102, volume=150)
        results = _detector().detect([candle], zone, [100.0])
        assert results[0].status == RetestStatus.CONFIRMED
        assert results[0].volume_confirmed is True

    def test_volume_equal_to_ema_fails(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candle = _candle(1, open_=97, high=103, low=94, close=102, volume=100)
        results = _detector().detect([candle], zone, [100.0])
        assert results[0].metadata["rejection_code"] == "RETEST_VOLUME_NOT_CONFIRMED"

    def test_volume_below_ema_fails(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candle = _candle(1, open_=97, high=103, low=94, close=102, volume=50)
        results = _detector().detect([candle], zone, [100.0])
        assert results[0].metadata["rejection_code"] == "RETEST_VOLUME_NOT_CONFIRMED"

    def test_missing_volume_ema_fails(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candle = _candle(1, open_=97, high=103, low=94, close=102, volume=150)
        results = _detector().detect([candle], zone, [None])
        assert results[0].metadata["rejection_code"] == "RETEST_VOLUME_MISSING"


class TestConfirmationWindow:
    def test_confirmation_on_interaction_candle(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candle = _candle(1, open_=97, high=103, low=94, close=102, volume=200)
        results = _detector().detect([candle], zone, [100.0])
        assert results[0].retest_candle_timestamp == candle.timestamp

    def test_confirmation_on_next_candle(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        interaction = _candle(1, open_=98, high=99, low=94, close=95.5, volume=50)  # touches but no rejection
        confirming = _candle(2, open_=96, high=103, low=95, close=102, volume=200)
        results = _detector().detect([interaction, confirming], zone, [100.0, 100.0])
        assert results[0].status == RetestStatus.CONFIRMED
        assert results[0].retest_candle_timestamp == confirming.timestamp

    def test_confirmation_within_configured_window(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        interaction = _candle(1, open_=98, high=99, low=94, close=95.5, volume=50)
        filler = _candle(2, open_=95.5, high=96, low=95, close=95.5, volume=50)
        confirming = _candle(3, open_=96, high=103, low=95, close=102, volume=200)
        results = _detector(max_confirm=3).detect(
            [interaction, filler, confirming], zone, [100.0, 100.0, 100.0]
        )
        assert results[0].status == RetestStatus.CONFIRMED
        assert results[0].retest_candle_timestamp == confirming.timestamp

    def test_confirmation_outside_window_fails(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        interaction = _candle(1, open_=98, high=99, low=94, close=95.5, volume=50)
        filler1 = _candle(2, open_=95.5, high=96, low=95, close=95.5, volume=50)
        filler2 = _candle(3, open_=95.5, high=96, low=95, close=95.5, volume=50)
        too_late = _candle(4, open_=96, high=103, low=95, close=102, volume=200)
        results = _detector(max_confirm=2).detect(
            [interaction, filler1, filler2, too_late], zone, [100.0] * 4
        )
        assert results[0].status == RetestStatus.REJECTED


class TestZoneStateGuards:
    def test_mitigated_zone_rejected(self):
        zone = _zone(
            "BUY",
            lower=95.0,
            upper=100.0,
            created_at=UTC_NOW,
            status=ZoneStatus.MITIGATED,
            mitigation_timestamp=UTC_NOW + timedelta(minutes=1),
        )
        candle = _candle(1, open_=97, high=103, low=94, close=102, volume=200)
        results = _detector().detect([candle], zone, [100.0])
        assert results[0].metadata["rejection_code"] == "ZONE_ALREADY_MITIGATED"

    def test_invalidated_zone_rejected(self):
        zone = _zone(
            "BUY",
            lower=95.0,
            upper=100.0,
            created_at=UTC_NOW,
            status=ZoneStatus.INVALIDATED,
            invalidation_timestamp=UTC_NOW + timedelta(minutes=1),
        )
        candle = _candle(1, open_=97, high=103, low=94, close=102, volume=200)
        results = _detector().detect([candle], zone, [100.0])
        assert results[0].metadata["rejection_code"] == "ZONE_INVALIDATED"


class TestDeterminismAndMutation:
    def test_stable_deterministic_retest_id(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candle = _candle(1, open_=97, high=103, low=94, close=102, volume=200)
        results_one = _detector().detect([candle], zone, [100.0])
        results_two = _detector().detect([candle], zone, [100.0])
        assert results_one[0].retest_id == results_two[0].retest_id

    def test_earliest_valid_confirmation_selected(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        confirming_early = _candle(1, open_=97, high=103, low=94, close=102, volume=200)
        confirming_late = _candle(2, open_=97, high=104, low=94, close=103, volume=200)
        results = _detector().detect([confirming_early, confirming_late], zone, [100.0, 100.0])
        assert len(results) == 1
        assert results[0].retest_candle_timestamp == confirming_early.timestamp

    def test_duplicate_confirmation_prevented(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candle = _candle(1, open_=97, high=103, low=94, close=102, volume=200)
        results = _detector().detect([candle], zone, [100.0])
        assert len(results) == 1

    def test_evaluation_time_respected(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candle = _candle(5, open_=97, high=103, low=94, close=102, volume=200)
        evaluation_time = UTC_NOW + timedelta(minutes=1)  # before the interaction candle
        results = _detector().detect([candle], zone, [100.0], evaluation_time_utc=evaluation_time)
        assert results[0].metadata["rejection_code"] == "ZONE_NOT_TOUCHED"

    def test_input_data_not_mutated(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candle = _candle(1, open_=97, high=103, low=94, close=102, volume=200)
        candle_snapshot = candle.model_copy()
        zone_snapshot = zone.model_copy()

        _detector().detect([candle], zone, [100.0])

        assert candle == candle_snapshot
        assert zone == zone_snapshot

    def test_mismatched_volume_ema_length_rejected(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candle = _candle(1, open_=97, high=103, low=94, close=102, volume=200)
        with pytest.raises(RetestCalculationError):
            _detector().detect([candle], zone, [100.0, 100.0])

    def test_no_entry_sl_tp_risk_score_or_signal_fields(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candle = _candle(1, open_=97, high=103, low=94, close=102, volume=200)
        results = _detector().detect([candle], zone, [100.0])
        result_fields = set(type(results[0]).model_fields.keys())
        forbidden = {
            "entry_price",
            "stop_loss",
            "take_profit",
            "risk_reward_ratio",
            "confidence_score",
            "signal_type",
        }
        assert result_fields.isdisjoint(forbidden)


class TestDetectLatestConfirmed:
    def test_returns_latest_confirmed(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candle = _candle(1, open_=97, high=103, low=94, close=102, volume=200)
        latest = _detector().detect_latest_confirmed([candle], zone, [100.0])
        assert latest is not None
        assert latest.status == RetestStatus.CONFIRMED

    def test_returns_none_when_no_confirmation(self):
        zone = _zone("BUY", lower=95.0, upper=100.0, created_at=UTC_NOW)
        candle = _candle(1, open_=110, high=112, low=108, close=111, volume=200)
        latest = _detector().detect_latest_confirmed([candle], zone, [100.0])
        assert latest is None
