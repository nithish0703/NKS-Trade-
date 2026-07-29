"""
Unit tests for app.validators.risk_management.RiskManagementValidator.
"""

from app.risk.results import (
    CorrelationResult,
    CorrelationStatus,
    PositionRiskResult,
    RiskPlan,
    RiskPlanStatus,
    StopLossResult,
    TakeProfitResult,
)
from app.validators.risk_management import RiskManagementValidator


def _stop_loss(valid=True) -> StopLossResult:
    return StopLossResult(
        direction="BUY",
        entry_price=100.0,
        selected_stop_loss=95.0 if valid else None,
        candidates=[],
        valid=valid,
        reason="test",
    )


def _take_profit(valid=True) -> TakeProfitResult:
    return TakeProfitResult(
        direction="BUY",
        entry_price=100.0,
        stop_loss=95.0,
        selected_take_profit=110.0 if valid else None,
        candidates=[],
        valid=valid,
        reason="test",
    )


def _position_risk(valid=True, risk_percentage=1.0) -> PositionRiskResult:
    return PositionRiskResult(
        account_balance=10000.0,
        risk_percentage=risk_percentage,
        risk_amount=100.0 if valid else None,
        entry_price=100.0,
        stop_loss=95.0,
        stop_distance=5.0 if valid else None,
        position_size=20.0 if valid else None,
        valid=valid,
        reason="test",
    )


def _correlation(acceptable=True, status=CorrelationStatus.ACCEPTABLE) -> CorrelationResult:
    return CorrelationResult(
        candidate_symbol="ETH-USDT",
        active_symbols=[],
        maximum_allowed_correlation=0.8,
        observed_correlations={},
        status=status,
        acceptable=acceptable,
        reason="test",
    )


def _plan(
    stop_loss=None,
    take_profit=None,
    position_risk=None,
    correlation=None,
    active_trade_count=0,
    maximum_active_trades=5,
    risk_reward_ratio=2.5,
    status=RiskPlanStatus.VALID,
    valid=True,
    reason="Risk plan is valid.",
) -> RiskPlan:
    return RiskPlan(
        direction="BUY",
        entry_price=100.0,
        stop_loss_result=stop_loss or _stop_loss(),
        take_profit_result=take_profit or _take_profit(),
        position_risk=position_risk or _position_risk(),
        correlation_result=correlation or _correlation(),
        active_trade_count=active_trade_count,
        maximum_active_trades=maximum_active_trades,
        risk_reward_ratio=risk_reward_ratio,
        status=status,
        valid=valid,
        reason=reason,
    )


class TestRiskManagementValidator:
    def test_valid_risk_plan_passes(self):
        result = RiskManagementValidator().validate(_plan())
        assert result.passed is True
        assert result.score == 0.0

    def test_missing_risk_plan_fails(self):
        result = RiskManagementValidator().validate(None)
        assert result.passed is False
        assert result.rejection_code == "RISK_PLAN_MISSING"

    def test_invalid_stop_loss_fails(self):
        plan = _plan(stop_loss=_stop_loss(valid=False), status=RiskPlanStatus.INVALID, valid=False)
        result = RiskManagementValidator().validate(plan)
        assert result.passed is False
        assert result.rejection_code == "STOP_LOSS_INVALID"

    def test_invalid_take_profit_fails(self):
        plan = _plan(take_profit=_take_profit(valid=False), status=RiskPlanStatus.INVALID, valid=False)
        result = RiskManagementValidator().validate(plan)
        assert result.passed is False
        assert result.rejection_code == "TAKE_PROFIT_INVALID"

    def test_rr_below_2_fails(self):
        plan = _plan(risk_reward_ratio=1.5, status=RiskPlanStatus.INVALID, valid=False)
        result = RiskManagementValidator().validate(plan)
        assert result.passed is False
        assert result.rejection_code == "RISK_REWARD_BELOW_MINIMUM"

    def test_risk_above_1_percent_fails(self):
        plan = _plan(position_risk=_position_risk(risk_percentage=1.5), status=RiskPlanStatus.INVALID, valid=False)
        result = RiskManagementValidator().validate(plan)
        assert result.passed is False
        assert result.rejection_code == "POSITION_RISK_INVALID"

    def test_five_active_trades_fails(self):
        plan = _plan(active_trade_count=5, maximum_active_trades=5, status=RiskPlanStatus.INVALID, valid=False)
        result = RiskManagementValidator().validate(plan)
        assert result.passed is False
        assert result.rejection_code == "MAXIMUM_ACTIVE_TRADES_REACHED"

    def test_excessive_correlation_fails(self):
        plan = _plan(
            correlation=_correlation(acceptable=False, status=CorrelationStatus.TOO_HIGH),
            status=RiskPlanStatus.INVALID,
            valid=False,
        )
        result = RiskManagementValidator().validate(plan)
        assert result.passed is False
        assert result.rejection_code == "CORRELATION_TOO_HIGH"

    def test_averaging_losing_trade_fails(self):
        plan = _plan(
            status=RiskPlanStatus.REJECTED,
            valid=False,
            reason="An active losing BUY position already exists for ETH-USDT; averaging is rejected.",
        )
        result = RiskManagementValidator().validate(plan)
        assert result.passed is False
        assert result.rejection_code == "LOSING_POSITION_AVERAGING_REJECTED"

    def test_score_remains_zero(self):
        passing = RiskManagementValidator().validate(_plan())
        assert passing.score == 0.0
        failing = RiskManagementValidator().validate(None)
        assert failing.score == 0.0

    def test_no_signal_generation(self):
        result = RiskManagementValidator().validate(_plan())
        result_fields = set(type(result).model_fields.keys())
        assert "signal_type" not in result_fields
        assert "entry_price" not in result_fields
