"""
Core strategy engine orchestrating structure, liquidity, zones, and validators.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Mapping, Optional, Sequence

from app.config.pairs import BTC_SYMBOL, validate_pair_symbol
from app.config.timeframes import ENTRY_TIMEFRAME, HTF_PRIMARY, HTF_SECONDARY
from app.data.candle_repository import CandleRepository
from app.data.market_data_errors import MarketDataError
from app.data.provider_base import MarketDataProvider
from app.indicators.calculator import IndicatorCalculator
from app.indicators.ema import IndicatorCalculationError
from app.liquidity.calculator import LiquidityCalculator
from app.liquidity.results import LiquiditySweepResult, SweepDirection
from app.market_structure.calculator import MarketStructureCalculator
from app.market_structure.displacement import StructureShiftCalculationError
from app.market_structure.results import HigherTimeframeBias
from app.market_structure.shift_calculator import StructureShiftCalculator
from app.models.candle import Candle
from app.models.market_context import MarketContext
from app.models.validation_result import ValidationResult
from app.risk.calculator import RiskManagementCalculator
from app.scoring.calculator import ConfidenceCalculator
from app.validators.btc_alignment import BTCAlignmentValidator
from app.validators.candle_quality import CandleQualityValidator
from app.validators.entry_zone import EntryZoneValidator
from app.validators.fake_breakout_filter import FakeBreakoutFilter
from app.validators.htf_bias import HigherTimeframeBiasValidator
from app.validators.liquidity_sweep import LiquiditySweepValidator
from app.validators.market_regime import MarketRegimeValidator
from app.validators.risk_management import RiskManagementValidator
from app.validators.session_filter import SessionFilter
from app.validators.structure_shift import StructureShiftValidator
from app.validators.volume_confirmation import VolumeConfirmationValidator
from app.zones.calculator import ZoneCalculator
from app.zones.order_block import ZoneCalculationError
from app.zones.setup_confirmation import ZoneSetupConfirmationCalculator

from app.scanner.pipeline_exceptions import (
    PipelineDataUnavailableError,
    PipelineInputError,
    PipelineStageError,
)
from app.scanner.pipeline_results import PipelineStageResult, PipelineStatus, StrategyPipelineResult

_REQUIRED_TIMEFRAMES = (ENTRY_TIMEFRAME, HTF_SECONDARY, HTF_PRIMARY)

# (stage_order, layer_name)
_STAGE_DEFINITIONS: tuple[tuple[int, str], ...] = (
    (1, "MARKET_REGIME"),
    (2, "HTF_BIAS"),
    (3, "LIQUIDITY_SWEEP"),
    (4, "STRUCTURE_SHIFT"),
    (5, "VOLUME_CONFIRMATION"),
    (6, "ENTRY_ZONE"),
    (7, "PREMIUM_DISCOUNT"),
    (8, "RETEST_CONFIRMATION"),
    (9, "SESSION_FILTER"),
    (10, "BTC_ALIGNMENT"),
    (11, "FAKE_BREAKOUT_FILTER"),
    (12, "CANDLE_QUALITY"),
    (13, "RISK_MANAGEMENT"),
    (14, "CONFIDENCE_SCORING"),
)

# Hard-mandatory gates: failure rejects the pipeline immediately.
_HARD_MANDATORY_STAGES = {
    "MARKET_REGIME",
    "HTF_BIAS",
    "LIQUIDITY_SWEEP",
    "STRUCTURE_SHIFT",
    "VOLUME_CONFIRMATION",
    "ENTRY_ZONE",
}
# Soft-scoring layers: failure never rejects; only zeroes that layer's score.
_SOFT_SCORING_STAGES = {
    "PREMIUM_DISCOUNT",
    "RETEST_CONFIRMATION",
    "SESSION_FILTER",
    "BTC_ALIGNMENT",
    "FAKE_BREAKOUT_FILTER",
}
# CANDLE_QUALITY, RISK_MANAGEMENT, CONFIDENCE_SCORING remain hard gates:
# structural/account-safety requirements that were never part of the
# soft-scoring redesign.
_MANDATORY_STAGES = _HARD_MANDATORY_STAGES | {
    "CANDLE_QUALITY",
    "RISK_MANAGEMENT",
    "CONFIDENCE_SCORING",
}


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


def _rename_layer(result: ValidationResult, layer_name: str) -> ValidationResult:
    """Return a copy of `result` with `layer_name` normalized to the scoring-engine's expected name."""
    return result.model_copy(update={"layer_name": layer_name})


