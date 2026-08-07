"""
Builds structured signal objects from validated setups.
"""

from app.config.thresholds import MIN_RISK_REWARD_RATIO
from app.models.signal import Direction, Signal, SignalStatus
from app.risk.results import RiskPlanStatus
from app.scanner.pipeline_results import PipelineStatus, StrategyPipelineResult
from app.scanner.scan_results import PairScanResult, PairScanStatus
from app.utils.identifiers import make_setup_key, make_trade_id


class SignalBuildError(Exception):
    """Raised when a final Signal cannot be built from a PairScanResult."""


class InstitutionalSignalBuilder:
    """
    Builds a final, immutable Signal from a VALID PairScanResult,
    reusing only already-computed pipeline/risk results.

    A Signal is built whenever every required strategy condition
    (all hard-mandatory pipeline stages: HTF Bias, Liquidity Sweep,
    BOS, IFVG, Order Flow, Risk Management) has passed -- there is no
    intermediate score or confidence tier gating this. Never
    recalculates market structure, indicators, liquidity, zones, or
    risk management, and never builds a signal from a rejected,
    duplicate, or errored scanner result.
    """

    def build(self, pair_scan_result: PairScanResult) -> Signal:
        if pair_scan_result.status != PairScanStatus.VALID:
            raise SignalBuildError(
                f"Cannot build a signal from a PairScanResult with status "
                f"{pair_scan_result.status.value}; only VALID results are eligible."
            )

        pipeline_result = pair_scan_result.pipeline_result
        if pipeline_result is None:
            raise SignalBuildError("PairScanResult has no pipeline_result.")

        self._validate_pipeline_result(pipeline_result)

        risk_plan = pipeline_result.risk_plan
        sweep = pipeline_result.liquidity_sweep
        zone = pipeline_result.selected_entry_zone
        structure_break = pipeline_result.selected_structure_break

        stop_loss = risk_plan.stop_loss_result.selected_stop_loss
        take_profit = risk_plan.take_profit_result.selected_take_profit
        if stop_loss is None:
            raise SignalBuildError("RiskPlan has no selected stop loss.")
        if take_profit is None:
            raise SignalBuildError("RiskPlan has no selected take profit.")

        risk_reward_ratio = risk_plan.risk_reward_ratio
        if risk_reward_ratio is None or risk_reward_ratio < MIN_RISK_REWARD_RATIO:
            raise SignalBuildError(
                f"RiskPlan risk_reward_ratio must be at least {MIN_RISK_REWARD_RATIO}."
            )

        setup_key = make_setup_key(
            symbol=pipeline_result.symbol,
            direction=pipeline_result.expected_direction,
            sweep_id=sweep.sweep_id,
            zone_id=zone.zone_id,
            break_id=structure_break.break_id,
            retest_id=zone.zone_id,
        )
        trade_id = make_trade_id(
            symbol=pipeline_result.symbol,
            direction=pipeline_result.expected_direction,
            setup_key=setup_key,
            detection_time_utc=pipeline_result.detection_time_utc,
        )

        institutional_reason = self._build_institutional_reason(
            pipeline_result=pipeline_result,
            stop_loss_source=risk_plan.stop_loss_result.selected_source,
            take_profit_source=risk_plan.take_profit_result.selected_source,
            risk_reward_ratio=risk_reward_ratio,
        )

        return Signal(
            trade_id=trade_id,
            coin=pipeline_result.symbol,
            direction=Direction(pipeline_result.expected_direction),
            entry_price=risk_plan.entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward_ratio=risk_reward_ratio,
            status=SignalStatus.CONFIRMED,
            liquidity_type=sweep.liquidity_level.liquidity_type.value,
            entry_zone_type=zone.zone_type.value,
            structure_confirmation=structure_break.break_type.value,
            detection_time_utc=pipeline_result.detection_time_utc,
            institutional_reason=institutional_reason,
            setup_key=setup_key,
            liquidity_sweep_id=sweep.sweep_id,
            structure_break_id=structure_break.break_id,
            entry_zone_id=zone.zone_id,
            created_at_utc=pipeline_result.detection_time_utc,
        )

    @staticmethod
    def _validate_pipeline_result(pipeline_result: StrategyPipelineResult) -> None:
        if pipeline_result.status != PipelineStatus.VALID:
            raise SignalBuildError("Pipeline result status must be VALID.")

        risk_plan = pipeline_result.risk_plan
        if risk_plan is None or risk_plan.status != RiskPlanStatus.VALID:
            raise SignalBuildError("Pipeline risk plan must be valid.")

        if pipeline_result.liquidity_sweep is None:
            raise SignalBuildError("Pipeline result has no selected liquidity sweep.")
        if pipeline_result.selected_structure_break is None:
            raise SignalBuildError("Pipeline result has no selected structure break.")
        if pipeline_result.selected_entry_zone is None:
            raise SignalBuildError("Pipeline result has no selected entry zone.")
        if pipeline_result.expected_direction is None:
            raise SignalBuildError("Pipeline result has no expected direction.")

    @staticmethod
    def _build_institutional_reason(
        *,
        pipeline_result: StrategyPipelineResult,
        stop_loss_source,
        take_profit_source,
        risk_reward_ratio: float,
    ) -> str:
        direction = pipeline_result.expected_direction
        liquidity_type = pipeline_result.liquidity_sweep.liquidity_level.liquidity_type.value
        structure_type = pipeline_result.selected_structure_break.break_type.value
        zone_type = pipeline_result.selected_entry_zone.zone_type.value

        stop_loss_source_text = stop_loss_source.value if stop_loss_source is not None else "UNKNOWN"
        take_profit_source_text = (
            take_profit_source.value if take_profit_source is not None else "UNKNOWN"
        )

        return (
            f"{direction} bias confirmed by a {liquidity_type} liquidity sweep and a "
            f"{structure_type} structure break (BOS). Entry at an inverted Fair Value "
            f"Gap ({zone_type}) that flipped and was retested, with order flow "
            f"(Volume Profile + CVD) agreeing with the trade direction. "
            f"Stop loss sourced from {stop_loss_source_text}, take profit sourced from "
            f"{take_profit_source_text}, risk-reward ratio {risk_reward_ratio:.2f}. "
            f"This reflects confirmed setup facts only and is not a guarantee of outcome."
        )
