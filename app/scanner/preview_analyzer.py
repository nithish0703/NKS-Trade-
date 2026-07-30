"""
Dashboard-only, non-trading scan preview analyzer.

The production strategy pipeline (InstitutionalSMCStrategyEngine) is
strictly fail-fast: it stops at the first mandatory layer that fails and
never computes downstream layers. That is correct and unchanged
behaviour for actual trading decisions.

This module independently re-evaluates the same 11 confidence-scoring
layers using the exact same pure validators/calculators the production
engine uses, but WITHOUT stopping on failure, purely so the dashboard
can display a "how far would this setup have gotten" preview even when
the real pipeline rejected early (e.g. at MARKET_REGIME).

Hard guarantees:
    - Never mutates or re-runs market-data fetching. Reuses the
      MarketContext the real pipeline already built (no extra network
      or database calls).
    - Never raises to abort a scan cycle; any internal calculation
      failure degrades a layer to "unavailable" rather than propagating.
    - Never produces a Signal, never persists anything, never sends a
      Telegram notification, and never touches RiskManagementCalculator
      or ConfidenceScoringEngine's final classification.
    - Output is attached only to dashboard-facing data (PairScanResult),
      never folded back into StrategyPipelineResult or used to change
      PairScanStatus.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.config.timeframes import ENTRY_TIMEFRAME, HTF_PRIMARY
from app.liquidity.calculator import LiquidityCalculator
from app.liquidity.results import LiquiditySweepResult, SweepDirection
from app.market_structure.displacement import StructureShiftCalculationError
from app.market_structure.results import HigherTimeframeBias
from app.market_structure.shift_calculator import StructureShiftCalculator
from app.models.market_context import MarketContext
from app.validators.btc_alignment import BTCAlignmentValidator
from app.validators.entry_zone import EntryZoneValidator
from app.validators.fake_breakout_filter import FakeBreakoutFilter
from app.validators.htf_bias import HigherTimeframeBiasValidator
from app.validators.liquidity_sweep import LiquiditySweepValidator
from app.validators.market_regime import MarketRegimeValidator
from app.validators.premium_discount import PremiumDiscountValidator
from app.validators.retest_confirmation import RetestConfirmationValidator
from app.validators.session_filter import SessionFilter
from app.validators.structure_shift import StructureShiftValidator
from app.validators.volume_confirmation import VolumeConfirmationValidator
from app.zones.calculator import ZoneCalculator
from app.zones.dealing_range import DealingRangeCalculator
from app.zones.order_block import ZoneCalculationError
from app.zones.retest_confirmation import RetestConfirmationDetector

# The 11 confidence-scoring layers, in the same order the production
# engine evaluates them. Identical to _STAGE_DEFINITIONS's scoring
# subset in strategy_engine.py (CANDLE_QUALITY, RISK_MANAGEMENT,
# CONFIDENCE_SCORING are intentionally excluded: they either never
# carry scoring weight or require the account/risk context this
# dashboard-only preview never touches).
#
# The first 6 (MARKET_REGIME through ENTRY_ZONE) are hard-mandatory
# pipeline gates in production: a real signal cannot exist unless every
# one of them passed. The remaining 5 (PREMIUM_DISCOUNT through
# FAKE_BREAKOUT_FILTER) are soft-scoring layers: production never stops
# the pipeline for their failure, it only zeroes their score
# contribution. This preview mirrors that distinction implicitly by
# always continuing past a failure at any of these layers, exactly
# like production does for the soft ones -- for the hard ones, a
# failure here means production would have rejected already, but this
# preview still shows how far the setup independently progressed.
PREVIEW_LAYER_ORDER: tuple[str, ...] = (
    "MARKET_REGIME",
    "HTF_BIAS",
    "LIQUIDITY_SWEEP",
    "STRUCTURE_SHIFT",
    "VOLUME_CONFIRMATION",
    "ENTRY_ZONE",
    "PREMIUM_DISCOUNT",
    "RETEST_CONFIRMATION",
    "SESSION_FILTER",
    "BTC_ALIGNMENT",
    "FAKE_BREAKOUT_FILTER",
)


class PreviewLayerStatus(str, Enum):
    """Outcome of independently (re-)evaluating one preview layer."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class PreviewLayerResult(BaseModel):
    """Single dashboard-preview layer outcome. Never affects strategy state."""

    model_config = ConfigDict(frozen=True)

    layer_name: str
    status: PreviewLayerStatus
    reason: Optional[str] = None


