"""
Orchestrates stop loss, take profit, position sizing, correlation, and
active-trade/averaging guards into a single risk plan.
"""

from typing import Mapping, Optional, Sequence

from app.config.thresholds import MIN_RISK_REWARD_RATIO
from app.liquidity.results import LiquidityLevel, LiquiditySweepResult
from app.market_structure.results import SwingPoint
from app.models.candle import Candle
from app.models.trade_zone import TradeZone

from app.risk.active_trade_guard import ActiveTradeGuard
from app.risk.averaging_guard import AveragingGuard
from app.risk.correlation_filter import CorrelationFilter
from app.risk.position_risk import PositionRiskCalculator
from app.risk.results import (
    CorrelationResult,
    CorrelationStatus,
    PositionRiskResult,
    RiskPlan,
    RiskPlanStatus,
    StopLossResult,
    TakeProfitResult,
)
from app.risk.risk_reward import validate_minimum_risk_reward
from app.risk.stop_loss import DynamicStopLossCalculator
from app.risk.take_profit import SingleTakeProfitCalculator


class RiskManagementCalculator:
    """
    Coordinates the full risk-management pipeline: active-trade limit,
    averaging guard, dynamic stop loss, single take profit, position
    sizing, and correlation, producing a single RiskPlan.
    """

    def __init__(
        self,
        stop_loss_calculator: DynamicStopLossCalculator,
        take_profit_calculator: SingleTakeProfitCalculator,
        position_risk_calculator: PositionRiskCalculator,
        correlation_filter: CorrelationFilter,
        active_trade_guard: ActiveTradeGuard,
        averaging_guard: AveragingGuard,
    ) -> None:
        self._stop_loss_calculator = stop_loss_calculator
        self._take_profit_calculator = take_profit_calculator
        self._position_risk_calculator = position_risk_calculator
        self._correlation_filter = correlation_filter
        self._active_trade_guard = active_trade_guard
        self._averaging_guard = averaging_guard

    def calculate(
        self,
        direction: str,
        entry_price: float,
        atr: float,
        selected_zone: TradeZone,
        liquidity_sweep: LiquiditySweepResult,
        institutional_liquidity_levels: Sequence[LiquidityLevel],
        major_swings: Sequence[SwingPoint],
        fair_value_gaps: Sequence[TradeZone],
        account_balance: float,
        active_trade_count: int,
        candidate_symbol: str,
        candidate_candles: Sequence[Candle],
        active_position_candles: Mapping[str, Sequence[Candle]],
        active_positions: Sequence[object],
        requested_risk_percentage: Optional[float] = None,
    ) -> RiskPlan:
        """
        Calculate the complete risk plan.

        Execution order: active-trade limit, averaging guard, dynamic
        stop loss, single take profit, position risk, correlation. Any
        mandatory failure produces an INVALID/REJECTED RiskPlan with all
        available component results preserved for audit; no partial
        valid result is ever returned.
        """
        active_trade_validation = self._active_trade_guard.validate(active_trade_count)
        if not active_trade_validation.passed:
            return self._rejected_plan(
                direction,
                entry_price,
                active_trade_count,
                reason=active_trade_validation.reason or "Active trade limit reached.",
            )

        averaging_validation = self._averaging_guard.validate(
            candidate_symbol, direction, active_positions
        )
        if not averaging_validation.passed:
            return self._rejected_plan(
                direction,
                entry_price,
                active_trade_count,
                reason=averaging_validation.reason or "Averaging into a losing position is rejected.",
            )

        stop_loss_result = self._stop_loss_calculator.calculate(
            direction, entry_price, atr, selected_zone, liquidity_sweep
        )
        if not stop_loss_result.valid:
            return self._invalid_plan(
                direction,
                entry_price,
                active_trade_count,
                stop_loss_result=stop_loss_result,
                reason="No valid dynamic stop loss could be calculated.",
            )

        take_profit_result = self._take_profit_calculator.calculate(
            direction,
            entry_price,
            stop_loss_result.selected_stop_loss,
            institutional_liquidity_levels,
            major_swings,
            fair_value_gaps,
        )
        if not take_profit_result.valid:
            return self._invalid_plan(
                direction,
                entry_price,
                active_trade_count,
                stop_loss_result=stop_loss_result,
                take_profit_result=take_profit_result,
                reason="No take-profit target meets the minimum risk-reward ratio.",
            )

        risk_reward_ratio = take_profit_result.risk_reward_ratio
        rr_validation = validate_minimum_risk_reward(risk_reward_ratio, MIN_RISK_REWARD_RATIO)
        if not rr_validation.passed:
            return self._invalid_plan(
                direction,
                entry_price,
                active_trade_count,
                stop_loss_result=stop_loss_result,
                take_profit_result=take_profit_result,
                risk_reward_ratio=risk_reward_ratio,
                reason=rr_validation.reason or "Risk-reward ratio is below the minimum.",
            )

        position_risk_result = self._position_risk_calculator.calculate(
            account_balance,
            entry_price,
            stop_loss_result.selected_stop_loss,
            direction,
            requested_risk_percentage,
        )
        if not position_risk_result.valid:
            return self._invalid_plan(
                direction,
                entry_price,
                active_trade_count,
                stop_loss_result=stop_loss_result,
                take_profit_result=take_profit_result,
                risk_reward_ratio=risk_reward_ratio,
                position_risk_result=position_risk_result,
                reason="Position risk calculation is invalid.",
            )

        correlation_result = self._correlation_filter.evaluate(
            candidate_symbol, candidate_candles, active_position_candles
        )
        if not correlation_result.acceptable:
            return self._invalid_plan(
                direction,
                entry_price,
                active_trade_count,
                stop_loss_result=stop_loss_result,
                take_profit_result=take_profit_result,
                risk_reward_ratio=risk_reward_ratio,
                position_risk_result=position_risk_result,
                correlation_result=correlation_result,
                reason="Correlation with an active position is too high.",
            )

        return RiskPlan(
            direction=direction,
            entry_price=entry_price,
            stop_loss_result=stop_loss_result,
            take_profit_result=take_profit_result,
            position_risk=position_risk_result,
            correlation_result=correlation_result,
            active_trade_count=active_trade_count,
            maximum_active_trades=self._active_trade_guard.maximum_active_trades,
            risk_reward_ratio=risk_reward_ratio,
            status=RiskPlanStatus.VALID,
            valid=True,
            reason="Risk plan is valid: stop loss, take profit, position risk, and correlation all passed.",
        )

    def _rejected_plan(
        self, direction: str, entry_price: float, active_trade_count: int, reason: str
    ) -> RiskPlan:
        placeholder_stop_loss = StopLossResult(
            direction=direction,
            entry_price=entry_price,
            candidates=[],
            valid=False,
            reason="Not calculated: an earlier mandatory guard rejected the trade.",
        )
        placeholder_take_profit = TakeProfitResult(
            direction=direction,
            entry_price=entry_price,
            stop_loss=entry_price,
            candidates=[],
            valid=False,
            reason="Not calculated: an earlier mandatory guard rejected the trade.",
        )
        placeholder_position_risk = PositionRiskResult(
            account_balance=1.0,
            risk_percentage=0.01,
            entry_price=entry_price,
            stop_loss=entry_price,
            valid=False,
            reason="Not calculated: an earlier mandatory guard rejected the trade.",
        )
        placeholder_correlation = CorrelationResult(
            candidate_symbol="",
            active_symbols=[],
            maximum_allowed_correlation=0.0,
            observed_correlations={},
            status=CorrelationStatus.DATA_MISSING,
            acceptable=False,
            reason="Not calculated: an earlier mandatory guard rejected the trade.",
        )
        return RiskPlan(
            direction=direction,
            entry_price=entry_price,
            stop_loss_result=placeholder_stop_loss,
            take_profit_result=placeholder_take_profit,
            position_risk=placeholder_position_risk,
            correlation_result=placeholder_correlation,
            active_trade_count=active_trade_count,
            maximum_active_trades=self._active_trade_guard.maximum_active_trades,
            risk_reward_ratio=None,
            status=RiskPlanStatus.REJECTED,
            valid=False,
            reason=reason,
        )

    def _invalid_plan(
        self,
        direction: str,
        entry_price: float,
        active_trade_count: int,
        reason: str,
        stop_loss_result: Optional[StopLossResult] = None,
        take_profit_result: Optional[TakeProfitResult] = None,
        risk_reward_ratio: Optional[float] = None,
        position_risk_result: Optional[PositionRiskResult] = None,
        correlation_result: Optional[CorrelationResult] = None,
    ) -> RiskPlan:
        stop_loss = stop_loss_result or StopLossResult(
            direction=direction, entry_price=entry_price, candidates=[], valid=False, reason="Not calculated."
        )
        take_profit = take_profit_result or TakeProfitResult(
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss.selected_stop_loss or entry_price,
            candidates=[],
            valid=False,
            reason="Not calculated.",
        )
        position_risk = position_risk_result or PositionRiskResult(
            account_balance=1.0,
            risk_percentage=0.01,
            entry_price=entry_price,
            stop_loss=stop_loss.selected_stop_loss or entry_price,
            valid=False,
            reason="Not calculated.",
        )
        correlation = correlation_result or CorrelationResult(
            candidate_symbol="",
            active_symbols=[],
            maximum_allowed_correlation=0.0,
            observed_correlations={},
            status=CorrelationStatus.DATA_MISSING,
            acceptable=False,
            reason="Not calculated.",
        )
        return RiskPlan(
            direction=direction,
            entry_price=entry_price,
            stop_loss_result=stop_loss,
            take_profit_result=take_profit,
            position_risk=position_risk,
            correlation_result=correlation,
            active_trade_count=active_trade_count,
            maximum_active_trades=self._active_trade_guard.maximum_active_trades,
            risk_reward_ratio=risk_reward_ratio,
            status=RiskPlanStatus.INVALID,
            valid=False,
            reason=reason,
        )
