"""
Unit tests for app.risk.risk_reward.
"""

import pytest

from app.risk.risk_reward import calculate_risk_reward, validate_minimum_risk_reward
from app.risk.stop_loss import RiskCalculationError


class TestCalculateRiskReward:
    def test_valid_buy_rr(self):
        ratio = calculate_risk_reward("BUY", entry_price=100.0, stop_loss=95.0, take_profit=110.0)
        assert ratio == pytest.approx(2.0)

    def test_valid_sell_rr(self):
        ratio = calculate_risk_reward("SELL", entry_price=100.0, stop_loss=105.0, take_profit=90.0)
        assert ratio == pytest.approx(2.0)

    def test_buy_stop_wrong_side(self):
        with pytest.raises(RiskCalculationError):
            calculate_risk_reward("BUY", entry_price=100.0, stop_loss=105.0, take_profit=110.0)

    def test_buy_tp_wrong_side(self):
        with pytest.raises(RiskCalculationError):
            calculate_risk_reward("BUY", entry_price=100.0, stop_loss=95.0, take_profit=90.0)

    def test_sell_stop_wrong_side(self):
        with pytest.raises(RiskCalculationError):
            calculate_risk_reward("SELL", entry_price=100.0, stop_loss=95.0, take_profit=90.0)

    def test_sell_tp_wrong_side(self):
        with pytest.raises(RiskCalculationError):
            calculate_risk_reward("SELL", entry_price=100.0, stop_loss=105.0, take_profit=110.0)

    def test_zero_risk_distance_rejected(self):
        with pytest.raises(RiskCalculationError):
            calculate_risk_reward("BUY", entry_price=100.0, stop_loss=100.0, take_profit=110.0)

    def test_non_positive_price_rejected(self):
        with pytest.raises(RiskCalculationError):
            calculate_risk_reward("BUY", entry_price=0.0, stop_loss=95.0, take_profit=110.0)


class TestValidateMinimumRiskReward:
    def test_rr_exactly_2_passes(self):
        result = validate_minimum_risk_reward(2.0)
        assert result.passed is True

    def test_rr_below_2_fails(self):
        result = validate_minimum_risk_reward(1.5)
        assert result.passed is False
        assert result.rejection_code == "RISK_REWARD_BELOW_MINIMUM"

    def test_invalid_rr_fails(self):
        result = validate_minimum_risk_reward(-1.0)
        assert result.passed is False
        assert result.rejection_code == "INVALID_RISK_REWARD"

    def test_score_remains_zero(self):
        passing = validate_minimum_risk_reward(2.5)
        assert passing.score == 0.0
        failing = validate_minimum_risk_reward(1.0)
        assert failing.score == 0.0
