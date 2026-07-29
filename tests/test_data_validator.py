"""
Unit tests for app.data.data_validator.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.data.data_validator import (
    DataValidationError,
    is_completed_candle,
    validate_candle_sequence,
    validate_okx_response,
)
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


def _valid_row(confirm: str = "1") -> list[str]:
    return [
        "1700000000000",
        "100",
        "110",
        "95",
        "105",
        "1000",
        "1000",
        "1000",
        confirm,
    ]


class TestValidateOkxResponse:
    def test_valid_okx_response(self):
        payload = {"code": "0", "msg": "", "data": [_valid_row()]}
        rows = validate_okx_response(payload)
        assert rows == [_valid_row()]

    def test_non_dictionary_payload(self):
        with pytest.raises(DataValidationError):
            validate_okx_response(["not", "a", "dict"])

    def test_non_zero_response_code(self):
        payload = {"code": "1", "msg": "error", "data": []}
        with pytest.raises(DataValidationError):
            validate_okx_response(payload)

    def test_missing_data(self):
        payload = {"code": "0", "msg": ""}
        with pytest.raises(DataValidationError):
            validate_okx_response(payload)

    def test_malformed_candle_row(self):
        payload = {"code": "0", "msg": "", "data": [["too", "short"]]}
        with pytest.raises(DataValidationError):
            validate_okx_response(payload)

    def test_data_not_a_list(self):
        payload = {"code": "0", "msg": "", "data": "not-a-list"}
        with pytest.raises(DataValidationError):
            validate_okx_response(payload)

    def test_row_not_a_list(self):
        payload = {"code": "0", "msg": "", "data": ["not-a-list-row"]}
        with pytest.raises(DataValidationError):
            validate_okx_response(payload)


class TestIsCompletedCandle:
    def test_completed_candle_confirmation(self):
        assert is_completed_candle(_valid_row(confirm="1")) is True

    def test_incomplete_candle_confirmation(self):
        assert is_completed_candle(_valid_row(confirm="0")) is False

    def test_invalid_confirmation_value(self):
        with pytest.raises(DataValidationError):
            is_completed_candle(_valid_row(confirm="x"))


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
