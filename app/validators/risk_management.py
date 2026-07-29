"""
Validates a fully calculated risk plan before it can be used downstream.
"""

from app.models.validation_result import ValidationResult
from app.risk.results import CorrelationStatus, RiskPlan, RiskPlanStatus

_LAYER_NAME = "RISK_MANAGEMENT"
_MINIMUM_RISK_REWARD = 2.0
_MAXIMUM_RISK_PERCENTAGE = 1.0


class RiskManagementValidator:
    """
    Validates that a RiskPlan is fully valid: stop loss, take profit,
    risk-reward, position risk, active-trade limit, correlation, and
    averaging guard all passed.
    """

    def validate(self, risk_plan: RiskPlan) -> ValidationResult:
        """
        Validate a RiskPlan.

        Failure codes:
            - RISK_PLAN_MISSING
            - STOP_LOSS_INVALID
            - TAKE_PROFIT_INVALID
            - RISK_REWARD_BELOW_MINIMUM
            - POSITION_RISK_INVALID
            - MAXIMUM_ACTIVE_TRADES_REACHED
            - CORRELATION_TOO_HIGH
            - LOSING_POSITION_AVERAGING_REJECTED
        """
        if risk_plan is None:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="No risk plan was provided.",
                rejection_code="RISK_PLAN_MISSING",
                score=0.0,
            )

        if risk_plan.status == RiskPlanStatus.REJECTED:
            if "losing" in risk_plan.reason.lower() or "averaging" in risk_plan.reason.lower():
                rejection_code = "LOSING_POSITION_AVERAGING_REJECTED"
            elif "active trade" in risk_plan.reason.lower():
                rejection_code = "MAXIMUM_ACTIVE_TRADES_REACHED"
            else:
                rejection_code = "RISK_PLAN_MISSING"
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=risk_plan.reason,
                rejection_code=rejection_code,
                score=0.0,
            )

        if not risk_plan.stop_loss_result.valid:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Stop loss is invalid.",
                rejection_code="STOP_LOSS_INVALID",
                score=0.0,
            )

        if not risk_plan.take_profit_result.valid:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Take profit is invalid.",
                rejection_code="TAKE_PROFIT_INVALID",
                score=0.0,
            )

        if (
            risk_plan.risk_reward_ratio is None
            or risk_plan.risk_reward_ratio < _MINIMUM_RISK_REWARD
        ):
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Risk-reward ratio is below the minimum.",
                rejection_code="RISK_REWARD_BELOW_MINIMUM",
                score=0.0,
            )

        if not risk_plan.position_risk.valid:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Position risk is invalid.",
                rejection_code="POSITION_RISK_INVALID",
                score=0.0,
            )

        if risk_plan.position_risk.risk_percentage > _MAXIMUM_RISK_PERCENTAGE:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Position risk exceeds the maximum allowed percentage.",
                rejection_code="POSITION_RISK_INVALID",
                score=0.0,
            )

        if risk_plan.active_trade_count >= risk_plan.maximum_active_trades:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Maximum active trades has been reached.",
                rejection_code="MAXIMUM_ACTIVE_TRADES_REACHED",
                score=0.0,
            )

        if (
            not risk_plan.correlation_result.acceptable
            or risk_plan.correlation_result.status == CorrelationStatus.TOO_HIGH
        ):
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="Correlation with an active position is too high.",
                rejection_code="CORRELATION_TOO_HIGH",
                score=0.0,
            )

        if not risk_plan.valid or risk_plan.status != RiskPlanStatus.VALID:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=risk_plan.reason,
                rejection_code="RISK_PLAN_MISSING",
                score=0.0,
            )

        return ValidationResult.success(
            layer_name=_LAYER_NAME,
            reason="RISK_MANAGEMENT_VALID",
            score=0.0,
        )