class InstitutionalSMCStrategyEngine:
    """
    Orchestrates the complete institutional Smart Money Concepts strategy
    pipeline for a single symbol: market-data preparation, direction
    resolution from higher-timeframe bias, and the 14 ordered validation
    stages (6 hard-mandatory gates, 5 soft-scoring layers that never stop
    the pipeline, and 3 remaining structural/account-safety gates),
    ending in confidence scoring.

    Every dependency is injected; this class does not construct its own
    calculators or validators. It never mutates input candles, contexts,
    positions, or calculation results, and never persists, publishes, or
    executes a trade.
    """

    def __init__(
        self,
        market_data_provider: MarketDataProvider,
        candle_repository: CandleRepository,
        indicator_calculator: IndicatorCalculator,
        market_structure_calculator: MarketStructureCalculator,
        liquidity_calculator: LiquidityCalculator,
        structure_shift_calculator: StructureShiftCalculator,
        zone_calculator: ZoneCalculator,
        zone_setup_confirmation_calculator: ZoneSetupConfirmationCalculator,
        market_regime_validator: MarketRegimeValidator,
        htf_bias_validator: HigherTimeframeBiasValidator,
        liquidity_sweep_validator: LiquiditySweepValidator,
        structure_shift_validator: StructureShiftValidator,
        volume_confirmation_validator: VolumeConfirmationValidator,
        entry_zone_validator: EntryZoneValidator,
        session_filter: SessionFilter,
        btc_alignment_validator: BTCAlignmentValidator,
        fake_breakout_filter: FakeBreakoutFilter,
        candle_quality_validator: CandleQualityValidator,
        risk_management_calculator: RiskManagementCalculator,
        risk_management_validator: RiskManagementValidator,
        confidence_calculator: ConfidenceCalculator,
    ) -> None:
        self._market_data_provider = market_data_provider
        self._candle_repository = candle_repository
        self._indicator_calculator = indicator_calculator
        self._market_structure_calculator = market_structure_calculator
        self._liquidity_calculator = liquidity_calculator
        self._structure_shift_calculator = structure_shift_calculator
        self._zone_calculator = zone_calculator
        self._zone_setup_confirmation_calculator = zone_setup_confirmation_calculator
        self._market_regime_validator = market_regime_validator
        self._htf_bias_validator = htf_bias_validator
        self._liquidity_sweep_validator = liquidity_sweep_validator
        self._structure_shift_validator = structure_shift_validator
        self._volume_confirmation_validator = volume_confirmation_validator
        self._entry_zone_validator = entry_zone_validator
        self._session_filter = session_filter
        self._btc_alignment_validator = btc_alignment_validator
        self._fake_breakout_filter = fake_breakout_filter
        self._candle_quality_validator = candle_quality_validator
        self._risk_management_calculator = risk_management_calculator
        self._risk_management_validator = risk_management_validator
        self._confidence_calculator = confidence_calculator

        # Every symbol scanned within the same scan cycle shares the same
        # `detection_time_utc` (set once by MultiPairScanScheduler.run_cycle),
        # so it doubles as a per-cycle cache key: this avoids refetching
        # identical BTC candles from the exchange once per non-BTC symbol
        # every cycle. The lock makes concurrent analyze_symbol() calls
        # (fanned out via asyncio.gather) share a single in-flight BTC
        # fetch instead of racing duplicate requests. Bounded to the most
        # recent cycle only -- never accumulates across cycles.
        self._btc_candle_cache_key: Optional[str] = None
        self._btc_candle_cache: Optional[dict[str, list[Candle]]] = None
        self._btc_candle_cache_lock = asyncio.Lock()

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
        Run the complete strategy pipeline for `symbol` and return the
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

    async def _fetch_btc_candles_for_cycle(
        self, detection_time_utc: datetime
    ) -> dict[str, list[Candle]]:
        """
        Fetch BTC candles once per scan cycle and reuse the result for
        every non-BTC symbol analyzed within that same cycle, instead of
        refetching identical BTC market data from the exchange once per
        symbol. `detection_time_utc` is the same value for every symbol
        scanned within one MultiPairScanScheduler cycle, so it serves as
        the cache key; a new cycle (a new `detection_time_utc`) always
        fetches fresh data and replaces the cache -- never reuses a
        previous cycle's BTC candles.
        """
        cache_key = detection_time_utc.isoformat()
        async with self._btc_candle_cache_lock:
            if self._btc_candle_cache_key == cache_key and self._btc_candle_cache is not None:
                return self._btc_candle_cache

            btc_candles = await self._market_data_provider.fetch_symbol_market_data(BTC_SYMBOL)
            self._btc_candle_cache_key = cache_key
            self._btc_candle_cache = btc_candles
            return btc_candles

    async def _prepare_market_context(self, symbol: str, detection_time_utc: datetime) -> MarketContext:
        """Fetch candles, persist them, and calculate indicators/structures/HTF bias."""
        symbol_candles = await self._market_data_provider.fetch_symbol_market_data(symbol)

        for timeframe in _REQUIRED_TIMEFRAMES:
            candles = symbol_candles.get(timeframe)
            if not candles:
                raise PipelineDataUnavailableError(
                    layer_name="MARKET_DATA_PREPARATION",
                    symbol=symbol,
                    reason=f"No candles available for required timeframe '{timeframe}'.",
                )
            self._candle_repository.save_candles(symbol, timeframe, candles)

        if symbol == BTC_SYMBOL:
            btc_candles = symbol_candles
        else:
            btc_candles = await self._fetch_btc_candles_for_cycle(detection_time_utc)
            for timeframe in _REQUIRED_TIMEFRAMES:
                btc_tf_candles = btc_candles.get(timeframe)
                if not btc_tf_candles:
                    raise PipelineDataUnavailableError(
                        layer_name="MARKET_DATA_PREPARATION",
                        symbol=BTC_SYMBOL,
                        reason=f"No BTC candles available for required timeframe '{timeframe}'.",
                    )
                self._candle_repository.save_candles(BTC_SYMBOL, timeframe, btc_tf_candles)

        indicators_by_timeframe = self._indicator_calculator.calculate_multiple_timeframes(
            symbol_candles
        )
        btc_indicators_by_timeframe = self._indicator_calculator.calculate_multiple_timeframes(
            btc_candles
        )

        structures_by_timeframe = self._market_structure_calculator.calculate_multiple_timeframes(
            symbol_candles
        )
        btc_structures_by_timeframe = self._market_structure_calculator.calculate_multiple_timeframes(
            btc_candles
        )

        htf_bias_result = self._market_structure_calculator.calculate_higher_timeframe_bias(
            symbol_candles, indicators_by_timeframe
        )
        btc_htf_bias_result = self._market_structure_calculator.calculate_higher_timeframe_bias(
            btc_candles, btc_indicators_by_timeframe
        )

        return MarketContext(
            symbol=symbol,
            detection_time_utc=detection_time_utc,
            candles_by_timeframe=symbol_candles,
            btc_candles_by_timeframe=btc_candles,
            entry_timeframe=ENTRY_TIMEFRAME,
            indicators_by_timeframe=indicators_by_timeframe,
            structures_by_timeframe=structures_by_timeframe,
            htf_bias_result=htf_bias_result,
            pipeline_metadata={
                "btc_indicators_by_timeframe": btc_indicators_by_timeframe,
                "btc_structures_by_timeframe": btc_structures_by_timeframe,
                "btc_htf_bias_result": btc_htf_bias_result,
            },
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
        btc_structures_by_timeframe = context.pipeline_metadata["btc_structures_by_timeframe"]
        btc_htf_bias_result = context.pipeline_metadata["btc_htf_bias_result"]
        btc_structure_4h = btc_structures_by_timeframe[HTF_PRIMARY]

        # Stage 1: MARKET_REGIME
        start = _now_ms()
        market_regime_result = self._market_regime_validator.validate(entry_snapshot, entry_candles)
        stages.append(self._stage(1, "MARKET_REGIME", market_regime_result, start))
        if not market_regime_result.passed:
            return self._build_rejected_result(context, None, stages, "MARKET_REGIME", market_regime_result)

        # Stage 2: HTF_BIAS (direction resolution)
        start = _now_ms()
        htf_bias_result = context.htf_bias_result
        if htf_bias_result.final_bias == HigherTimeframeBias.BULLISH:
            expected_direction: Optional[str] = "BUY"
        elif htf_bias_result.final_bias == HigherTimeframeBias.BEARISH:
            expected_direction = "SELL"
        else:
            expected_direction = None

        htf_validation = self._htf_bias_validator.validate(htf_bias_result, expected_direction or "BUY")
        stages.append(self._stage(2, "HTF_BIAS", htf_validation, start))
        if not htf_validation.passed or expected_direction is None:
            if htf_validation.passed and expected_direction is None:
                htf_validation = ValidationResult.failure(
                    layer_name="HTF_BIAS",
                    reason="Higher-timeframe bias is MIXED or UNKNOWN.",
                    rejection_code="HTF_BIAS_UNKNOWN",
                )
                stages[-1] = self._stage(2, "HTF_BIAS", htf_validation, 0.0)
            return self._build_rejected_result(context, expected_direction, stages, "HTF_BIAS", htf_validation)

        context = context.with_updates(expected_direction=expected_direction)

        # Stage 3: LIQUIDITY_SWEEP
        start = _now_ms()
        try:
            liquidity_detection_result = self._liquidity_calculator.detect_levels(
                entry_candles, entry_structure.swings, context.detection_time_utc
            )
            confirmed_sweeps = self._liquidity_calculator.detect_sweeps(
                entry_candles, liquidity_detection_result
            )
        except Exception as exc:  # noqa: BLE001
            raise PipelineStageError(
                layer_name="LIQUIDITY_SWEEP", symbol=symbol, reason="Liquidity detection failed.",
                original_exception=exc,
            ) from exc

        selected_sweep = self._select_latest_valid_sweep(
            confirmed_sweeps, expected_direction, context.detection_time_utc
        )

        liquidity_validation = self._liquidity_sweep_validator.validate(selected_sweep, expected_direction)
        stages.append(self._stage(3, "LIQUIDITY_SWEEP", liquidity_validation, start))
        context = context.with_updates(
            liquidity_detection_result=liquidity_detection_result,
            liquidity_sweeps=confirmed_sweeps,
            selected_liquidity_sweep=selected_sweep,
        )
        if not liquidity_validation.passed:
            return self._build_rejected_result(context, expected_direction, stages, "LIQUIDITY_SWEEP", liquidity_validation)

        # Stage 4: STRUCTURE_SHIFT
        start = _now_ms()
        volume_ema_values = [entry_snapshot.volume_ema20] * len(entry_candles)
        try:
            structure_shift_result = self._structure_shift_calculator.calculate(
                entry_candles, entry_structure.swings, entry_structure, [selected_sweep], volume_ema_values
            )
        except StructureShiftCalculationError as exc:
            raise PipelineStageError(
                layer_name="STRUCTURE_SHIFT", symbol=symbol, reason="Structure-shift calculation failed.",
                original_exception=exc,
            ) from exc

        structure_shift_validation = _rename_layer(
            self._structure_shift_validator.validate(structure_shift_result, expected_direction),
            "STRUCTURE_SHIFT",
        )
        stages.append(self._stage(4, "STRUCTURE_SHIFT", structure_shift_validation, start))
        selected_break = structure_shift_result.latest_confirmed_break
        context = context.with_updates(
            structure_shift_result=structure_shift_result, selected_structure_break=selected_break
        )
        if not structure_shift_validation.passed:
            return self._build_rejected_result(
                context, expected_direction, stages, "STRUCTURE_SHIFT", structure_shift_validation
            )

        # Stage 5 (phase A): VOLUME_CONFIRMATION preliminary check (displacement only)
        start = _now_ms()
        displacement = selected_break.displacement if selected_break else None
        preliminary_volume_result = self._volume_confirmation_validator.validate(displacement, None)
        stages.append(self._stage(5, "VOLUME_CONFIRMATION", preliminary_volume_result, start))
        if displacement is None:
            return self._build_rejected_result(
                context, expected_direction, stages, "VOLUME_CONFIRMATION", preliminary_volume_result
            )

        # Stage 6: ENTRY_ZONE
        start = _now_ms()
        current_price = entry_candles[-1].close
        try:
            zone_detection_result = self._zone_calculator.calculate(
                entry_candles,
                structure_shift_result.all_breaks,
                expected_direction,
                current_price,
                context.detection_time_utc,
            )
        except ZoneCalculationError as exc:
            raise PipelineStageError(
                layer_name="ENTRY_ZONE", symbol=symbol, reason="Zone calculation failed.",
                original_exception=exc,
            ) from exc

        entry_zone_validation = _rename_layer(
            self._entry_zone_validator.validate(zone_detection_result, expected_direction), "ENTRY_ZONE"
        )
        stages.append(self._stage(6, "ENTRY_ZONE", entry_zone_validation, start))
        selected_zone = zone_detection_result.selected_zone
        context = context.with_updates(
            zone_detection_result=zone_detection_result, selected_entry_zone=selected_zone
        )
        if not entry_zone_validation.passed:
            return self._build_rejected_result(
                context, expected_direction, stages, "ENTRY_ZONE", entry_zone_validation
            )

        # Stages 7 & 8: PREMIUM_DISCOUNT then RETEST_CONFIRMATION (soft-scoring)
        # (ZoneSetupConfirmationCalculator evaluates Premium/Discount first
        # and only evaluates retest if that passes, matching the required order.)
        # Neither failure stops the pipeline: downstream stages continue
        # using the best-available zone/retest data rather than aborting.
        start = _now_ms()
        setup_result = self._zone_setup_confirmation_calculator.calculate(
            entry_candles,
            entry_structure.swings,
            selected_zone,
            expected_direction,
            volume_ema_values,
            current_price,
            context.detection_time_utc,
        )
        premium_discount_validation = _rename_layer(
            setup_result.premium_discount_validation, "PREMIUM_DISCOUNT"
        )
        stages.append(
            self._stage(7, "PREMIUM_DISCOUNT", premium_discount_validation, start, mandatory=False)
        )
        context = context.with_updates(dealing_range_result=setup_result.dealing_range)

        start = _now_ms()
        retest_result = setup_result.retest
        retest_validation = _rename_layer(setup_result.retest_validation, "RETEST_CONFIRMATION")
        stages.append(self._stage(8, "RETEST_CONFIRMATION", retest_validation, start, mandatory=False))
        context = context.with_updates(retest_result=retest_result)

        # Stage 5 (phase B): finalize VOLUME_CONFIRMATION with displacement +
        # whatever retest data is available (unconfirmed if RETEST_CONFIRMATION
        # failed above -- never fabricated).
        final_volume_result = self._volume_confirmation_validator.validate(displacement, retest_result)
        stages[4] = self._stage(5, "VOLUME_CONFIRMATION", final_volume_result, 0.0)
        if not final_volume_result.passed:
            return self._build_rejected_result(
                context, expected_direction, stages, "VOLUME_CONFIRMATION", final_volume_result
            )

        confirmation_candle = self._resolve_confirmation_candle(entry_candles, retest_result)

        # Stage 9: SESSION_FILTER (soft-scoring)
        start = _now_ms()
        btc_trend_strong = btc_structure_4h.trend_direction.value in ("BULLISH", "BEARISH")
        atr_high = entry_snapshot.atr is not None and entry_snapshot.atr > 0
        volume_high = (
            confirmation_candle.volume is not None
            and entry_snapshot.volume_ema20 is not None
            and confirmation_candle.volume > entry_snapshot.volume_ema20
        )
        session_validation = self._session_filter.validate(
            context.detection_time_utc, btc_trend_strong, atr_high, volume_high
        )
        stages.append(self._stage(9, "SESSION_FILTER", session_validation, start, mandatory=False))

        # Stage 10: BTC_ALIGNMENT (soft-scoring)
        start = _now_ms()
        btc_alignment_validation = self._btc_alignment_validator.validate(
            symbol, expected_direction, btc_structure_4h, btc_htf_bias_result
        )
        stages.append(self._stage(10, "BTC_ALIGNMENT", btc_alignment_validation, start, mandatory=False))

        # Stage 11: FAKE_BREAKOUT_FILTER (soft-scoring)
        start = _now_ms()
        fake_breakout_validation = self._fake_breakout_filter.validate(
            entry_candles, selected_sweep, selected_break
        )
        stages.append(
            self._stage(11, "FAKE_BREAKOUT_FILTER", fake_breakout_validation, start, mandatory=False)
        )

        # Stage 12: CANDLE_QUALITY
        start = _now_ms()
        candle_quality_validation = self._candle_quality_validator.validate(
            confirmation_candle, expected_direction
        )
        stages.append(self._stage(12, "CANDLE_QUALITY", candle_quality_validation, start))
        if not candle_quality_validation.passed:
            return self._build_rejected_result(
                context, expected_direction, stages, "CANDLE_QUALITY", candle_quality_validation
            )

        # Stage 13: RISK_MANAGEMENT
        start = _now_ms()
        entry_price = confirmation_candle.close
        major_swings = entry_structure.swings
        fair_value_gaps = zone_detection_result.fair_value_gaps
        try:
            risk_plan = self._risk_management_calculator.calculate(
                direction=expected_direction,
                entry_price=entry_price,
                atr=entry_snapshot.atr,
                selected_zone=selected_zone,
                liquidity_sweep=selected_sweep,
                institutional_liquidity_levels=liquidity_detection_result.active_levels,
                major_swings=major_swings,
                fair_value_gaps=fair_value_gaps,
                account_balance=account_balance,
                active_trade_count=active_trade_count,
                candidate_symbol=symbol,
                candidate_candles=entry_candles,
                active_position_candles=active_position_candles,
                active_positions=active_positions,
            )
        except Exception as exc:  # noqa: BLE001
            raise PipelineStageError(
                layer_name="RISK_MANAGEMENT", symbol=symbol, reason="Risk-management calculation failed.",
                original_exception=exc,
            ) from exc

        risk_validation = self._risk_management_validator.validate(risk_plan)
        stages.append(self._stage(13, "RISK_MANAGEMENT", risk_validation, start))
        context = context.with_updates(risk_plan=risk_plan)
        if not risk_validation.passed:
            return self._build_rejected_result(context, expected_direction, stages, "RISK_MANAGEMENT", risk_validation)

        # Stage 14: CONFIDENCE_SCORING
        start = _now_ms()
        try:
            confidence_result = self._confidence_calculator.calculate(
                market_regime=market_regime_result,
                htf_bias=htf_validation,
                liquidity_sweep=liquidity_validation,
                structure_shift=structure_shift_validation,
                volume_confirmation=final_volume_result,
                entry_zone=entry_zone_validation,
                premium_discount=premium_discount_validation,
                retest_confirmation=retest_validation,
                session_filter=session_validation,
                btc_alignment=btc_alignment_validation,
                fake_breakout_filter=fake_breakout_validation,
            )
        except Exception as exc:  # noqa: BLE001
            raise PipelineStageError(
                layer_name="CONFIDENCE_SCORING", symbol=symbol, reason="Confidence scoring failed.",
                original_exception=exc,
            ) from exc

        # The binary CONFIRMED/REJECTED decision depends only on every
        # hard-mandatory stage having passed (mandatory_layers_passed),
        # never on the numeric score/classification -- a setup that
        # only cleared MEDIUM-level score, or failed a soft-scoring
        # layer, still reaches CONFIRMED as long as every required
        # (hard-mandatory) condition passed. The score itself is
        # retained only as internal diagnostic/audit detail; no field
        # anywhere presents it as the signal's confidence or rating.
        confidence_stage_result = ValidationResult(
            passed=confidence_result.mandatory_layers_passed,
            layer_name="CONFIDENCE_SCORING",
            reason=confidence_result.reason,
        )
        stages.append(self._stage(14, "CONFIDENCE_SCORING", confidence_stage_result, start))
        context = context.with_updates(confidence_result=confidence_result)

        if not confidence_result.mandatory_layers_passed:
            return self._build_rejected_result(
                context, expected_direction, stages, "CONFIDENCE_SCORING", confidence_stage_result
            )

        return StrategyPipelineResult(
            symbol=symbol,
            expected_direction=expected_direction,
            detection_time_utc=context.detection_time_utc,
            status=PipelineStatus.VALID,
            passed=True,
            failed_layer=None,
            rejection_reason=None,
            market_context=context,
            stages=stages,
            market_regime_result=market_regime_result,
            htf_bias_result=htf_bias_result,
            liquidity_detection_result=liquidity_detection_result,
            liquidity_sweep=selected_sweep,
            structure_shift_result=structure_shift_result,
            selected_structure_break=selected_break,
            volume_validation=final_volume_result,
            zone_detection_result=zone_detection_result,
            selected_entry_zone=selected_zone,
            dealing_range_result=setup_result.dealing_range,
            retest_result=retest_result,
            session_result=session_validation,
            btc_alignment_result=btc_alignment_validation,
            fake_breakout_result=fake_breakout_validation,
            candle_quality_result=candle_quality_validation,
            risk_plan=risk_plan,
            confidence_result=confidence_result,
            metadata={"entry_price_rule": "confirmed_retest_candle_close"},
        )

    @staticmethod
    def _select_latest_valid_sweep(
        confirmed_sweeps: Sequence[LiquiditySweepResult],
        expected_direction: str,
        detection_time_utc: datetime,
    ) -> Optional[LiquiditySweepResult]:
        """
        Select the latest confirmed sweep that occurred before the
        detection time and matches the expected direction. Liquidity
        level strength (STRONG/INSTITUTIONAL) and reclaim are already
        guaranteed by the sweep detector; direction matching is applied
        here since the detector itself is direction-agnostic.
        """
        required_direction = (
            SweepDirection.BULLISH if expected_direction == "BUY" else SweepDirection.BEARISH
        )
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
    def _resolve_confirmation_candle(entry_candles: Sequence[Candle], retest_result) -> Candle:
        if retest_result is not None:
            for candle in entry_candles:
                if candle.timestamp == retest_result.retest_candle_timestamp:
                    return candle
        return entry_candles[-1]

    @staticmethod
    def _stage(
        stage_order: int,
        layer_name: str,
        validation_result: ValidationResult,
        start_ms: float,
        mandatory: bool = True,
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
        )

    def _build_rejected_result(
        self,
        context: MarketContext,
        expected_direction: Optional[str],
        stages: list[PipelineStageResult],
        failed_layer: str,
        failed_validation: ValidationResult,
    ) -> StrategyPipelineResult:
        """
        Build a REJECTED StrategyPipelineResult, marking every stage after
        `failed_layer` as executed=False and never fabricating downstream
        results.
        """
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
                    reason="Not executed: pipeline stopped at an earlier mandatory failure.",
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
        )