class PreviewAnalysisResult(BaseModel):
    """
    Dashboard-only, non-trading preview of how far an independently
    re-evaluated setup would have progressed, computed from the same
    MarketContext the real (fail-fast) pipeline already built.

    This is never stored as a Signal, never sent to Telegram, never
    used for risk management or trade decisions, and never substituted
    for ConfidenceScoringEngine's final output.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    preview_direction: Optional[str] = None
    preview_progress_raw_score: float
    preview_progress_max_score: float
    preview_progress_percentage: int
    preview_completed_layers: list[str]
    preview_failed_layers: list[str]
    preview_data_availability: dict[str, PreviewLayerStatus]


class PreviewAnalyzer:
    """
    Independently re-evaluates the 11 confidence-scoring layers from an
    already-built MarketContext, continuing past failures instead of
    stopping at the first one.

    Every dependency here is the exact same pure validator/calculator
    instance the production engine uses (see engine_factory.py) -- no
    thresholds, weights, or pass/fail logic are reimplemented or
    duplicated; only the fail-fast *orchestration* differs.
    """

    def __init__(
        self,
        *,
        liquidity_calculator: LiquidityCalculator,
        structure_shift_calculator: StructureShiftCalculator,
        zone_calculator: ZoneCalculator,
        dealing_range_calculator: DealingRangeCalculator,
        retest_confirmation_detector: RetestConfirmationDetector,
        market_regime_validator: MarketRegimeValidator,
        htf_bias_validator: HigherTimeframeBiasValidator,
        liquidity_sweep_validator: LiquiditySweepValidator,
        structure_shift_validator: StructureShiftValidator,
        volume_confirmation_validator: VolumeConfirmationValidator,
        entry_zone_validator: EntryZoneValidator,
        premium_discount_validator: PremiumDiscountValidator,
        retest_confirmation_validator: RetestConfirmationValidator,
        session_filter: SessionFilter,
        btc_alignment_validator: BTCAlignmentValidator,
        fake_breakout_filter: FakeBreakoutFilter,
        layer_weights: dict[str, float],
        max_score: float,
    ) -> None:
        self._liquidity_calculator = liquidity_calculator
        self._structure_shift_calculator = structure_shift_calculator
        self._zone_calculator = zone_calculator
        self._dealing_range_calculator = dealing_range_calculator
        self._retest_confirmation_detector = retest_confirmation_detector
        self._market_regime_validator = market_regime_validator
        self._htf_bias_validator = htf_bias_validator
        self._liquidity_sweep_validator = liquidity_sweep_validator
        self._structure_shift_validator = structure_shift_validator
        self._volume_confirmation_validator = volume_confirmation_validator
        self._entry_zone_validator = entry_zone_validator
        self._premium_discount_validator = premium_discount_validator
        self._retest_confirmation_validator = retest_confirmation_validator
        self._session_filter = session_filter
        self._btc_alignment_validator = btc_alignment_validator
        self._fake_breakout_filter = fake_breakout_filter
        self._layer_weights = layer_weights
        self._max_score = max_score

    def analyze(self, context: MarketContext) -> PreviewAnalysisResult:
        """
        Independently re-evaluate as many of the 11 scoring layers as
        available input data allows, never stopping at the first
        failure. Reuses `context` as-is; performs no network or
        database I/O and mutates nothing.
        """
        layers: dict[str, PreviewLayerResult] = {}

        preview_direction = self._resolve_preview_direction(context)

        entry_snapshot = (
            context.indicators_by_timeframe.get(ENTRY_TIMEFRAME)
            if context.indicators_by_timeframe
            else None
        )
        entry_structure = (
            context.structures_by_timeframe.get(ENTRY_TIMEFRAME)
            if context.structures_by_timeframe
            else None
        )
        entry_candles = (
            context.candles_by_timeframe.get(ENTRY_TIMEFRAME)
            if context.candles_by_timeframe
            else None
        )

        # MARKET_REGIME
        if entry_snapshot is None:
            layers["MARKET_REGIME"] = self._unavailable(
                "MARKET_REGIME", "Entry-timeframe indicators are unavailable."
            )
        else:
            result = self._market_regime_validator.validate(entry_snapshot, entry_candles)
            layers["MARKET_REGIME"] = self._from_validation(result)

        # HTF_BIAS
        htf_bias_result = context.htf_bias_result
        if htf_bias_result is None or preview_direction is None:
            reason = (
                "Higher-timeframe bias is unavailable."
                if htf_bias_result is None
                else "Higher-timeframe bias is MIXED or UNKNOWN."
            )
            layers["HTF_BIAS"] = (
                self._unavailable("HTF_BIAS", reason)
                if htf_bias_result is None
                else PreviewLayerResult(
                    layer_name="HTF_BIAS", status=PreviewLayerStatus.FAILED, reason=reason
                )
            )
        else:
            result = self._htf_bias_validator.validate(htf_bias_result, preview_direction)
            layers["HTF_BIAS"] = self._from_validation(result)

        # Everything below genuinely requires a resolved direction and
        # entry-timeframe candles/structure; without them these layers
        # are marked unavailable rather than fabricated.
        can_continue = (
            preview_direction is not None
            and entry_candles is not None
            and entry_structure is not None
        )

        selected_sweep: Optional[LiquiditySweepResult] = None
        if not can_continue:
            layers["LIQUIDITY_SWEEP"] = self._unavailable(
                "LIQUIDITY_SWEEP", "Required entry-timeframe data or direction is unavailable."
            )
        else:
            try:
                detection = self._liquidity_calculator.detect_levels(
                    entry_candles, entry_structure.swings, context.detection_time_utc
                )
                confirmed_sweeps = self._liquidity_calculator.detect_sweeps(entry_candles, detection)
                selected_sweep = self._select_latest_valid_sweep(
                    confirmed_sweeps, preview_direction, context.detection_time_utc
                )
                result = self._liquidity_sweep_validator.validate(selected_sweep, preview_direction)
                layers["LIQUIDITY_SWEEP"] = self._from_validation(result)
            except Exception as exc:  # noqa: BLE001 - a preview failure must never break the scan
                layers["LIQUIDITY_SWEEP"] = self._unavailable("LIQUIDITY_SWEEP", str(exc))

        structure_shift_result = None
        selected_break = None
        if not can_continue:
            layers["STRUCTURE_SHIFT"] = self._unavailable(
                "STRUCTURE_SHIFT", "Required entry-timeframe data or direction is unavailable."
            )
        else:
            try:
                volume_ema_values = [entry_snapshot.volume_ema20] * len(entry_candles)
                structure_shift_result = self._structure_shift_calculator.calculate(
                    entry_candles,
                    entry_structure.swings,
                    entry_structure,
                    [selected_sweep],
                    volume_ema_values,
                )
                selected_break = structure_shift_result.latest_confirmed_break
                result = self._structure_shift_validator.validate(
                    structure_shift_result, preview_direction
                )
                layers["STRUCTURE_SHIFT"] = self._from_validation(result, "STRUCTURE_SHIFT")
            except StructureShiftCalculationError as exc:
                layers["STRUCTURE_SHIFT"] = self._unavailable("STRUCTURE_SHIFT", str(exc))
            except Exception as exc:  # noqa: BLE001
                layers["STRUCTURE_SHIFT"] = self._unavailable("STRUCTURE_SHIFT", str(exc))

        # VOLUME_CONFIRMATION (preliminary read; finalized after retest below)
        displacement = selected_break.displacement if selected_break is not None else None
        if displacement is None:
            layers["VOLUME_CONFIRMATION"] = self._unavailable(
                "VOLUME_CONFIRMATION", "No confirmed structural break to check displacement against."
            )
        else:
            result = self._volume_confirmation_validator.validate(displacement, None)
            layers["VOLUME_CONFIRMATION"] = self._from_validation(result)

        zone_detection_result = None
        selected_zone = None
        if not can_continue or structure_shift_result is None:
            layers["ENTRY_ZONE"] = self._unavailable(
                "ENTRY_ZONE", "No structure-shift result available to detect entry zones from."
            )
        else:
            try:
                current_price = entry_candles[-1].close
                zone_detection_result = self._zone_calculator.calculate(
                    entry_candles,
                    structure_shift_result.all_breaks,
                    preview_direction,
                    current_price,
                    context.detection_time_utc,
                )
                selected_zone = zone_detection_result.selected_zone
                result = self._entry_zone_validator.validate(zone_detection_result, preview_direction)
                layers["ENTRY_ZONE"] = self._from_validation(result, "ENTRY_ZONE")
            except ZoneCalculationError as exc:
                layers["ENTRY_ZONE"] = self._unavailable("ENTRY_ZONE", str(exc))
            except Exception as exc:  # noqa: BLE001
                layers["ENTRY_ZONE"] = self._unavailable("ENTRY_ZONE", str(exc))

        retest_result = None
        if not can_continue:
            layers["PREMIUM_DISCOUNT"] = self._unavailable(
                "PREMIUM_DISCOUNT", "Required entry-timeframe data or direction is unavailable."
            )
            layers["RETEST_CONFIRMATION"] = self._unavailable(
                "RETEST_CONFIRMATION", "Required entry-timeframe data or direction is unavailable."
            )
        else:
            try:
                current_price = entry_candles[-1].close
                dealing_range = self._dealing_range_calculator.calculate(
                    entry_structure.swings, current_price, preview_direction
                )
                premium_discount_result = self._premium_discount_validator.validate(
                    dealing_range, preview_direction
                )
                layers["PREMIUM_DISCOUNT"] = self._from_validation(
                    premium_discount_result, "PREMIUM_DISCOUNT"
                )
            except Exception as exc:  # noqa: BLE001
                layers["PREMIUM_DISCOUNT"] = self._unavailable("PREMIUM_DISCOUNT", str(exc))

            if selected_zone is None:
                layers["RETEST_CONFIRMATION"] = self._unavailable(
                    "RETEST_CONFIRMATION", "No entry zone was selected to check a retest against."
                )
            else:
                try:
                    volume_ema_values = [entry_snapshot.volume_ema20] * len(entry_candles)
                    retest_result = self._retest_confirmation_detector.detect_latest_confirmed(
                        entry_candles, selected_zone, volume_ema_values, context.detection_time_utc
                    )
                    result = self._retest_confirmation_validator.validate(
                        retest_result, preview_direction, selected_zone
                    )
                    layers["RETEST_CONFIRMATION"] = self._from_validation(
                        result, "RETEST_CONFIRMATION"
                    )
                except Exception as exc:  # noqa: BLE001
                    layers["RETEST_CONFIRMATION"] = self._unavailable("RETEST_CONFIRMATION", str(exc))

        # Finalize VOLUME_CONFIRMATION now that retest (if any) is known.
        if displacement is not None:
            result = self._volume_confirmation_validator.validate(displacement, retest_result)
            layers["VOLUME_CONFIRMATION"] = self._from_validation(result)

        # SESSION_FILTER (degrades to the last available entry candle,
        # exactly like the production engine's fallback when no retest
        # has been confirmed yet).
        btc_structures_by_timeframe = (
            context.pipeline_metadata.get("btc_structures_by_timeframe")
            if context.pipeline_metadata
            else None
        )
        if entry_snapshot is None or entry_candles is None or not btc_structures_by_timeframe:
            layers["SESSION_FILTER"] = self._unavailable(
                "SESSION_FILTER", "Required entry-timeframe or BTC market data is unavailable."
            )
        else:
            try:
                btc_structure_4h = btc_structures_by_timeframe[HTF_PRIMARY]
                confirmation_candle = self._resolve_confirmation_candle(entry_candles, retest_result)
                btc_trend_strong = btc_structure_4h.trend_direction.value in ("BULLISH", "BEARISH")
                atr_high = entry_snapshot.atr is not None and entry_snapshot.atr > 0
                volume_high = (
                    confirmation_candle.volume is not None
                    and entry_snapshot.volume_ema20 is not None
                    and confirmation_candle.volume > entry_snapshot.volume_ema20
                )
                result = self._session_filter.validate(
                    context.detection_time_utc, btc_trend_strong, atr_high, volume_high
                )
                layers["SESSION_FILTER"] = self._from_validation(result)
            except Exception as exc:  # noqa: BLE001
                layers["SESSION_FILTER"] = self._unavailable("SESSION_FILTER", str(exc))

        # BTC_ALIGNMENT
        btc_htf_bias_result = (
            context.pipeline_metadata.get("btc_htf_bias_result") if context.pipeline_metadata else None
        )
        if not can_continue or not btc_structures_by_timeframe or btc_htf_bias_result is None:
            layers["BTC_ALIGNMENT"] = self._unavailable(
                "BTC_ALIGNMENT", "Required BTC market-structure data is unavailable."
            )
        else:
            try:
                result = self._btc_alignment_validator.validate(
                    context.symbol,
                    preview_direction,
                    btc_structures_by_timeframe[HTF_PRIMARY],
                    btc_htf_bias_result,
                )
                layers["BTC_ALIGNMENT"] = self._from_validation(result)
            except Exception as exc:  # noqa: BLE001
                layers["BTC_ALIGNMENT"] = self._unavailable("BTC_ALIGNMENT", str(exc))

        # FAKE_BREAKOUT_FILTER
        if not can_continue:
            layers["FAKE_BREAKOUT_FILTER"] = self._unavailable(
                "FAKE_BREAKOUT_FILTER", "Required entry-timeframe data or direction is unavailable."
            )
        else:
            try:
                result = self._fake_breakout_filter.validate(entry_candles, selected_sweep, selected_break)
                layers["FAKE_BREAKOUT_FILTER"] = self._from_validation(result)
            except Exception as exc:  # noqa: BLE001
                layers["FAKE_BREAKOUT_FILTER"] = self._unavailable("FAKE_BREAKOUT_FILTER", str(exc))

        return self._build_result(context.symbol, preview_direction, layers)

    def _build_result(
        self,
        symbol: str,
        preview_direction: Optional[str],
        layers: dict[str, PreviewLayerResult],
    ) -> PreviewAnalysisResult:
        raw_score = 0.0
        completed_layers: list[str] = []
        failed_layers: list[str] = []
        availability: dict[str, PreviewLayerStatus] = {}

        for layer_name in PREVIEW_LAYER_ORDER:
            layer = layers.get(layer_name)
            if layer is None:
                availability[layer_name] = PreviewLayerStatus.UNAVAILABLE
                continue

            availability[layer_name] = layer.status
            if layer.status == PreviewLayerStatus.PASSED:
                raw_score += self._layer_weights.get(layer_name, 0.0)
                completed_layers.append(layer_name)
            elif layer.status == PreviewLayerStatus.FAILED:
                failed_layers.append(layer_name)

        percentage = round((raw_score / self._max_score) * 100) if self._max_score else 0

        return PreviewAnalysisResult(
            symbol=symbol,
            preview_direction=preview_direction,
            preview_progress_raw_score=raw_score,
            preview_progress_max_score=self._max_score,
            preview_progress_percentage=percentage,
            preview_completed_layers=completed_layers,
            preview_failed_layers=failed_layers,
            preview_data_availability=availability,
        )

    @staticmethod
    def _resolve_preview_direction(context: MarketContext) -> Optional[str]:
        """
        Derive preview direction only from independently calculated 4H
        (primary) and 1H (secondary) HTF bias -- the same
        `htf_bias_result.final_bias` the production engine's stage 2
        reads, computed upfront before any mandatory stage runs. Never
        inferred from raw candles or EMA/momentum values alone.
        """
        htf_bias_result = context.htf_bias_result
        if htf_bias_result is None:
            return None
        if htf_bias_result.final_bias == HigherTimeframeBias.BULLISH:
            return "BUY"
        if htf_bias_result.final_bias == HigherTimeframeBias.BEARISH:
            return "SELL"
        return None

    @staticmethod
    def _select_latest_valid_sweep(
        confirmed_sweeps, expected_direction: str, detection_time_utc: datetime
    ) -> Optional[LiquiditySweepResult]:
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
    def _resolve_confirmation_candle(entry_candles, retest_result):
        if retest_result is not None:
            for candle in entry_candles:
                if candle.timestamp == retest_result.retest_candle_timestamp:
                    return candle
        return entry_candles[-1]

    @staticmethod
    def _from_validation(result, rename_to: Optional[str] = None) -> PreviewLayerResult:
        layer_name = rename_to or result.layer_name
        return PreviewLayerResult(
            layer_name=layer_name,
            status=PreviewLayerStatus.PASSED if result.passed else PreviewLayerStatus.FAILED,
            reason=result.reason,
        )

    @staticmethod
    def _unavailable(layer_name: str, reason: str) -> PreviewLayerResult:
        return PreviewLayerResult(
            layer_name=layer_name, status=PreviewLayerStatus.UNAVAILABLE, reason=reason
        )
