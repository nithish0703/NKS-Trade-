"""
Unit tests for app.risk.active_trade_guard.ActiveTradeGuard.
"""

from app.risk.active_trade_guard import ActiveTradeGuard


def _guard(max_trades=5) -> ActiveTradeGuard:
    return ActiveTradeGuard(maximum_active_trades=max_trades)


class TestActiveTradeGuard:
    def test_zero_active_trades_passes(self):
        result = _guard().validate(0)
        assert result.passed is True

    def test_four_active_trades_passes(self):
        result = _guard().validate(4)
        assert result.passed is True

    def test_five_active_trades_fails(self):
        result = _guard().validate(5)
        assert result.passed is False
        assert result.rejection_code == "MAXIMUM_ACTIVE_TRADES_REACHED"

    def test_more_than_five_fails(self):
        result = _guard().validate(6)
        assert result.passed is False

    def test_negative_count_fails(self):
        result = _guard().validate(-1)
        assert result.passed is False
        assert result.rejection_code == "INVALID_ACTIVE_TRADE_COUNT"

    def test_score_remains_zero(self):
        passing = _guard().validate(0)
        assert passing.score == 0.0
        failing = _guard().validate(5)
        assert failing.score == 0.0
