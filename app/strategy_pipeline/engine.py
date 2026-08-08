"""
Institutional strategy pipeline engine: the sole strategy engine used
by the live scanner, dashboard, and Telegram notification path.

Flow:

    HTF Bias (1H)
        v
    Liquidity Sweep
        v
    BOS (Trade idea valid?)
        v
    IFVG (Good entry location?)
        v
    Volume Profile + CVD (Order flow confidence -- soft, non-blocking)
        v
    Signal

Every dependency is injected; this class does not construct its own
calculators or detectors. It never mutates input candles, contexts,
positions, or calculation results, and never persists, publishes, or
executes a trade. Exposes `analyze_symbol(...) -> StrategyPipelineResult`,
the interface `app.scanner.pair_scanner.PairScanner` depends on.
"""

import time
from datetime import datetime, timezone
from typing import Mapping, Optional, Sequence

from app.config.pairs import validate_pair_symbol
from app.config.thresholds import ENTRY_PRICE_ANCHOR, ENTRY_ZONE_MAX_DISTANCE_ATR
from app.config.timeframes import ENTRY_TIMEFRAME, HTF_SECONDARY
from app.data.market_data_errors import MarketDataError
from app.data.provider_base import MarketDataProvider
from app.indicators.calculator import IndicatorCalculator
from app.indicators.ema import IndicatorCalculationError
from app.liquidity.calculator import LiquidityCalculator
from app.market_structure.bos_detector import BOSDetector
from app.market_structure.calculator import MarketStructureCalculator
from app.market_structure.displacement import DisplacementDetector, StructureShiftCalculationError
from app.models.candle import Candle
from app.models.market_context import MarketContext
from app.models.trade_zone import TradeZone
from app.models.validation_result import ValidationResult
from app.risk.calculator import RiskManagementCalculator
from app.zones.fair_value_gap import FairValueGapDetector

from app.scanner.pipeline_exceptions import (
    PipelineDataUnavailableError,
    PipelineInputError,
    PipelineStageError,
)
from app.scanner.pipeline_results import PipelineStageResult, PipelineStatus, StrategyPipelineResult

from app.strategy_pipeline.bos import evaluate_bos
from app.strategy_pipeline.ema_trend import evaluate_ema_trend
from app.strategy_pipeline.htf_bias import HtfBiasDirection, evaluate_htf_bias
from app.strategy_pipeline.ifvg import evaluate_ifvg
from app.strategy_pipeline.liquidity_sweep import validate_liquidity_sweep
from app.strategy_pipeline.order_flow import OrderFlowResult, evaluate_order_flow
from app.strategy_pipeline.premium_discount import evaluate_premium_discount_zone
from app.strategy_pipeline.scoring import calculate_pipeline_decision

# 4h (HTF_PRIMARY) is intentionally excluded: no stage in this pipeline
# reads it, so fetching it was 300 wasted candles (weight 2 on Binance
# Futures) per symbol per scan cycle for nothing. Phase 5 reintroduces
# it for BTC-only regime alignment (see HTF_PRIMARY/EXCHANGE_TIMEFRAME_MAP
# in app/config/timeframes.py, both kept for that reason).
_REQUIRED_TIMEFRAMES = (ENTRY_TIMEFRAME, HTF_SECONDARY)

# (stage_order, layer_name). All stages except ORDER_FLOW are
# hard-mandatory: failure at any of those rejects the pipeline
# immediately, matching the diagram's sequential arrows. ORDER_FLOW
# (Volume Profile + CVD) is a soft confidence layer -- it always
# executes once reached and never rejects the pipeline by itself; its
# outcome is a HIGH/MEDIUM/LOW confidence tier carried on the result,
# not a pass/fail gate. There is no numeric score/classification stage
# either: the final gate decision is purely binary CONFIRMED/REJECTED,
# independent of ORDER_FLOW's confidence tier.
_STAGE_DEFINITIONS: tuple[tuple[int, str], ...] = (
    (1, "HTF_BIAS"),
    (2, "LIQUIDITY_SWEEP"),
    (3, "BOS"),
    (4, "IFVG"),
    (5, "ORDER_FLOW"),
    (6, "RISK_MANAGEMENT"),
)
_MANDATORY_STAGES = frozenset(name for _, name in _STAGE_DEFINITIONS if name != "ORDER_FLOW")


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


