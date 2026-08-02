"""
Tests for the Binance-specific structural validation helpers in
app.data.binance_market_data_provider: validate_binance_klines_response
and is_completed_kline.
"""

from datetime import datetime, timezone

import pytest

from app.data.binance_market_data_provider import (
    is_completed_kline,
    validate_binance_klines_response,
)
from app.data.candle_validator import DataValidationError

_VALID_ROW = [1600000000000, "100.0", "101.0", "99.0", "100.5", "10.0", 1600000899999]


class TestValidateBinanceKlinesResponse:
    def test_valid_payload_returns_rows(self):
        rows = validate_binance_klines_response([_VALID_ROW])
        assert rows == [_VALID_ROW]

    def test_empty_list_returns_empty(self):
        assert validate_binance_klines_response([]) == []

    def test_non_list_payload_rejected(self):
        with pytest.raises(DataValidationError):
            validate_binance_klines_response({"not": "a list"})

    def test_row_not_a_list_rejected(self):
        with pytest.raises(DataValidationError):
            validate_binance_klines_response(["not-a-list"])

    def test_row_with_too_few_fields_rejected(self):
        with pytest.raises(DataValidationError):
            validate_binance_klines_response([[1, 2, 3]])


class TestIsCompletedKline:
    def test_closed_before_now_is_completed(self):
        now = datetime.fromtimestamp(1600000900, tz=timezone.utc)
        assert is_completed_kline(_VALID_ROW, interval_seconds=900, now_utc=now) is True

    def test_closing_after_now_is_not_completed(self):
        now = datetime.fromtimestamp(1600000000, tz=timezone.utc)
        assert is_completed_kline(_VALID_ROW, interval_seconds=900, now_utc=now) is False

    def test_exact_close_time_boundary_is_completed(self):
        close_time_seconds = _VALID_ROW[6] / 1000
        now = datetime.fromtimestamp(close_time_seconds, tz=timezone.utc)
        assert is_completed_kline(_VALID_ROW, interval_seconds=900, now_utc=now) is True

    def test_too_few_fields_raises(self):
        now = datetime.now(timezone.utc)
        with pytest.raises(DataValidationError):
            is_completed_kline([1, 2], interval_seconds=900, now_utc=now)

    def test_malformed_close_time_raises(self):
        now = datetime.now(timezone.utc)
        bad_row = [1600000000000, "1", "1", "1", "1", "1", "not-a-number"]
        with pytest.raises(DataValidationError):
            is_completed_kline(bad_row, interval_seconds=900, now_utc=now)
