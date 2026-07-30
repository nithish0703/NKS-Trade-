"""
Tests for app.scanner.strategy_engine.InstitutionalSMCStrategyEngine
rejection paths, using mocked dependencies.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.market_structure.results import HigherTimeframeBias, TrendDirection
from app.models.candle import Candle
from app.models.validation_result import ValidationResult
from app.risk.results import RiskPlan, RiskPlanStatus
from app.scanner.pipeline_results import PipelineStatus
from app.scanner.strategy_engine import InstitutionalSMCStrategyEngine
from app.scoring.results import ConfidenceClassification, ConfidenceScoreResult

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _candle(index: int, symbol="BTC-USDT", timeframe="15m") -> Candle:
    price = 100.0 + index * 0.01
    return Candle(
        timestamp=UTC_NOW - timedelta(minutes=(50 - index)),
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=100.0,
        symbol=symbol,
        timeframe=timeframe,
    )


def _candles(count=50, symbol="BTC-USDT", timeframe="15m") -> list[Candle]:
    return [_candle(i, symbol, timeframe) for i in range(count)]


def _market_data(symbol: str) -> dict:
    return {"15m": _candles(symbol=symbol), "1h": _candles(symbol=symbol, timeframe="1h"), "4h": _candles(symbol=symbol, timeframe="4h")}


def _htf_bias(final_bias=HigherTimeframeBias.BULLISH, aligned=True):
    bias = MagicMock()
    bias.final_bias = final_bias
    bias.aligned = aligned
    return bias


def _structure(trend=TrendDirection.BULLISH):
    structure = MagicMock()
    structure.trend_direction = trend
    structure.swings = []
    return structure


def _snapshot():
    snapshot = MagicMock()
    snapshot.atr = 5.0
    snapshot.atr_expansion_ratio = 1.5
    snapshot.volume_ema20 = 100.0
    snapshot.ema20 = 105.0
    snapshot.ema50 = 100.0
    return snapshot


def _build_engine(**overrides) -> InstitutionalSMCStrategyEngine:
    """Build an engine with fully mocked, default-passing dependencies."""
    market_data_provider = AsyncMock()
    market_data_provider.fetch_symbol_market_data = AsyncMock(side_effect=lambda s: _market_data(s))

    candle_repository = MagicMock()

    indicator_calculator = MagicMock()
    indicator_calculator.calculate_multiple_timeframes = MagicMock(
        return_value={"15m": _snapshot(), "1h": _snapshot(), "4h": _snapshot()}
    )

    market_structure_calculator = MagicMock()
    market_structure_calculator.calculate_multiple_timeframes = MagicMock(
        return_value={"15m": _structure(), "1h": _structure(), "4h": _structure()}
    )
    market_structure_calculator.calculate_higher_timeframe_bias = MagicMock(return_value=_htf_bias())

    liquidity_calculator = MagicMock()
    liquidity_calculator.detect_levels = MagicMock(return_value=MagicMock(active_levels=[]))
    liquidity_calculator.detect_sweeps = MagicMock(return_value=[])

    structure_shift_calculator = MagicMock()
    shift_result = MagicMock()
    selected_break = MagicMock()
    selected_break.displacement = MagicMock()
    shift_result.latest_confirmed_break = selected_break
    shift_result.all_breaks = []
    structure_shift_calculator.calculate = MagicMock(return_value=shift_result)

    zone_calculator = MagicMock()
    zone_result = MagicMock()
    zone_result.selected_zone = MagicMock()
    zone_result.fair_value_gaps = []
    zone_calculator.calculate = MagicMock(return_value=zone_result)

    zone_setup_confirmation_calculator = MagicMock()
    setup_result = MagicMock()
    setup_result.dealing_range = MagicMock()
    setup_result.premium_discount_validation = ValidationResult.success(layer_name="premium_discount")
    setup_result.retest = MagicMock()
    setup_result.retest.retest_candle_timestamp = UTC_NOW
    setup_result.retest_validation = ValidationResult.success(layer_name="retest_confirmation")
    zone_setup_confirmation_calculator.calculate = MagicMock(return_value=setup_result)

    market_regime_validator = MagicMock()
    market_regime_validator.validate = MagicMock(
        return_value=ValidationResult.success(layer_name="MARKET_REGIME")
    )

    htf_bias_validator = MagicMock()
    htf_bias_validator.validate = MagicMock(return_value=ValidationResult.success(layer_name="HTF_BIAS"))

    liquidity_sweep_validator = MagicMock()
    liquidity_sweep_validator.validate = MagicMock(
        return_value=ValidationResult.success(layer_name="LIQUIDITY_SWEEP")
    )

    structure_shift_validator = MagicMock()
    structure_shift_validator.validate = MagicMock(
        return_value=ValidationResult.success(layer_name="structure_shift")
    )

    volume_confirmation_validator = MagicMock()
    volume_confirmation_validator.validate = MagicMock(
        return_value=ValidationResult.success(layer_name="VOLUME_CONFIRMATION")
    )

    entry_zone_validator = MagicMock()
    entry_zone_validator.validate = MagicMock(
        return_value=ValidationResult.success(layer_name="entry_zone")
    )

    session_filter = MagicMock()
    session_filter.validate = MagicMock(
        return_value=ValidationResult.success(layer_name="SESSION_FILTER")
    )

    btc_alignment_validator = MagicMock()
    btc_alignment_validator.validate = MagicMock(
        return_value=ValidationResult.success(layer_name="BTC_ALIGNMENT")
    )

    fake_breakout_filter = MagicMock()
    fake_breakout_filter.validate = MagicMock(
        return_value=ValidationResult.success(layer_name="FAKE_BREAKOUT_FILTER")
    )

    candle_quality_validator = MagicMock()
    candle_quality_validator.validate = MagicMock(
        return_value=ValidationResult.success(layer_name="CANDLE_QUALITY")
    )

    risk_management_calculator = MagicMock()
    risk_plan = MagicMock(spec=RiskPlan)
    risk_plan.status = RiskPlanStatus.VALID
    risk_plan.entry_price = 100.0
    risk_management_calculator.calculate = MagicMock(return_value=risk_plan)

    risk_management_validator = MagicMock()
    risk_management_validator.validate = MagicMock(
        return_value=ValidationResult.success(layer_name="RISK_MANAGEMENT")
    )

    confidence_calculator = MagicMock()
    confidence_calculator.calculate = MagicMock(
        return_value=ConfidenceScoreResult(
            raw_score=115.0,
            maximum_raw_score=115,
            normalized_score=100.0,
            classification=ConfidenceClassification.PREMIUM,
            publishable=True,
            mandatory_layers_passed=True,
            layer_scores=[],
            failed_mandatory_layers=[],
            reason="PREMIUM_CONFIDENCE",
        )
    )

    dependencies = dict(
        market_data_provider=market_data_provider,
        candle_repository=candle_repository,
        indicator_calculator=indicator_calculator,
        market_structure_calculator=market_structure_calculator,
        liquidity_calculator=liquidity_calculator,
        structure_shift_calculator=structure_shift_calculator,
        zone_calculator=zone_calculator,
        zone_setup_confirmation_calculator=zone_setup_confirmation_calculator,
        market_regime_validator=market_regime_validator,
        htf_bias_validator=htf_bias_validator,
        liquidity_sweep_validator=liquidity_sweep_validator,
        structure_shift_validator=structure_shift_validator,
        volume_confirmation_validator=volume_confirmation_validator,
        entry_zone_validator=entry_zone_validator,
        session_filter=session_filter,
        btc_alignment_validator=btc_alignment_validator,
        fake_breakout_filter=fake_breakout_filter,
        candle_quality_validator=candle_quality_validator,
        risk_management_calculator=risk_management_calculator,
        risk_management_validator=risk_management_validator,
        confidence_calculator=confidence_calculator,
    )
    dependencies.update(overrides)
    return InstitutionalSMCStrategyEngine(**dependencies)


async def _analyze(engine: InstitutionalSMCStrategyEngine, symbol="BTC-USDT"):
    return await engine.analyze_symbol(
        symbol=symbol,
        account_balance=10000.0,
        active_trade_count=0,
        active_positions=[],
        active_position_candles={},
        detection_time_utc=UTC_NOW,
    )


class TestStrategyEngineRejections:
    @pytest.mark.asyncio
    async def test_market_regime_failure_stops_immediately(self):
        market_regime_validator = MagicMock()
        market_regime_validator.validate = MagicMock(
            return_value=ValidationResult.failure(layer_name="MARKET_REGIME", reason="not trending", rejection_code="X")
        )
        engine = _build_engine(market_regime_validator=market_regime_validator)
        result = await _analyze(engine)
        assert result.status == PipelineStatus.REJECTED
        assert result.failed_layer == "MARKET_REGIME"
        executed_stages = [s for s in result.stages if s.executed]
        assert len(executed_stages) == 1

    @pytest.mark.asyncio
    async def test_mixed_htf_bias_stops(self):
        market_structure_calculator = MagicMock()
        market_structure_calculator.calculate_multiple_timeframes = MagicMock(
            return_value={"15m": _structure(), "1h": _structure(), "4h": _structure()}
        )
        market_structure_calculator.calculate_higher_timeframe_bias = MagicMock(
            return_value=_htf_bias(final_bias=HigherTimeframeBias.MIXED)
        )
        engine = _build_engine(market_structure_calculator=market_structure_calculator)
        result = await _analyze(engine)
        assert result.status == PipelineStatus.REJECTED
        assert result.failed_layer == "HTF_BIAS"

    @pytest.mark.asyncio
    async def test_missing_liquidity_sweep_stops(self):
        liquidity_sweep_validator = MagicMock()
        liquidity_sweep_validator.validate = MagicMock(
            return_value=ValidationResult.failure(layer_name="LIQUIDITY_SWEEP", reason="no sweep", rejection_code="X")
        )
        engine = _build_engine(liquidity_sweep_validator=liquidity_sweep_validator)
        result = await _analyze(engine)
        assert result.status == PipelineStatus.REJECTED
        assert result.failed_layer == "LIQUIDITY_SWEEP"

    @pytest.mark.asyncio
    async def test_structure_shift_failure_stops(self):
        structure_shift_validator = MagicMock()
        structure_shift_validator.validate = MagicMock(
            return_value=ValidationResult.failure(layer_name="structure_shift", reason="no shift", rejection_code="X")
        )
        engine = _build_engine(structure_shift_validator=structure_shift_validator)
        result = await _analyze(engine)
        assert result.status == PipelineStatus.REJECTED
        assert result.failed_layer == "STRUCTURE_SHIFT"

    @pytest.mark.asyncio
    async def test_displacement_volume_failure_stops(self):
        volume_confirmation_validator = MagicMock()
        volume_confirmation_validator.validate = MagicMock(
            return_value=ValidationResult.failure(layer_name="VOLUME_CONFIRMATION", reason="no volume", rejection_code="X")
        )
        engine = _build_engine(volume_confirmation_validator=volume_confirmation_validator)
        result = await _analyze(engine)
        assert result.status == PipelineStatus.REJECTED
        assert result.failed_layer == "VOLUME_CONFIRMATION"

    @pytest.mark.asyncio
    async def test_entry_zone_failure_stops(self):
        entry_zone_validator = MagicMock()
        entry_zone_validator.validate = MagicMock(
            return_value=ValidationResult.failure(layer_name="entry_zone", reason="no zone", rejection_code="X")
        )
        engine = _build_engine(entry_zone_validator=entry_zone_validator)
        result = await _analyze(engine)
        assert result.status == PipelineStatus.REJECTED
        assert result.failed_layer == "ENTRY_ZONE"

    @pytest.mark.asyncio
    async def test_premium_discount_failure_does_not_stop(self):
        zone_setup_confirmation_calculator = MagicMock()
        setup_result = MagicMock()
        setup_result.dealing_range = MagicMock()
        setup_result.premium_discount_validation = ValidationResult.failure(
            layer_name="premium_discount", reason="wrong zone", rejection_code="X"
        )
        setup_result.retest = None
        setup_result.retest_validation = ValidationResult.failure(layer_name="retest_confirmation", reason="n/a")
        zone_setup_confirmation_calculator.calculate = MagicMock(return_value=setup_result)
        engine = _build_engine(zone_setup_confirmation_calculator=zone_setup_confirmation_calculator)
        result = await _analyze(engine)
        # PREMIUM_DISCOUNT is a soft-scoring layer: failure never stops the
        # pipeline. It continues on to RETEST_CONFIRMATION, VOLUME_CONFIRMATION
        # phase B, and onward -- reaching VALID here since every other
        # dependency in _build_engine is a default pass.
        premium_discount_stage = next(s for s in result.stages if s.layer_name == "PREMIUM_DISCOUNT")
        assert premium_discount_stage.executed is True
        assert premium_discount_stage.passed is False
        assert premium_discount_stage.mandatory is False
        assert result.status == PipelineStatus.VALID

    @pytest.mark.asyncio
    async def test_retest_failure_does_not_stop(self):
        zone_setup_confirmation_calculator = MagicMock()
        setup_result = MagicMock()
        setup_result.dealing_range = MagicMock()
        setup_result.premium_discount_validation = ValidationResult.success(layer_name="premium_discount")
        setup_result.retest = None
        setup_result.retest_validation = ValidationResult.failure(
            layer_name="retest_confirmation", reason="no retest", rejection_code="X"
        )
        zone_setup_confirmation_calculator.calculate = MagicMock(return_value=setup_result)
        engine = _build_engine(zone_setup_confirmation_calculator=zone_setup_confirmation_calculator)
        result = await _analyze(engine)
        # RETEST_CONFIRMATION is a soft-scoring layer: failure never stops
        # the pipeline. VOLUME_CONFIRMATION phase B is finalized with
        # retest_result=None (never fabricated) and the mocked
        # volume_confirmation_validator still passes by default.
        retest_stage = next(s for s in result.stages if s.layer_name == "RETEST_CONFIRMATION")
        assert retest_stage.executed is True
        assert retest_stage.passed is False
        assert retest_stage.mandatory is False
        assert result.retest_result is None
        assert result.status == PipelineStatus.VALID

    @pytest.mark.asyncio
    async def test_session_failure_does_not_stop(self):
        session_filter = MagicMock()
        session_filter.validate = MagicMock(
            return_value=ValidationResult.failure(layer_name="SESSION_FILTER", reason="unsupported session", rejection_code="X")
        )
        engine = _build_engine(session_filter=session_filter)
        result = await _analyze(engine)
        session_stage = next(s for s in result.stages if s.layer_name == "SESSION_FILTER")
        assert session_stage.executed is True
        assert session_stage.passed is False
        assert session_stage.mandatory is False
        assert result.status == PipelineStatus.VALID

    @pytest.mark.asyncio
    async def test_btc_conflict_does_not_stop(self):
        btc_alignment_validator = MagicMock()
        btc_alignment_validator.validate = MagicMock(
            return_value=ValidationResult.failure(layer_name="BTC_ALIGNMENT", reason="conflict", rejection_code="X")
        )
        engine = _build_engine(btc_alignment_validator=btc_alignment_validator)
        result = await _analyze(engine)
        btc_stage = next(s for s in result.stages if s.layer_name == "BTC_ALIGNMENT")
        assert btc_stage.executed is True
        assert btc_stage.passed is False
        assert btc_stage.mandatory is False
        assert result.status == PipelineStatus.VALID

    @pytest.mark.asyncio
    async def test_fake_breakout_does_not_stop(self):
        fake_breakout_filter = MagicMock()
        fake_breakout_filter.validate = MagicMock(
            return_value=ValidationResult.failure(layer_name="FAKE_BREAKOUT_FILTER", reason="fake breakout", rejection_code="X")
        )
        engine = _build_engine(fake_breakout_filter=fake_breakout_filter)
        result = await _analyze(engine)
        fake_breakout_stage = next(s for s in result.stages if s.layer_name == "FAKE_BREAKOUT_FILTER")
        assert fake_breakout_stage.executed is True
        assert fake_breakout_stage.passed is False
        assert fake_breakout_stage.mandatory is False
        assert result.status == PipelineStatus.VALID

    @pytest.mark.asyncio
    async def test_candle_quality_failure_stops(self):
        candle_quality_validator = MagicMock()
        candle_quality_validator.validate = MagicMock(
            return_value=ValidationResult.failure(layer_name="CANDLE_QUALITY", reason="doji", rejection_code="X")
        )
        engine = _build_engine(candle_quality_validator=candle_quality_validator)
        result = await _analyze(engine)
        assert result.status == PipelineStatus.REJECTED
        assert result.failed_layer == "CANDLE_QUALITY"

    @pytest.mark.asyncio
    async def test_risk_management_failure_stops(self):
        risk_management_validator = MagicMock()
        risk_management_validator.validate = MagicMock(
            return_value=ValidationResult.failure(layer_name="RISK_MANAGEMENT", reason="rr too low", rejection_code="X")
        )
        engine = _build_engine(risk_management_validator=risk_management_validator)
        result = await _analyze(engine)
        assert result.status == PipelineStatus.REJECTED
        assert result.failed_layer == "RISK_MANAGEMENT"

    @pytest.mark.asyncio
    async def test_non_publishable_confidence_rejects(self):
        confidence_calculator = MagicMock()
        confidence_calculator.calculate = MagicMock(
            return_value=ConfidenceScoreResult(
                raw_score=85.0,
                maximum_raw_score=115,
                normalized_score=70.83,
                classification=ConfidenceClassification.MEDIUM,
                publishable=False,
                mandatory_layers_passed=True,
                layer_scores=[],
                failed_mandatory_layers=[],
                reason="MEDIUM_INTERNAL_ONLY",
            )
        )
        engine = _build_engine(confidence_calculator=confidence_calculator)
        result = await _analyze(engine)
        assert result.status == PipelineStatus.REJECTED
        assert result.failed_layer == "CONFIDENCE_SCORING"

    @pytest.mark.asyncio
    async def test_downstream_dependencies_not_called_after_failure(self):
        risk_management_calculator = MagicMock()
        market_regime_validator = MagicMock()
        market_regime_validator.validate = MagicMock(
            return_value=ValidationResult.failure(layer_name="MARKET_REGIME", reason="fail", rejection_code="X")
        )
        engine = _build_engine(
            market_regime_validator=market_regime_validator,
            risk_management_calculator=risk_management_calculator,
        )
        await _analyze(engine)
        risk_management_calculator.calculate.assert_not_called()

    @pytest.mark.asyncio
    async def test_later_stages_marked_executed_false(self):
        market_regime_validator = MagicMock()
        market_regime_validator.validate = MagicMock(
            return_value=ValidationResult.failure(layer_name="MARKET_REGIME", reason="fail", rejection_code="X")
        )
        engine = _build_engine(market_regime_validator=market_regime_validator)
        result = await _analyze(engine)
        non_executed = [s for s in result.stages if not s.executed]
        assert len(non_executed) == 13
        assert all(not s.passed for s in non_executed)
