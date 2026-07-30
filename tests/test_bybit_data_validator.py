"""
Unit tests for app.data.bybit_data_validator.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.data.bybit_data_validator import (
    DataValidationError,
    is_completed_kline,
    validate_bybit_response,
)

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_FIFTEEN_MINUTES = 15 * 60


def _valid_row(start_time_ms: str = "1700000000000") -> list[str]:
    # [startTime, open, high, low, close, volume, turnover]
    return [start_time_ms, "100", "110", "95", "105", "1000", "100000"]


class TestValidateBybitResponse:
    def test_valid_bybit_response(self):
        payload = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"category": "linear", "symbol": "BTCUSDT", "list": [_valid_row()]},
        }
        rows = validate_bybit_response(payload)
        assert rows == [_valid_row()]

    def test_non_dictionary_payload(self):
        with pytest.raises(DataValidationError):
            validate_bybit_response(["not", "a", "dict"])

    def test_non_zero_ret_code(self):
        payload = {"retCode": 10001, "retMsg": "Request parameter error", "result": {"list": []}}
        with pytest.raises(DataValidationError):
            validate_bybit_response(payload)

    def test_missing_result(self):
        payload = {"retCode": 0, "retMsg": "OK"}
        with pytest.raises(DataValidationError):
            validate_bybit_response(payload)

    def test_result_not_a_dict(self):
        payload = {"retCode": 0, "retMsg": "OK", "result": "not-a-dict"}
        with pytest.raises(DataValidationError):
            validate_bybit_response(payload)

    def test_missing_list(self):
        payload = {"retCode": 0, "retMsg": "OK", "result": {}}
        with pytest.raises(DataValidationError):
            validate_bybit_response(payload)

    def test_list_not_a_list(self):
        payload = {"retCode": 0, "retMsg": "OK", "result": {"list": "not-a-list"}}
        with pytest.raises(DataValidationError):
            validate_bybit_response(payload)

    def test_malformed_kline_row(self):
        payload = {"retCode": 0, "retMsg": "OK", "result": {"list": [["too", "short"]]}}
        with pytest.raises(DataValidationError):
            validate_bybit_response(payload)

    def test_row_not_a_list(self):
        payload = {"retCode": 0, "retMsg": "OK", "result": {"list": ["not-a-list-row"]}}
        with pytest.raises(DataValidationError):
            validate_bybit_response(payload)


class TestIsCompletedKline:
    def test_completed_kline(self):
        # A candle that started 20 minutes ago on a 15m timeframe has
        # long since closed.
        start = UTC_NOW - timedelta(minutes=20)
        row = _valid_row(start_time_ms=str(int(start.timestamp() * 1000)))
        assert is_completed_kline(row, interval_seconds=_FIFTEEN_MINUTES, now_utc=UTC_NOW) is True

    def test_still_forming_kline(self):
        # A candle that started 5 minutes ago on a 15m timeframe is
        # still in progress.
        start = UTC_NOW - timedelta(minutes=5)
        row = _valid_row(start_time_ms=str(int(start.timestamp() * 1000)))
        assert is_completed_kline(row, interval_seconds=_FIFTEEN_MINUTES, now_utc=UTC_NOW) is False

    def test_exactly_at_close_boundary_is_completed(self):
        start = UTC_NOW - timedelta(seconds=_FIFTEEN_MINUTES)
        row = _valid_row(start_time_ms=str(int(start.timestamp() * 1000)))
        assert is_completed_kline(row, interval_seconds=_FIFTEEN_MINUTES, now_utc=UTC_NOW) is True

    def test_invalid_start_time_raises(self):
        row = ["not-a-timestamp", "100", "110", "95", "105", "1000", "100000"]
        with pytest.raises(DataValidationError):
            is_completed_kline(row, interval_seconds=_FIFTEEN_MINUTES, now_utc=UTC_NOW)

    def test_row_too_short_raises(self):
        with pytest.raises(DataValidationError):
            is_completed_kline(["1700000000000"], interval_seconds=_FIFTEEN_MINUTES, now_utc=UTC_NOW)
