"""
Unit tests for app.risk.position_risk.PositionRiskCalculator.
"""

import pytest

from app.risk.position_risk import PositionRiskCalculator


def _calculator(max_risk=1.0) -> PositionRiskCalculator:
    return PositionRiskCalculator(maximum_risk_percentage=max_risk)


class TestPositionRiskCalculator:
    def test_valid_1_percent_risk(self):
        result = _calculator().calculate(10000.0, 100.0, 95.0, "BUY", requested_risk_percentage=1.0)
        assert result.valid is True
        assert result.risk_amount == pytest.approx(100.0)

    def test_lower_requested_risk_accepted(self):
        result = _calculator().calculate(10000.0, 100.0, 95.0, "BUY", requested_risk_percentage=0.5)
        assert result.valid is True
        assert result.risk_percentage == 0.5

    def test_risk_above_1_percent_rejected(self):
        result = _calculator().calculate(10000.0, 100.0, 95.0, "BUY", requested_risk_percentage=1.5)
        assert result.valid is False
        assert result.metadata["rejection_code"] == "RISK_EXCEEDS_MAXIMUM"

    def test_default_risk_is_1_percent(self):
        result = _calculator().calculate(10000.0, 100.0, 95.0, "BUY")
        assert result.risk_percentage == 1.0
        assert result.valid is True

    def test_valid_buy_position_size(self):
        result = _calculator().calculate(10000.0, 100.0, 95.0, "BUY", requested_risk_percentage=1.0)
        # risk_amount=100, stop_distance=5 -> size=20
        assert result.position_size == pytest.approx(20.0)

    def test_valid_sell_position_size(self):
        result = _calculator().calculate(10000.0, 100.0, 105.0, "SELL", requested_risk_percentage=1.0)
        assert result.position_size == pytest.approx(20.0)

    def test_invalid_account_balance(self):
        result = _calculator().calculate(0.0, 100.0, 95.0, "BUY")
        assert result.valid is False
        assert result.metadata["rejection_code"] == "INVALID_ACCOUNT_BALANCE"

    def test_zero_stop_distance(self):
        result = _calculator().calculate(10000.0, 100.0, 100.0, "BUY")
        assert result.valid is False
        assert result.metadata["rejection_code"] == "INVALID_STOP_DISTANCE"

    def test_wrong_side_stop(self):
        result = _calculator().calculate(10000.0, 100.0, 105.0, "BUY")  # stop above entry for BUY
        assert result.valid is False
        assert result.metadata["rejection_code"] == "INVALID_STOP_DISTANCE"

    def test_finite_positive_size(self):
        result = _calculator().calculate(10000.0, 100.0, 95.0, "BUY")
        assert result.position_size > 0

    def test_no_leverage_calculation(self):
        result = _calculator().calculate(10000.0, 100.0, 95.0, "BUY")
        result_fields = set(type(result).model_fields.keys())
        assert "leverage" not in result_fields

    def test_inputs_not_mutated(self):
        balance = 10000.0
        entry = 100.0
        stop = 95.0
        _calculator().calculate(balance, entry, stop, "BUY")
        assert balance == 10000.0
        assert entry == 100.0
        assert stop == 95.0
