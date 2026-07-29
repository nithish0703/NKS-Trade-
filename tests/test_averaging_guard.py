"""
Unit tests for app.risk.averaging_guard.AveragingGuard.
"""

from app.risk.averaging_guard import AveragingGuard


def _position(symbol, direction, entry_price, current_price, status="OPEN"):
    return {
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry_price,
        "current_price": current_price,
        "status": status,
    }


class TestAveragingGuard:
    def test_no_existing_position_passes(self):
        result = AveragingGuard().validate("BTC-USDT", "BUY", [])
        assert result.passed is True

    def test_profitable_same_direction_position_does_not_trigger(self):
        position = _position("BTC-USDT", "BUY", entry_price=100.0, current_price=110.0)
        result = AveragingGuard().validate("BTC-USDT", "BUY", [position])
        assert result.passed is True

    def test_losing_buy_same_symbol_and_direction_fails(self):
        position = _position("BTC-USDT", "BUY", entry_price=100.0, current_price=95.0)
        result = AveragingGuard().validate("BTC-USDT", "BUY", [position])
        assert result.passed is False
        assert result.rejection_code == "LOSING_POSITION_AVERAGING_REJECTED"

    def test_losing_sell_same_symbol_and_direction_fails(self):
        position = _position("BTC-USDT", "SELL", entry_price=100.0, current_price=105.0)
        result = AveragingGuard().validate("BTC-USDT", "SELL", [position])
        assert result.passed is False
        assert result.rejection_code == "LOSING_POSITION_AVERAGING_REJECTED"

    def test_opposite_direction_does_not_trigger(self):
        position = _position("BTC-USDT", "SELL", entry_price=100.0, current_price=95.0)
        result = AveragingGuard().validate("BTC-USDT", "BUY", [position])
        assert result.passed is True

    def test_different_symbol_does_not_trigger(self):
        position = _position("ETH-USDT", "BUY", entry_price=100.0, current_price=95.0)
        result = AveragingGuard().validate("BTC-USDT", "BUY", [position])
        assert result.passed is True

    def test_missing_position_data_fails_safely(self):
        incomplete_position = {"symbol": "BTC-USDT", "direction": "BUY"}  # missing prices/status
        result = AveragingGuard().validate("BTC-USDT", "BUY", [incomplete_position])
        assert result.passed is False
        assert result.rejection_code == "AVERAGING_DATA_MISSING"

    def test_score_remains_zero(self):
        passing = AveragingGuard().validate("BTC-USDT", "BUY", [])
        assert passing.score == 0.0
        losing = _position("BTC-USDT", "BUY", entry_price=100.0, current_price=95.0)
        failing = AveragingGuard().validate("BTC-USDT", "BUY", [losing])
        assert failing.score == 0.0
