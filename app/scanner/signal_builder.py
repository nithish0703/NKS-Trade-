"""
Builds structured signal objects from validated setups.
"""

from app.models.signal import Direction, MarketRegime, Signal, SignalType
from app.risk.results import RiskPlanStatus
from app.scanner.pipeline_results import PipelineStatus, StrategyPipelineResult
from app.scanner.scan_results import PairScanResult, PairScanStatus
from app.scoring.results import ConfidenceClassification
from app.utils.identifiers import make_setup_key, make_trade_id
from app.validators.session_filter import SessionFilter

_PUBLISHABLE_CLASSIFICATIONS = (ConfidenceClassification.PREMIUM, ConfidenceClassification.STRONG)

_CLASSIFICATION_TO_SIGNAL_TYPE = {
    ConfidenceClassification.PREMIUM: SignalType.PREMIUM,
    ConfidenceClassification.STRONG: SignalType.STRONG,
}


class SignalBuildError(Exception):
    """Raised when a final Signal cannot be built from a PairScanResult."""


class InstitutionalSignalBuilder:
    """
    Builds a final, immutable Signal from a VALID PairScanResult,
    reusing only already-computed pipeline/risk/confidence results.

    Never recalculates market structure, indicators, liquidity, zones,
    risk management, or confidence scoring, and never builds a signal
    from a MEDIUM, rejected, duplicate, or errored scanner result.
    """

    def __init__(self) -> None:
        self._session_filter = SessionFilter()

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

        confidence_result = pipeline_result.confidence_result
        risk_plan = pipeline_result.risk_plan
        sweep = pipeline_result.liquidity_sweep
        zone = pipeline_result.selected_entry_zone
        structure_break = pipeline_result.selected_structure_break
        retest = pipeline_result.retest_result

        stop_loss = risk_plan.stop_loss_result.selected_stop_loss
        take_profit = risk_plan.take_profit_result.selected_take_profit
        if stop_loss is None:
            raise SignalBuildError("RiskPlan has no selected stop loss.")
        if take_profit is None:
            raise SignalBuildError("RiskPlan has no selected take profit.")

        risk_reward_ratio = risk_plan.risk_reward_ratio
        if risk_reward_ratio is None or risk_reward_ratio < 2.0:
            raise SignalBuildError("RiskPlan risk_reward_ratio must be at least 2.0.")

        setup_key = make_setup_key(
            symbol=pipeline_result.symbol,
            direction=pipeline_result.expected_direction,
            sweep_id=sweep.sweep_id,
            zone_id=zone.zone_id,
            break_id=structure_break.break_id,
            retest_id=retest.retest_id,
        )
        trade_id = make_trade_id(
            symbol=pipeline_result.symbol,
            direction=pipeline_result.expected_direction,
            setup_key=setup_key,
            detection_time_utc=pipeline_result.detection_time_utc,
        )

        session = self._session_filter.detect_session(pipeline_result.detection_time_utc)

        institutional_reason = self._build_institutional_reason(
            pipeline_result=pipeline_result,
            session=session.value,
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
            confidence_score=confidence_result.normalized_score,
            signal_type=_CLASSIFICATION_TO_SIGNAL_TYPE[confidence_result.classification],
            market_regime=MarketRegime.TRENDING,
            higher_timeframe_bias=pipeline_result.htf_bias_result.final_bias.value,
            liquidity_type=sweep.liquidity_level.liquidity_type.value,
            entry_zone_type=zone.zone_type.value,
            structure_confirmation=structure_break.break_type.value,
            volume_confirmation=pipeline_result.volume_validation.passed,
            atr_status="EXPANDING",
            trading_session=session.value,
            btc_market_alignment=pipeline_result.btc_alignment_result.passed,
            detection_time_utc=pipeline_result.detection_time_utc,
            institutional_reason=institutional_reason,
            setup_key=setup_key,
            liquidity_sweep_id=sweep.sweep_id,
            structure_break_id=structure_break.break_id,
            entry_zone_id=zone.zone_id,
            retest_id=retest.retest_id,
            created_at_utc=pipeline_result.detection_time_utc,
        )

    @staticmethod
    def _validate_pipeline_result(pipeline_result: StrategyPipelineResult) -> None:
        if pipeline_result.status != PipelineStatus.VALID:
            raise SignalBuildError("Pipeline result status must be VALID.")

        confidence_result = pipeline_result.confidence_result
        if confidence_result is None or not confidence_result.publishable:
            raise SignalBuildError("Pipeline confidence result must be publishable.")
        if confidence_result.classification not in _PUBLISHABLE_CLASSIFICATIONS:
            raise SignalBuildError("Pipeline confidence classification must be PREMIUM or STRONG.")

        risk_plan = pipeline_result.risk_plan
        if risk_plan is None or risk_plan.status != RiskPlanStatus.VALID:
            raise SignalBuildError("Pipeline risk plan must be valid.")

        if pipeline_result.liquidity_sweep is None:
            raise SignalBuildError("Pipeline result has no selected liquidity sweep.")
        if pipeline_result.selected_structure_break is None:
            raise SignalBuildError("Pipeline result has no selected structure break.")
        if pipeline_result.selected_entry_zone is None:
            raise SignalBuildError("Pipeline result has no selected entry zone.")
        if pipeline_result.retest_result is None:
            raise SignalBuildError("Pipeline result has no retest confirmation.")
        if pipeline_result.htf_bias_result is None:
            raise SignalBuildError("Pipeline result has no higher-timeframe bias result.")
        if pipeline_result.expected_direction is None:
            raise SignalBuildError("Pipeline result has no expected direction.")

    @staticmethod
    def _build_institutional_reason(
        *,
        pipeline_result: StrategyPipelineResult,
        session: str,
        stop_loss_source,
        take_profit_source,
        risk_reward_ratio: float,
    ) -> str:
        htf_bias = pipeline_result.htf_bias_result.final_bias.value
        liquidity_type = pipeline_result.liquidity_sweep.liquidity_level.liquidity_type.value
        structure_type = pipeline_result.selected_structure_break.break_type.value
        zone_type = pipeline_result.selected_entry_zone.zone_type.value

        dealing_range_result = pipeline_result.dealing_range_result
        range_position = (
            dealing_range_result.position.value if dealing_range_result is not None else "UNKNOWN"
        )

        volume_confirmed = pipeline_result.volume_validation.passed
        btc_aligned = pipeline_result.btc_alignment_result.passed

        stop_loss_source_text = stop_loss_source.value if stop_loss_source is not None else "UNKNOWN"
        take_profit_source_text = (
            take_profit_source.value if take_profit_source is not None else "UNKNOWN"
        )

        return (
            f"HTF bias {htf_bias} confirmed by a {liquidity_type} liquidity sweep and a "
            f"{structure_type} structure shift. Entry at a {zone_type} zone in the "
            f"{range_position} of the dealing range, confirmed by a retest. "
            f"Volume {'confirmed' if volume_confirmed else 'not confirmed'} on displacement/retest. "
            f"{session} session. BTC alignment {'confirmed' if btc_aligned else 'not confirmed'}. "
            f"Stop loss sourced from {stop_loss_source_text}, take profit sourced from "
            f"{take_profit_source_text}, risk-reward ratio {risk_reward_ratio:.2f}. "
            f"This reflects confirmed setup facts only and is not a guarantee of outcome."
        )