def _resolve_entry_price(zone: TradeZone, expected_direction: str, last_close: float) -> float:
    """
    Resolve the entry price per ENTRY_PRICE_ANCHOR rather than always
    using the latest closed candle's close, which can sit far from the
    zone the pipeline actually selected. ZONE_MIDPOINT anchors to the
    zone's center; ZONE_EDGE uses the boundary a retracement into the
    zone touches first (the lower edge for a BUY, the upper edge for a
    SELL); LAST_CLOSE preserves the pre-fix behaviour as an explicit,
    named opt-out.
    """
    if ENTRY_PRICE_ANCHOR == "ZONE_MIDPOINT":
        return (zone.lower_price + zone.upper_price) / 2.0
    if ENTRY_PRICE_ANCHOR == "ZONE_EDGE":
        return zone.lower_price if expected_direction == "BUY" else zone.upper_price
    if ENTRY_PRICE_ANCHOR == "LAST_CLOSE":
        return last_close
    raise ValueError(f"Unsupported ENTRY_PRICE_ANCHOR: {ENTRY_PRICE_ANCHOR!r}")


class PipelineStrategyEngine:
    """
    Orchestrates the strategy pipeline (HTF Bias, Liquidity Sweep, BOS,
    IFVG as hard-mandatory gates; Volume Profile+CVD as a soft
    confidence layer; Risk Management as the final hard-mandatory
    gate) for a single symbol.
    """

    def __init__(
        self,
        market_data_provider: MarketDataProvider,
        indicator_calculator: IndicatorCalculator,
        market_structure_calculator: MarketStructureCalculator,
        liquidity_calculator: LiquidityCalculator,
        displacement_detector: DisplacementDetector,
        bos_detector: BOSDetector,
        fair_value_gap_detector: FairValueGapDetector,
        risk_management_calculator: RiskManagementCalculator,
    ) -> None:
        self._market_data_provider = market_data_provider
        self._indicator_calculator = indicator_calculator
        self._market_structure_calculator = market_structure_calculator
        self._liquidity_calculator = liquidity_calculator
        self._displacement_detector = displacement_detector
        self._bos_detector = bos_detector
        self._fair_value_gap_detector = fair_value_gap_detector
        self._risk_management_calculator = risk_management_calculator

    async def analyze_symbol(
        self,
        *,
        symbol: str,
        account_balance: float,
        active_trade_count: int,
        active_positions: Sequence[object],
        active_position_candles: Mapping[str, Sequence[Candle]],
        detection_time_utc: Optional[datetime] = None,
    ) -> StrategyPipelineResult:
        """
        Run the 5-stage strategy pipeline for `symbol` and return the
        final internal StrategyPipelineResult.

        Raises:
            PipelineInputError: If symbol, account_balance, or
                active_trade_count are invalid.
            PipelineDataUnavailableError: If required market data or
                indicator/structure calculations cannot be produced.
            PipelineStageError: If an unexpected technical failure occurs
                inside a pipeline stage.
        """
        detection_time_utc = detection_time_utc or datetime.now(timezone.utc)

        try:
            validated_symbol = validate_pair_symbol(symbol)
        except ValueError as exc:
            raise PipelineInputError(
                layer_name="INPUT_VALIDATION", symbol=symbol, reason=str(exc), original_exception=exc
            ) from exc

        if account_balance is None or account_balance <= 0:
            raise PipelineInputError(
                layer_name="INPUT_VALIDATION",
                symbol=validated_symbol,
                reason="account_balance must be positive.",
            )
        if active_trade_count is None or active_trade_count < 0:
            raise PipelineInputError(
                layer_name="INPUT_VALIDATION",
                symbol=validated_symbol,
                reason="active_trade_count cannot be negative.",
            )

        try:
            context = await self._prepare_market_context(validated_symbol, detection_time_utc)
        except (MarketDataError, IndicatorCalculationError) as exc:
            raise PipelineDataUnavailableError(
                layer_name="MARKET_DATA_PREPARATION",
                symbol=validated_symbol,
                reason="Required market data or indicator calculation is unavailable.",
                original_exception=exc,
            ) from exc

        stages: list[PipelineStageResult] = []
        return await self._run_pipeline(
            context=context,
            account_balance=account_balance,
            active_trade_count=active_trade_count,
            active_positions=active_positions,
            active_position_candles=active_position_candles,
            stages=stages,
        )

    async def _prepare_market_context(self, symbol: str, detection_time_utc: datetime) -> MarketContext:
        """Fetch candles and calculate indicators/structures for the 1H HTF Bias stage."""
        symbol_candles = await self._market_data_provider.fetch_symbol_market_data(symbol)

        for timeframe in _REQUIRED_TIMEFRAMES:
            candles = symbol_candles.get(timeframe)
            if not candles:
                raise PipelineDataUnavailableError(
                    layer_name="MARKET_DATA_PREPARATION",
                    symbol=symbol,
                    reason=f"No candles available for required timeframe '{timeframe}'.",
                )

        indicators_by_timeframe = self._indicator_calculator.calculate_multiple_timeframes(symbol_candles)
        structures_by_timeframe = self._market_structure_calculator.calculate_multiple_timeframes(
            symbol_candles
        )

        return MarketContext(
            symbol=symbol,
            detection_time_utc=detection_time_utc,
            candles_by_timeframe=symbol_candles,
            entry_timeframe=ENTRY_TIMEFRAME,
            indicators_by_timeframe=indicators_by_timeframe,
            structures_by_timeframe=structures_by_timeframe,
        )

    async def _run_pipeline(
        self,
        context: MarketContext,
        account_balance: float,
        active_trade_count: int,
        active_positions: Sequence[object],
        active_position_candles: Mapping[str, Sequence[Candle]],
        stages: list[PipelineStageResult],
    ) -> StrategyPipelineResult:
        symbol = context.symbol
        entry_candles = context.candles_by_timeframe[ENTRY_TIMEFRAME]
        entry_snapshot = context.indicators_by_timeframe[ENTRY_TIMEFRAME]
        entry_structure = context.structures_by_timeframe[ENTRY_TIMEFRAME]
        htf_structure = context.structures_by_timeframe[HTF_SECONDARY]

        stage_results: dict[str, ValidationResult] = {}

        # Stage 1: HTF_BIAS (direction resolution)
        start = _now_ms()
        htf_bias_result = evaluate_htf_bias(htf_structure)
        expected_direction = (
            htf_bias_result.permitted_direction.value
            if htf_bias_result.permitted_direction != HtfBiasDirection.NONE
            else None
        )
        htf_validation = ValidationResult(
            passed=htf_bias_result.passed, layer_name="HTF_BIAS", reason=htf_bias_result.reason
        )

        # EMA trend filter: an additional direction-agreement condition
        # folded into Stage 1 rather than a new stage, reusing the
        # already-computed entry-timeframe EMA200 slope direction (see
        # app/strategy_pipeline/ema_trend.py). Only evaluated once the
        # 1H HTF Bias itself has already permitted a direction.
        if htf_validation.passed and expected_direction is not None:
            ema_trend_result = evaluate_ema_trend(entry_snapshot, expected_direction)
            if not ema_trend_result.passed:
                htf_validation = ValidationResult(
                    passed=False, layer_name="HTF_BIAS", reason=ema_trend_result.reason
                )

        stage_results["HTF_BIAS"] = htf_validation
        stages.append(self._stage(1, "HTF_BIAS", htf_validation, start))
        if not htf_validation.passed or expected_direction is None:
            return self._build_rejected_result(context, expected_direction, stages, "HTF_BIAS", htf_validation)

        context = context.with_updates(expected_direction=expected_direction)

        # Stage 2: LIQUIDITY_SWEEP
        start = _now_ms()
        try:
            liquidity_detection_result = self._liquidity_calculator.detect_levels(
                entry_candles, entry_structure.swings, context.detection_time_utc
            )
            confirmed_sweeps = self._liquidity_calculator.detect_sweeps(entry_candles, liquidity_detection_result)
        except Exception as exc:  # noqa: BLE001
            raise PipelineStageError(
                layer_name="LIQUIDITY_SWEEP", symbol=symbol, reason="Liquidity detection failed.",
                original_exception=exc,
            ) from exc

        selected_sweep = self._select_latest_valid_sweep(confirmed_sweeps, expected_direction, context.detection_time_utc)
        sweep_result = validate_liquidity_sweep(
            selected_sweep, entry_candles, expected_direction=expected_direction
        )
        sweep_validation = ValidationResult(
            passed=sweep_result.passed, layer_name="LIQUIDITY_SWEEP", reason=sweep_result.reason
        )
        stage_results["LIQUIDITY_SWEEP"] = sweep_validation
        stages.append(self._stage(2, "LIQUIDITY_SWEEP", sweep_validation, start))
        context = context.with_updates(
            liquidity_detection_result=liquidity_detection_result,
            liquidity_sweeps=confirmed_sweeps,
            selected_liquidity_sweep=selected_sweep,
        )
        if not sweep_validation.passed:
            return self._build_rejected_result(context, expected_direction, stages, "LIQUIDITY_SWEEP", sweep_validation)

        # Stage 3: BOS (trade idea valid?)
        start = _now_ms()
        volume_ema_values = [entry_snapshot.volume_ema20] * len(entry_candles)
        try:
            displacement_results = self._displacement_detector.detect(entry_candles, volume_ema_values)
        except StructureShiftCalculationError as exc:
            raise PipelineStageError(
                layer_name="BOS", symbol=symbol, reason="Displacement calculation failed.",
                original_exception=exc,
            ) from exc

        bos_result = evaluate_bos(
            candles=entry_candles,
            swings=entry_structure.swings,
            displacement_results=displacement_results,
            liquidity_sweeps=confirmed_sweeps,
            validated_sweep=selected_sweep,
            bos_detector=self._bos_detector,
            expected_direction=expected_direction,
        )
        bos_validation = ValidationResult(passed=bos_result.passed, layer_name="BOS", reason=bos_result.reason)
        stage_results["BOS"] = bos_validation
        stages.append(self._stage(3, "BOS", bos_validation, start))
        context = context.with_updates(selected_structure_break=bos_result.structure_break)
        if not bos_validation.passed:
            return self._build_rejected_result(context, expected_direction, stages, "BOS", bos_validation)

        # Stage 4: IFVG (good entry location?)
        start = _now_ms()
        structure_breaks = [bos_result.structure_break] if bos_result.structure_break else []
        try:
            fair_value_gaps = self._fair_value_gap_detector.detect(entry_candles, structure_breaks)
        except Exception as exc:  # noqa: BLE001
            raise PipelineStageError(
                layer_name="IFVG", symbol=symbol, reason="Fair Value Gap detection failed.",
                original_exception=exc,
            ) from exc

        ifvg_result = evaluate_ifvg(
            entry_candles,
            fair_value_gaps,
            expected_direction=expected_direction,
            structure_break=bos_result.structure_break,
        )
        ifvg_validation = ValidationResult(passed=ifvg_result.passed, layer_name="IFVG", reason=ifvg_result.reason)

        # Premium/Discount dealing-range filter: an additional
        # entry-location condition folded into Stage 4 rather than a
        # new stage, reusing the existing swing range already on
        # entry_structure (see app/strategy_pipeline/premium_discount.py).
        # Only evaluated once IFVG has already selected a valid entry
        # zone, using that zone's own anchored price (see
        # _resolve_entry_price / ENTRY_PRICE_ANCHOR) as the entry price
        # under test -- not the latest closed candle's close, which may
        # sit far from the selected zone.
        if ifvg_validation.passed and ifvg_result.selected_zone is not None:
            anchored_entry_price = _resolve_entry_price(
                ifvg_result.selected_zone, expected_direction, entry_candles[-1].close
            )
            premium_discount_result = evaluate_premium_discount_zone(
                entry_structure, anchored_entry_price, expected_direction
            )
            if not premium_discount_result.passed:
                ifvg_validation = ValidationResult(
                    passed=False, layer_name="IFVG", reason=premium_discount_result.reason
                )

        stage_results["IFVG"] = ifvg_validation
        stages.append(self._stage(4, "IFVG", ifvg_validation, start))
        context = context.with_updates(selected_entry_zone=ifvg_result.selected_zone)
        if not ifvg_validation.passed:
            return self._build_rejected_result(context, expected_direction, stages, "IFVG", ifvg_validation)

        # Stage 5: VOLUME_PROFILE + CVD (order flow confidence -- soft,
        # non-blocking). Always executes once reached and never rejects
        # the pipeline: Stages 1-4 above are the hard-mandatory gate,
        # and the pipeline always continues to Stage 6 from here. The
        # resulting HIGH/MEDIUM/LOW confidence tier is carried on the
        # result for display/notification only.
        start = _now_ms()
        order_flow_result = evaluate_order_flow(
            entry_candles,
            expected_direction=expected_direction,
        )
        order_flow_validation = ValidationResult(
            passed=True, layer_name="ORDER_FLOW", reason=order_flow_result.reason
        )
        stage_results["ORDER_FLOW"] = order_flow_validation
        stages.append(
            self._stage(
                5,
                "ORDER_FLOW",
                order_flow_validation,
                start,
                mandatory=False,
                metadata={
                    "confidence": order_flow_result.confidence.value,
                    "volume_profile_passed": order_flow_result.volume_profile.passed,
                    "cvd_passed": order_flow_result.cvd.passed,
                },
            )
        )

        # Stage 6: RISK_MANAGEMENT
        start = _now_ms()
        entry_zone = ifvg_result.selected_zone
        atr = entry_snapshot.atr
        if entry_zone is None or atr is None or atr <= 0:
            risk_validation = ValidationResult.failure(
                layer_name="RISK_MANAGEMENT",
                reason="Entry zone or ATR is unavailable for risk calculation.",
            )
            stages.append(self._stage(6, "RISK_MANAGEMENT", risk_validation, start, mandatory=True))
            return self._build_rejected_result(
                context,
                expected_direction,
                stages,
                "RISK_MANAGEMENT",
                risk_validation,
                order_flow_result=order_flow_result,
            )

        last_close = entry_candles[-1].close
        entry_price = _resolve_entry_price(entry_zone, expected_direction, last_close)

        distance_atr = abs(last_close - entry_price) / atr
        if distance_atr > ENTRY_ZONE_MAX_DISTANCE_ATR:
            stale_validation = ValidationResult.failure(
                layer_name="RISK_MANAGEMENT",
                reason=(
                    f"price has already left the entry zone ({distance_atr:.1f} ATR away); "
                    "the setup is stale."
                ),
            )
            stages.append(self._stage(6, "RISK_MANAGEMENT", stale_validation, start, mandatory=True))
            return self._build_rejected_result(
                context,
                expected_direction,
                stages,
                "RISK_MANAGEMENT",
                stale_validation,
                order_flow_result=order_flow_result,
            )

        risk_plan = self._risk_management_calculator.calculate(
            direction=expected_direction,
            entry_price=entry_price,
            atr=atr,
            selected_zone=entry_zone,
            liquidity_sweep=selected_sweep,
            institutional_liquidity_levels=liquidity_detection_result.active_levels,
            major_swings=entry_structure.swings,
            fair_value_gaps=fair_value_gaps,
            account_balance=account_balance,
            active_trade_count=active_trade_count,
            candidate_symbol=symbol,
            candidate_candles=entry_candles,
            active_position_candles=active_position_candles,
            active_positions=active_positions,
        )
        risk_validation = ValidationResult(
            passed=risk_plan.valid, layer_name="RISK_MANAGEMENT", reason=risk_plan.reason
        )
        stages.append(self._stage(6, "RISK_MANAGEMENT", risk_validation, start))
        context = context.with_updates(risk_plan=risk_plan)
        if not risk_plan.valid:
            return self._build_rejected_result(
                context,
                expected_direction,
                stages,
                "RISK_MANAGEMENT",
                risk_validation,
                order_flow_result=order_flow_result,
            )

        # Final binary decision: every hard-mandatory stage above
        # already rejected immediately on its own failure, so by
        # construction every stage_results entry is passed=True here
        # (ORDER_FLOW's soft confidence tier never contributes a
        # failure). This aggregation is kept as an explicit audit
        # record ("all required conditions were satisfied") rather than
        # inlining a bare True -- it never computes or exposes a score,
        # percentage, or classification tier of its own.
        pipeline_decision = calculate_pipeline_decision(stage_results)

        return StrategyPipelineResult(
            symbol=symbol,
            expected_direction=expected_direction,
            detection_time_utc=context.detection_time_utc,
            status=PipelineStatus.VALID if pipeline_decision.confirmed else PipelineStatus.REJECTED,
            passed=pipeline_decision.confirmed,
            failed_layer=None if pipeline_decision.confirmed else "PIPELINE_DECISION",
            rejection_reason=None if pipeline_decision.confirmed else pipeline_decision.reason,
            market_context=context,
            stages=stages,
            liquidity_detection_result=liquidity_detection_result,
            liquidity_sweep=selected_sweep,
            selected_structure_break=bos_result.structure_break,
            selected_entry_zone=entry_zone,
            risk_plan=risk_plan,
            order_flow_confidence=order_flow_result.confidence.value,
            order_flow_reason=order_flow_result.reason,
            entry_grade=ifvg_result.entry_grade,
        )

    @staticmethod
    def _select_latest_valid_sweep(confirmed_sweeps, expected_direction: str, detection_time_utc: datetime):
        from app.liquidity.results import SweepDirection

        required_direction = SweepDirection.BULLISH if expected_direction == "BUY" else SweepDirection.BEARISH
        eligible = [
            sweep
            for sweep in confirmed_sweeps
            if sweep.confirmed
            and sweep.direction == required_direction
            and sweep.sweep_candle_timestamp < detection_time_utc
        ]
        if not eligible:
            return None
        return max(eligible, key=lambda s: s.sweep_candle_timestamp)

    @staticmethod
    def _stage(
        stage_order: int,
        layer_name: str,
        validation_result: ValidationResult,
        start_ms: float,
        mandatory: bool = True,
        metadata: Optional[dict] = None,
    ) -> PipelineStageResult:
        duration = max(0.0, _now_ms() - start_ms) if start_ms else 0.0
        return PipelineStageResult(
            stage_order=stage_order,
            layer_name=layer_name,
            mandatory=mandatory,
            executed=True,
            passed=validation_result.passed,
            reason=validation_result.reason,
            validation_result=validation_result,
            duration_ms=duration,
            metadata=metadata,
        )

    def _build_rejected_result(
        self,
        context: MarketContext,
        expected_direction: Optional[str],
        stages: list[PipelineStageResult],
        failed_layer: str,
        failed_validation: ValidationResult,
        order_flow_result: Optional[OrderFlowResult] = None,
    ) -> StrategyPipelineResult:
        executed_orders = {s.stage_order for s in stages}
        remaining_stages = list(stages)
        for stage_order, layer_name in _STAGE_DEFINITIONS:
            if stage_order in executed_orders:
                continue
            remaining_stages.append(
                PipelineStageResult(
                    stage_order=stage_order,
                    layer_name=layer_name,
                    mandatory=layer_name in _MANDATORY_STAGES,
                    executed=False,
                    passed=False,
                    reason="Not executed: pipeline stopped at an earlier stage.",
                    validation_result=None,
                    duration_ms=0.0,
                )
            )
        remaining_stages.sort(key=lambda s: s.stage_order)

        return StrategyPipelineResult(
            symbol=context.symbol,
            expected_direction=expected_direction,
            detection_time_utc=context.detection_time_utc,
            status=PipelineStatus.REJECTED,
            passed=False,
            failed_layer=failed_layer,
            rejection_reason=failed_validation.reason or f"{failed_layer} validation failed.",
            market_context=context,
            stages=remaining_stages,
            order_flow_confidence=order_flow_result.confidence.value if order_flow_result else None,
            order_flow_reason=order_flow_result.reason if order_flow_result else None,
        )
