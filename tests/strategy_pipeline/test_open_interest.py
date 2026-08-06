"""
Unit tests for app.strategy_pipeline.open_interest.
"""

from datetime import datetime, timedelta, timezone

from app.data.open_interest_point import OpenInterestPoint
from app.models.candle import Candle
from app.strategy_pipeline.open_interest import ConfirmationStatus, evaluate_open_interest_confirmation

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(index: int, close: float) -> Candle:
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=100.0,
        symbol="BTC-USDT",
        timeframe="15m",
    )


def _oi_point(index: int, value: float) -> OpenInterestPoint:
    return OpenInterestPoint(
        symbol="BTC-USDT", timestamp=UTC_NOW + timedelta(minutes=index), open_interest=value
    )


class TestOpenInterestConfirmation:
    def test_buy_confirmed_when_price_and_oi_both_rise(self):
        candles = [_candle(0, 100.0), _candle(1, 105.0)]
        oi = [_oi_point(0, 1000.0), _oi_point(1, 1100.0)]
        result = evaluate_open_interest_confirmation(candles, oi, expected_direction="BUY")
        assert result.passed is True
        assert result.status == ConfirmationStatus.CONFIRMED

    def test_buy_rejected_when_price_rises_but_oi_falls_short_covering(self):
        candles = [_candle(0, 100.0), _candle(1, 105.0)]
        oi = [_oi_point(0, 1000.0), _oi_point(1, 900.0)]
        result = evaluate_open_interest_confirmation(candles, oi, expected_direction="BUY")
        assert result.passed is False
        assert result.status == ConfirmationStatus.DISAGREED
        assert "short covering" in result.reason.lower()

    def test_buy_rejected_when_price_falls(self):
        candles = [_candle(0, 105.0), _candle(1, 100.0)]
        oi = [_oi_point(0, 1000.0), _oi_point(1, 1100.0)]
        result = evaluate_open_interest_confirmation(candles, oi, expected_direction="BUY")
        assert result.passed is False

    def test_sell_confirmed_when_price_falls_and_oi_rises(self):
        candles = [_candle(0, 105.0), _candle(1, 100.0)]
        oi = [_oi_point(0, 1000.0), _oi_point(1, 1100.0)]
        result = evaluate_open_interest_confirmation(candles, oi, expected_direction="SELL")
        assert result.passed is True
        assert result.status == ConfirmationStatus.CONFIRMED

    def test_sell_rejected_when_price_falls_but_oi_falls_long_liquidation(self):
        candles = [_candle(0, 105.0), _candle(1, 100.0)]
        oi = [_oi_point(0, 1000.0), _oi_point(1, 900.0)]
        result = evaluate_open_interest_confirmation(candles, oi, expected_direction="SELL")
        assert result.passed is False
        assert result.status == ConfirmationStatus.DISAGREED
        assert "long liquidation" in result.reason.lower()

    def test_sell_rejected_when_price_rises(self):
        candles = [_candle(0, 100.0), _candle(1, 105.0)]
        oi = [_oi_point(0, 1000.0), _oi_point(1, 1100.0)]
        result = evaluate_open_interest_confirmation(candles, oi, expected_direction="SELL")
        assert result.passed is False

    def test_insufficient_candles_fails(self):
        result = evaluate_open_interest_confirmation(
            [_candle(0, 100.0)], [_oi_point(0, 1000.0), _oi_point(1, 1100.0)], expected_direction="BUY"
        )
        assert result.passed is False
        assert result.status == ConfirmationStatus.UNAVAILABLE
        assert "candle" in result.reason.lower()

    def test_insufficient_oi_history_fails(self):
        result = evaluate_open_interest_confirmation(
            [_candle(0, 100.0), _candle(1, 105.0)], [_oi_point(0, 1000.0)], expected_direction="BUY"
        )
        assert result.passed is False
        assert result.status == ConfirmationStatus.UNAVAILABLE
        assert "open interest" in result.reason.lower()

    def test_empty_oi_history_from_fetch_failure_is_unavailable_not_disagreed(self):
        # An empty history (e.g. every retry attempt returned nothing)
        # must never be reported as a genuine directional disagreement.
        result = evaluate_open_interest_confirmation(
            [_candle(0, 100.0), _candle(1, 105.0)], [], expected_direction="BUY"
        )
        assert result.passed is False
        assert result.status == ConfirmationStatus.UNAVAILABLE

    def test_empty_inputs_fail_without_crashing(self):
        result = evaluate_open_interest_confirmation([], [], expected_direction="BUY")
        assert result.passed is False
        assert result.status == ConfirmationStatus.UNAVAILABLE

    def test_flat_price_rejects_buy(self):
        candles = [_candle(0, 100.0), _candle(1, 100.0)]
        oi = [_oi_point(0, 1000.0), _oi_point(1, 1100.0)]
        result = evaluate_open_interest_confirmation(candles, oi, expected_direction="BUY")
        assert result.passed is False

    def test_flat_oi_rejects_buy(self):
        candles = [_candle(0, 100.0), _candle(1, 105.0)]
        oi = [_oi_point(0, 1000.0), _oi_point(1, 1000.0)]
        result = evaluate_open_interest_confirmation(candles, oi, expected_direction="BUY")
        assert result.passed is False
