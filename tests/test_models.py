"""
Unit tests for core data models: Candle, ValidationResult, TradeZone, and Signal.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.candle import Candle
from app.models.signal import Direction, Signal, SignalStatus
from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType
from app.models.validation_result import ValidationResult

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_candle(**overrides) -> Candle:
    fields = {
        "timestamp": UTC_NOW,
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 1000.0,
        "symbol": "BTC-USDT",
        "timeframe": "1h",
    }
    fields.update(overrides)
    return Candle(**fields)


def _make_signal(**overrides) -> Signal:
    fields = {
        "trade_id": "abc123",
        "coin": "BTC-USDT",
        "direction": Direction.BUY,
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "risk_reward_ratio": 2.0,
        "status": SignalStatus.CONFIRMED,
        "liquidity_type": "EQUAL_HIGHS",
        "entry_zone_type": "ORDER_BLOCK",
        "structure_confirmation": "BOS",
        "detection_time_utc": UTC_NOW,
        "institutional_reason": "Bullish order block retest with liquidity sweep.",
        "setup_key": "setup-key-abc",
        "liquidity_sweep_id": "sweep-1",
        "structure_break_id": "break-1",
        "entry_zone_id": "zone-1",
        "created_at_utc": UTC_NOW,
    }
    fields.update(overrides)
    return Signal(**fields)


class TestCandle:
    def test_valid_bullish_candle(self):
        candle = _make_candle()
        assert candle.is_bullish
        assert not candle.is_bearish

    def test_invalid_candle_high(self):
        with pytest.raises(ValidationError):
            _make_candle(high=90.0)

    def test_candle_body_ratio(self):
        candle = _make_candle(open=100.0, high=110.0, low=95.0, close=105.0)
        expected_ratio = candle.body_size / candle.candle_range
        assert candle.body_ratio == pytest.approx(expected_ratio)
        assert candle.body_ratio == pytest.approx(5.0 / 15.0)

    def test_naive_non_utc_datetime_rejected(self):
        with pytest.raises(ValidationError):
            _make_candle(timestamp=datetime(2026, 1, 1))


class TestValidationResult:
    def test_successful_validation_result(self):
        result = ValidationResult.success(layer_name="market_regime", score=90.0)
        assert result.passed is True
        assert result.rejection_code is None
        assert result.score == 90.0

    def test_failed_validation_result(self):
        result = ValidationResult.failure(
            layer_name="liquidity_sweep",
            reason="No sweep detected",
            rejection_code="NO_SWEEP",
        )
        assert result.passed is False
        assert result.rejection_code == "NO_SWEEP"

    def test_negative_score_rejected(self):
        with pytest.raises(ValidationError):
            ValidationResult(passed=True, layer_name="x", score=-1.0)


class TestTradeZone:
    def test_invalid_trade_zone_boundaries(self):
        with pytest.raises(ValidationError):
            TradeZone(
                zone_id="z1",
                symbol="BTC-USDT",
                timeframe="1h",
                zone_type=ZoneType.ORDER_BLOCK,
                direction="BUY",
                lower_price=110.0,
                upper_price=100.0,
                created_at=UTC_NOW,
                status=ZoneStatus.FRESH,
                source_candle_timestamp=UTC_NOW,
            )

    def test_valid_trade_zone(self):
        zone = TradeZone(
            zone_id="z1",
            symbol="BTC-USDT",
            timeframe="1h",
            zone_type=ZoneType.FAIR_VALUE_GAP,
            direction="SELL",
            lower_price=100.0,
            upper_price=110.0,
            created_at=UTC_NOW,
            source_candle_timestamp=UTC_NOW,
        )
        assert zone.status == ZoneStatus.FRESH


class TestSignal:
    def test_valid_buy_signal(self):
        signal = _make_signal(
            direction=Direction.BUY,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=110.0,
        )
        assert signal.direction == Direction.BUY

    def test_invalid_buy_stop_loss(self):
        with pytest.raises(ValidationError):
            _make_signal(
                direction=Direction.BUY,
                entry_price=100.0,
                stop_loss=105.0,
                take_profit=110.0,
            )

    def test_valid_sell_signal(self):
        signal = _make_signal(
            direction=Direction.SELL,
            entry_price=100.0,
            stop_loss=105.0,
            take_profit=90.0,
        )
        assert signal.direction == Direction.SELL

    def test_invalid_sell_take_profit(self):
        with pytest.raises(ValidationError):
            _make_signal(
                direction=Direction.SELL,
                entry_price=100.0,
                stop_loss=105.0,
                take_profit=110.0,
            )

    def test_rejected_status_not_constructible(self):
        with pytest.raises(ValidationError):
            _make_signal(status=SignalStatus.REJECTED)

    def test_naive_non_utc_detection_time_rejected(self):
        with pytest.raises(ValidationError):
            _make_signal(detection_time_utc=datetime(2026, 1, 1))
