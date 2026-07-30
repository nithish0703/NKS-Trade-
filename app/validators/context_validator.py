"""
Orchestrates market regime, session, BTC alignment, fake-breakout, and
candle-quality validation into a single pre-risk validation pipeline.
"""

from typing import Any, Optional, Sequence

from pydantic import BaseModel, ConfigDict

from app.indicators.results import IndicatorSnapshot
from app.liquidity.results import LiquiditySweepResult
from app.market_structure.results import HigherTimeframeBiasResult, MarketStructureResult
from app.market_structure.shift_results import StructureBreakResult
from app.models.candle import Candle
from app.models.market_context import MarketContext
from app.models.validation_result import ValidationResult

from app.validators.btc_alignment import BTCAlignmentValidator
from app.validators.candle_quality import CandleQualityValidator
from app.validators.fake_breakout_filter import FakeBreakoutFilter
from app.validators.market_regime import MarketRegimeValidator
from app.validators.session_filter import SessionFilter


class PreRiskValidationResult(BaseModel):
    """
    Aggregated result of the pre-risk validation pipeline: market
    regime, session, BTC alignment, fake breakout, and candle quality.

    This model does not calculate risk, stop loss, take profit,
    confidence score, or signal fields.
    """

    model_config = ConfigDict(frozen=True)

    market_regime: Optional[ValidationResult] = None
    session: Optional[ValidationResult] = None
    btc_alignment: Optional[ValidationResult] = None
    fake_breakout: Optional[ValidationResult] = None
    candle_quality: Optional[ValidationResult] = None
    passed: bool
    failed_layer: Optional[str] = None
    reason: str
    validation_results: list[ValidationResult]
    metadata: Optional[dict[str, Any]] = None


class PreRiskValidator:
    """
    Runs the ordered pre-risk validation pipeline: Market Regime
    (including its folded candle-range/compression check), Session,
    BTC Alignment, Fake Breakout, and Candle Quality.

    Every layer can stop the pipeline on failure.
    """

    def __init__(
        self,
        market_regime_validator: MarketRegimeValidator,
        session_filter: SessionFilter,
        btc_alignment_validator: BTCAlignmentValidator,
        fake_breakout_filter: FakeBreakoutFilter,
        candle_quality_validator: CandleQualityValidator,
    ) -> None:
        self._market_regime_validator = market_regime_validator
        self._session_filter = session_filter
        self._btc_alignment_validator = btc_alignment_validator
        self._fake_breakout_filter = fake_breakout_filter
        self._candle_quality_validator = candle_quality_validator

    def validate(
        self,
        market_context: MarketContext,
        expected_direction: str,
        entry_timeframe_candles: Sequence[Candle],
        entry_snapshot: IndicatorSnapshot,
        btc_structure: MarketStructureResult,
        btc_htf_bias: HigherTimeframeBiasResult,
        liquidity_sweep: LiquiditySweepResult,
        structure_break: StructureBreakResult,
        confirmation_candle: Candle,
    ) -> PreRiskValidationResult:
        """
        Run the ordered pre-risk validation pipeline.

        Market Regime, Session, BTC Alignment, Fake Breakout, and
        Candle Quality each stop the pipeline immediately on failure;
        later validators are never executed after an earlier failure.
        Does not calculate risk, score, or generate signals.
        """
        validation_results: list[ValidationResult] = []

        market_regime_result = self._market_regime_validator.validate(
            entry_snapshot, entry_timeframe_candles
        )
        validation_results.append(market_regime_result)
        if not market_regime_result.passed:
            return self._build_result(
                market_regime=market_regime_result,
                validation_results=validation_results,
                failed_layer=market_regime_result.layer_name,
            )

        btc_trend_strong = btc_structure.trend_direction.value in ("BULLISH", "BEARISH")
        atr_high = entry_snapshot.atr is not None and entry_snapshot.atr > 0
        volume_high = (
            entry_snapshot.volume_ratio is not None and entry_snapshot.volume_ratio > 1.0
        )
        session_result = self._session_filter.validate(
            market_context.detection_time_utc, btc_trend_strong, atr_high, volume_high
        )
        validation_results.append(session_result)
        if not session_result.passed:
            return self._build_result(
                market_regime=market_regime_result,
                session=session_result,
                validation_results=validation_results,
                failed_layer=session_result.layer_name,
            )

        btc_alignment_result = self._btc_alignment_validator.validate(
            market_context.symbol, expected_direction, btc_structure, btc_htf_bias
        )
        validation_results.append(btc_alignment_result)
        if not btc_alignment_result.passed:
            return self._build_result(
                market_regime=market_regime_result,
                session=session_result,
                btc_alignment=btc_alignment_result,
                validation_results=validation_results,
                failed_layer=btc_alignment_result.layer_name,
            )

        fake_breakout_result = self._fake_breakout_filter.validate(
            entry_timeframe_candles, liquidity_sweep, structure_break
        )
        validation_results.append(fake_breakout_result)
        if not fake_breakout_result.passed:
            return self._build_result(
                market_regime=market_regime_result,
                session=session_result,
                btc_alignment=btc_alignment_result,
                fake_breakout=fake_breakout_result,
                validation_results=validation_results,
                failed_layer=fake_breakout_result.layer_name,
            )

        candle_quality_result = self._candle_quality_validator.validate(
            confirmation_candle, expected_direction
        )
        validation_results.append(candle_quality_result)
        if not candle_quality_result.passed:
            return self._build_result(
                market_regime=market_regime_result,
                session=session_result,
                btc_alignment=btc_alignment_result,
                fake_breakout=fake_breakout_result,
                candle_quality=candle_quality_result,
                validation_results=validation_results,
                failed_layer=candle_quality_result.layer_name,
            )

        return self._build_result(
            market_regime=market_regime_result,
            session=session_result,
            btc_alignment=btc_alignment_result,
            fake_breakout=fake_breakout_result,
            candle_quality=candle_quality_result,
            validation_results=validation_results,
            failed_layer=None,
        )

    @staticmethod
    def _build_result(
        validation_results: list[ValidationResult],
        failed_layer: Optional[str],
        market_regime: Optional[ValidationResult] = None,
        session: Optional[ValidationResult] = None,
        btc_alignment: Optional[ValidationResult] = None,
        fake_breakout: Optional[ValidationResult] = None,
        candle_quality: Optional[ValidationResult] = None,
    ) -> PreRiskValidationResult:
        passed = failed_layer is None
        reason = (
            "All pre-risk validation layers passed."
            if passed
            else f"Pre-risk validation stopped at layer '{failed_layer}'."
        )
        return PreRiskValidationResult(
            market_regime=market_regime,
            session=session,
            btc_alignment=btc_alignment,
            fake_breakout=fake_breakout,
            candle_quality=candle_quality,
            passed=passed,
            failed_layer=failed_layer,
            reason=reason,
            validation_results=validation_results,
        )
