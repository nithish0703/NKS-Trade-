"""
Risk package: stop loss, take profit, risk-reward, and correlation controls.
"""

from app.risk.active_trade_guard import ActiveTradeGuard
from app.risk.averaging_guard import AveragingGuard
from app.risk.calculator import RiskManagementCalculator
from app.risk.correlation_filter import CorrelationFilter
from app.risk.position_risk import PositionRiskCalculator
from app.risk.results import (
    CorrelationResult,
    CorrelationStatus,
    PositionRiskResult,
    RiskPlan,
    RiskPlanStatus,
    StopLossCandidate,
    StopLossResult,
    StopLossSource,
    TakeProfitCandidate,
    TakeProfitResult,
    TakeProfitSource,
)
from app.risk.risk_reward import calculate_risk_reward, validate_minimum_risk_reward
from app.risk.stop_loss import DynamicStopLossCalculator, RiskCalculationError
from app.risk.take_profit import SingleTakeProfitCalculator

__all__ = [
    "RiskCalculationError",
    "DynamicStopLossCalculator",
    "SingleTakeProfitCalculator",
    "PositionRiskCalculator",
    "CorrelationFilter",
    "ActiveTradeGuard",
    "AveragingGuard",
    "RiskManagementCalculator",
    "StopLossCandidate",
    "StopLossResult",
    "TakeProfitCandidate",
    "TakeProfitResult",
    "PositionRiskResult",
    "CorrelationResult",
    "RiskPlan",
    "StopLossSource",
    "TakeProfitSource",
    "RiskPlanStatus",
    "CorrelationStatus",
    "calculate_risk_reward",
    "validate_minimum_risk_reward",
]
