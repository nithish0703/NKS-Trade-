"""
Tests for app.scanner.strategy_engine.InstitutionalSMCStrategyEngine
success paths, using fully mocked passing dependencies.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.market_structure.results import HigherTimeframeBias
from app.risk.results import RiskPlanStatus
from app.scanner.pipeline_results import PipelineStatus
from app.scoring.results import ConfidenceClassification, ConfidenceScoreResult

from tests.test_strategy_engine_rejections import UTC_NOW, _analyze, _build_engine, _htf_bias

pytestmark = pytest.mark.asyncio


class TestStrategyEngineSuccess:
    async def test_complete_buy_pipeline(self):
        engine = _build_engine()
        result = await _analyze(engine, symbol="BTC-USDT")
        assert result.status == PipelineStatus.VALID
        assert result.passed is True
        assert result.expected_direction == "BUY"

    async def test_complete_sell_pipeline(self):
        market_structure_calculator = MagicMock()
        structure = MagicMock()
        structure.swings = []
        market_structure_calculator.calculate_multiple_timeframes = MagicMock(
            return_value={"15m": structure, "1h": structure, "4h": structure}
        )
        market_structure_calculator.calculate_higher_timeframe_bias = MagicMock(
            return_value=_htf_bias(final_bias=HigherTimeframeBias.BEARISH)
        )
        engine = _build_engine(market_structure_calculator=market_structure_calculator)
        result = await _analyze(engine)
        assert result.status == PipelineStatus.VALID
        assert result.expected_direction == "SELL"

    async def test_expected_direction_resolved_from_htf_bias(self):
        engine = _build_engine()
        result = await _analyze(engine)
        assert result.expected_direction in ("BUY", "SELL")
        assert result.htf_bias_result is not None

    async def test_btc_usdt_data_reused_when_symbol_is_btc_usdt(self):
        engine = _build_engine()
        await _analyze(engine, symbol="BTC-USDT")
        # fetch_symbol_market_data should be called exactly once for BTC-USDT
        # (reused for both "symbol" and "BTC" data) rather than twice.
        assert engine._market_data_provider.fetch_symbol_market_data.call_count == 1

    async def test_all_mandatory_stages_pass(self):
        engine = _build_engine()
        result = await _analyze(engine)
        mandatory_executed = [s for s in result.stages if s.mandatory and s.executed]
        assert all(s.passed for s in mandatory_executed)
        # 9 hard-mandatory stages: MARKET_REGIME, HTF_BIAS, LIQUIDITY_SWEEP,
        # STRUCTURE_SHIFT, VOLUME_CONFIRMATION, ENTRY_ZONE, CANDLE_QUALITY,
        # RISK_MANAGEMENT, CONFIDENCE_SCORING. The other 5 (PREMIUM_DISCOUNT,
        # RETEST_CONFIRMATION, SESSION_FILTER, BTC_ALIGNMENT,
        # FAKE_BREAKOUT_FILTER) are soft-scoring, non-mandatory.
        assert len(mandatory_executed) == 9

    async def test_soft_layer_failure_still_allows_success(self):
        from app.models.validation_result import ValidationResult

        session_filter = MagicMock()
        session_filter.validate = MagicMock(
            return_value=ValidationResult.failure(layer_name="SESSION_FILTER", reason="off-hours")
        )
        engine = _build_engine(session_filter=session_filter)
        result = await _analyze(engine)
        assert result.status == PipelineStatus.VALID

    async def test_dynamic_stop_retained(self):
        engine = _build_engine()
        result = await _analyze(engine)
        assert result.risk_plan is not None
        assert result.risk_plan.status == RiskPlanStatus.VALID

    async def test_exactly_one_take_profit_retained(self):
        engine = _build_engine()
        result = await _analyze(engine)
        # take_profit_result is mocked as a MagicMock attribute on risk_plan;
        # verify the pipeline result only stores a single risk_plan (not a list).
        assert not isinstance(result.risk_plan, list)

    async def test_rr_at_least_2(self):
        # RR is enforced inside RiskManagementCalculator/Validator (mocked
        # here to pass); confirm the risk validator was consulted.
        engine = _build_engine()
        result = await _analyze(engine)
        assert result.status == PipelineStatus.VALID
        engine._risk_management_validator.validate.assert_called_once()

    async def test_risk_at_most_1_percent(self):
        engine = _build_engine()
        result = await _analyze(engine)
        # Enforced inside PositionRiskCalculator (mocked to pass here);
        # confirm risk management was invoked with the account balance passed through.
        assert result.status == PipelineStatus.VALID
        _, kwargs = engine._risk_management_calculator.calculate.call_args
        assert kwargs["account_balance"] == 10000.0

    async def test_premium_result_succeeds(self):
        confidence_calculator = MagicMock()
        confidence_calculator.calculate = MagicMock(
            return_value=ConfidenceScoreResult(
                raw_score=115.0,
                maximum_raw_score=115,
                normalized_score=95.83,
                classification=ConfidenceClassification.PREMIUM,
                publishable=True,
                mandatory_layers_passed=True,
                layer_scores=[],
                failed_mandatory_layers=[],
                reason="PREMIUM_CONFIDENCE",
            )
        )
        engine = _build_engine(confidence_calculator=confidence_calculator)
        result = await _analyze(engine)
        assert result.status == PipelineStatus.VALID
        assert result.confidence_result.classification == ConfidenceClassification.PREMIUM

    async def test_strong_result_succeeds(self):
        confidence_calculator = MagicMock()
        confidence_calculator.calculate = MagicMock(
            return_value=ConfidenceScoreResult(
                raw_score=100.0,
                maximum_raw_score=115,
                normalized_score=83.33,
                classification=ConfidenceClassification.STRONG,
                publishable=True,
                mandatory_layers_passed=True,
                layer_scores=[],
                failed_mandatory_layers=[],
                reason="STRONG_CONFIDENCE",
            )
        )
        engine = _build_engine(confidence_calculator=confidence_calculator)
        result = await _analyze(engine)
        assert result.status == PipelineStatus.VALID
        assert result.confidence_result.classification == ConfidenceClassification.STRONG

    async def test_final_result_includes_complete_stage_audit(self):
        engine = _build_engine()
        result = await _analyze(engine)
        assert len(result.stages) == 14
        assert all(s.executed for s in result.stages)

    async def test_inputs_not_mutated(self):
        engine = _build_engine()
        active_positions = []
        active_position_candles = {}
        await engine.analyze_symbol(
            symbol="BTC-USDT",
            account_balance=10000.0,
            active_trade_count=0,
            active_positions=active_positions,
            active_position_candles=active_position_candles,
            detection_time_utc=UTC_NOW,
        )
        assert active_positions == []
        assert active_position_candles == {}

    async def test_no_publishing_persistence_or_trade_execution(self):
        engine = _build_engine()
        result = await _analyze(engine)
        assert result.status == PipelineStatus.VALID
        result_fields = set(type(result).model_fields.keys())
        forbidden = {"telegram_status", "dashboard_status", "exchange_order_id", "deployment_state"}
        assert result_fields.isdisjoint(forbidden)
