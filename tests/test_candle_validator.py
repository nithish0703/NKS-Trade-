"""
Unit tests for app.data.candle_validator (exchange-agnostic Candle
sequence structural validation).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.data.candle_validator import DataValidationError, validate_candle_sequence
from app.models.candle import Candle

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
        "timeframe": "15m",
    }
    fields.update(overrides)
    return Candle(**fields)


class TestValidateCandleSequence:
    def test_valid_ascending_candle_sequence(self):
        candles = [
            _make_candle(timestamp=UTC_NOW),
            _make_candle(timestamp=UTC_NOW + timedelta(minutes=15)),
        ]
        validate_candle_sequence(candles)  # should not raise

    def test_empty_sequence_rejected(self):
        with pytest.raises(DataValidationError):
            validate_candle_sequence([])

    def test_duplicate_candle_timestamps(self):
        candles = [_make_candle(timestamp=UTC_NOW), _make_candle(timestamp=UTC_NOW)]
        with pytest.raises(DataValidationError):
            validate_candle_sequence(candles)

    def test_non_ascending_timestamps(self):
        candles = [
            _make_candle(timestamp=UTC_NOW + timedelta(minutes=15)),
            _make_candle(timestamp=UTC_NOW),
        ]
        with pytest.raises(DataValidationError):
            validate_candle_sequence(candles)

    def test_mixed_symbols_rejected(self):
        candles = [
            _make_candle(timestamp=UTC_NOW, symbol="BTC-USDT"),
            _make_candle(timestamp=UTC_NOW + timedelta(minutes=15), symbol="ETH-USDT"),
        ]
        with pytest.raises(DataValidationError):
            validate_candle_sequence(candles)

    def test_mixed_timeframes_rejected(self):
        candles = [
            _make_candle(timestamp=UTC_NOW, timeframe="15m"),
            _make_candle(timestamp=UTC_NOW + timedelta(minutes=15), timeframe="1h"),
        ]
        with pytest.raises(DataValidationError):
            validate_candle_sequence(candles)
