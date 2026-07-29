"""
Unit tests for app.validators.session_filter.SessionFilter.
"""

from datetime import datetime, timezone

from app.validators.results import TradingSession
from app.validators.session_filter import SessionFilter


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 1, 1, hour, minute, tzinfo=timezone.utc)


class TestDetectSessionBoundaries:
    def test_0000_is_asia(self):
        assert SessionFilter().detect_session(_dt(0, 0)) == TradingSession.ASIA

    def test_0759_is_asia(self):
        assert SessionFilter().detect_session(_dt(7, 59)) == TradingSession.ASIA

    def test_0800_is_london(self):
        assert SessionFilter().detect_session(_dt(8, 0)) == TradingSession.LONDON

    def test_1259_is_london(self):
        assert SessionFilter().detect_session(_dt(12, 59)) == TradingSession.LONDON

    def test_1300_is_overlap(self):
        assert SessionFilter().detect_session(_dt(13, 0)) == TradingSession.LONDON_NEW_YORK_OVERLAP

    def test_1559_is_overlap(self):
        assert SessionFilter().detect_session(_dt(15, 59)) == TradingSession.LONDON_NEW_YORK_OVERLAP

    def test_1600_is_new_york(self):
        assert SessionFilter().detect_session(_dt(16, 0)) == TradingSession.NEW_YORK

    def test_2059_is_new_york(self):
        assert SessionFilter().detect_session(_dt(20, 59)) == TradingSession.NEW_YORK

    def test_2100_is_unsupported(self):
        assert SessionFilter().detect_session(_dt(21, 0)) == TradingSession.OUTSIDE_SUPPORTED_SESSION


class TestSessionValidation:
    def test_london_passes(self):
        result = SessionFilter().validate(_dt(10, 0), btc_trend_strong=False, atr_high=False, volume_high=False)
        assert result.passed is True

    def test_new_york_passes(self):
        result = SessionFilter().validate(_dt(18, 0), btc_trend_strong=False, atr_high=False, volume_high=False)
        assert result.passed is True

    def test_overlap_passes(self):
        result = SessionFilter().validate(_dt(14, 0), btc_trend_strong=False, atr_high=False, volume_high=False)
        assert result.passed is True

    def test_asia_passes_only_when_all_three_conditions_true(self):
        result = SessionFilter().validate(_dt(2, 0), btc_trend_strong=True, atr_high=True, volume_high=True)
        assert result.passed is True

    def test_asia_fails_when_btc_trend_not_strong(self):
        result = SessionFilter().validate(_dt(2, 0), btc_trend_strong=False, atr_high=True, volume_high=True)
        assert result.passed is False
        assert result.rejection_code == "ASIAN_SESSION_CONDITIONS_FAILED"

    def test_asia_fails_when_atr_not_high(self):
        result = SessionFilter().validate(_dt(2, 0), btc_trend_strong=True, atr_high=False, volume_high=True)
        assert result.passed is False
        assert result.rejection_code == "ASIAN_SESSION_CONDITIONS_FAILED"

    def test_asia_fails_when_volume_not_high(self):
        result = SessionFilter().validate(_dt(2, 0), btc_trend_strong=True, atr_high=True, volume_high=False)
        assert result.passed is False
        assert result.rejection_code == "ASIAN_SESSION_CONDITIONS_FAILED"

    def test_unsupported_session_fails(self):
        result = SessionFilter().validate(_dt(22, 0), btc_trend_strong=True, atr_high=True, volume_high=True)
        assert result.passed is False
        assert result.rejection_code == "UNSUPPORTED_TRADING_SESSION"

    def test_naive_timestamp_fails(self):
        naive = datetime(2026, 1, 1, 10, 0)
        result = SessionFilter().validate(naive, btc_trend_strong=True, atr_high=True, volume_high=True)
        assert result.passed is False
        assert result.rejection_code == "INVALID_SESSION_TIMESTAMP"

    def test_score_remains_zero(self):
        passing = SessionFilter().validate(_dt(10, 0), True, True, True)
        assert passing.score == 0.0
        failing = SessionFilter().validate(_dt(22, 0), True, True, True)
        assert failing.score == 0.0
