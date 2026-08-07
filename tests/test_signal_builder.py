"""
Tests for app.scanner.signal_builder.InstitutionalSignalBuilder.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.config.thresholds import MIN_RISK_REWARD_RATIO
from app.models.signal import Direction, SignalStatus
from app.risk.results import (
    CorrelationResult,
    CorrelationStatus,
    PositionRiskResult,
    RiskPlan,
    RiskPlanStatus,
    StopLossResult,
    StopLossSource,
    TakeProfitResult,
    TakeProfitSource,
)
from app.scanner.pipeline_results import PipelineStatus, StrategyPipelineResult
from app.scanner.scan_results import PairScanResult, PairScanStatus
from app.scanner.signal_builder import InstitutionalSignalBuilder, SignalBuildError

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _real_risk_plan(direction="BUY", entry_price=100.0, stop_loss=95.0, take_profit=110.0, rr=3.0):
    stop_loss_result = StopLossResult(
        direction=direction,
        entry_price=entry_price,
        selected_stop_loss=stop_loss,
        selected_source=StopLossSource.ATR,
        candidates=[],
        valid=True,
        reason="valid",
    )
    take_profit_result = TakeProfitResult(
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        selected_take_profit=take_profit,
        selected_source=TakeProfitSource.MAJOR_INSTITUTIONAL_LIQUIDITY,
        risk_reward_ratio=rr,
        candidates=[],
        valid=True,
        reason="valid",
    )
    position_risk = PositionRiskResult(
        account_balance=10000.0,
        risk_percentage=1.0,
        entry_price=entry_price,
        stop_loss=stop_loss,
        valid=True,
        reason="valid",
    )
    correlation_result = CorrelationResult(
        candidate_symbol="BTC-USDT",
        active_symbols=[],
        maximum_allowed_correlation=0.7,
        observed_correlations={},
        status=CorrelationStatus.ACCEPTABLE,
        acceptable=True,
        reason="valid",
    )
    return RiskPlan(
        direction=direction,
        entry_price=entry_price,
        stop_loss_result=stop_loss_result,
        take_profit_result=take_profit_result,
        position_risk=position_risk,
        correlation_result=correlation_result,
        active_trade_count=0,
        maximum_active_trades=5,
        risk_reward_ratio=rr,
        status=RiskPlanStatus.VALID,
        valid=True,
        reason="valid",
    )


def _sweep(sweep_id="sweep-1", liquidity_type="EQUAL_HIGH"):
    from app.liquidity.results import LiquidityType

    sweep = MagicMock()
    sweep.sweep_id = sweep_id
    sweep.liquidity_level.liquidity_type = LiquidityType(liquidity_type)
    return sweep


def _zone(zone_id="zone-1", zone_type="ORDER_BLOCK"):
    from app.models.trade_zone import ZoneType

    zone = MagicMock()
    zone.zone_id = zone_id
    zone.zone_type = ZoneType(zone_type)
    return zone


def _structure_break(break_id="break-1", break_type="BOS"):
    from app.market_structure.shift_results import StructureBreakType

    structure_break = MagicMock()
    structure_break.break_id = break_id
    structure_break.break_type = StructureBreakType(break_type)
    return structure_break


def _valid_pipeline_result(
    *,
    symbol="BTC-USDT",
    direction="BUY",
    risk_plan=None,
    sweep=None,
    zone=None,
    structure_break=None,
) -> StrategyPipelineResult:
    return StrategyPipelineResult(
        symbol=symbol,
        expected_direction=direction,
        detection_time_utc=UTC_NOW,
        status=PipelineStatus.VALID,
        passed=True,
        stages=[],
        liquidity_sweep=sweep or _sweep(),
        selected_structure_break=structure_break or _structure_break(),
        selected_entry_zone=zone or _zone(),
        risk_plan=risk_plan or _real_risk_plan(direction=direction),
    )


def _pair_scan_result(pipeline_result=None, status=PairScanStatus.VALID, duplicate=False) -> PairScanResult:
    resolved_pipeline_result = pipeline_result if pipeline_result is not None else _valid_pipeline_result()
    return PairScanResult(
        symbol=resolved_pipeline_result.symbol,
        status=status,
        pipeline_result=resolved_pipeline_result if status in (PairScanStatus.VALID, PairScanStatus.DUPLICATE) else None,
        duplicate_key="dup-key" if duplicate else None,
        duplicate=duplicate,
        started_at_utc=UTC_NOW,
        completed_at_utc=UTC_NOW,
        duration_ms=1.0,
        reason="Duplicate institutional setup suppressed." if duplicate else None,
    )


class TestSignalBuilderSuccess:
    def test_valid_buy_signal(self):
        builder = InstitutionalSignalBuilder()
        pair_result = _pair_scan_result(_valid_pipeline_result(direction="BUY"))
        signal = builder.build(pair_result)
        assert signal.direction == Direction.BUY
        assert signal.status == SignalStatus.CONFIRMED

    def test_valid_sell_signal(self):
        builder = InstitutionalSignalBuilder()
        risk_plan = _real_risk_plan(direction="SELL", entry_price=100.0, stop_loss=105.0, take_profit=90.0, rr=3.0)
        pair_result = _pair_scan_result(
            _valid_pipeline_result(direction="SELL", risk_plan=risk_plan)
        )
        signal = builder.build(pair_result)
        assert signal.direction == Direction.SELL
        assert signal.status == SignalStatus.CONFIRMED

    def test_exact_field_mapping(self):
        builder = InstitutionalSignalBuilder()
        pipeline_result = _valid_pipeline_result()
        pair_result = _pair_scan_result(pipeline_result)
        signal = builder.build(pair_result)

        assert signal.coin == "BTC-USDT"
        assert signal.entry_price == pipeline_result.risk_plan.entry_price
        assert signal.stop_loss == pipeline_result.risk_plan.stop_loss_result.selected_stop_loss
        assert signal.take_profit == pipeline_result.risk_plan.take_profit_result.selected_take_profit
        assert signal.risk_reward_ratio == pipeline_result.risk_plan.risk_reward_ratio
        assert signal.status == SignalStatus.CONFIRMED
        assert signal.liquidity_type == "EQUAL_HIGH"
        assert signal.entry_zone_type == "ORDER_BLOCK"
        assert signal.structure_confirmation == "BOS"
        assert signal.liquidity_sweep_id == "sweep-1"
        assert signal.entry_zone_id == "zone-1"
        assert signal.structure_break_id == "break-1"

    def test_deterministic_trade_id(self):
        builder = InstitutionalSignalBuilder()
        pipeline_result = _valid_pipeline_result()
        signal_one = builder.build(_pair_scan_result(pipeline_result))
        signal_two = builder.build(_pair_scan_result(pipeline_result))
        assert signal_one.trade_id == signal_two.trade_id
        assert signal_one.trade_id.startswith("SMC-")

    def test_deterministic_institutional_reason(self):
        builder = InstitutionalSignalBuilder()
        pipeline_result = _valid_pipeline_result()
        signal_one = builder.build(_pair_scan_result(pipeline_result))
        signal_two = builder.build(_pair_scan_result(pipeline_result))
        assert signal_one.institutional_reason == signal_two.institutional_reason
        assert "guarantee" not in signal_one.institutional_reason.lower() or "not a guarantee" in signal_one.institutional_reason.lower()

    def test_dynamic_sl_retained(self):
        builder = InstitutionalSignalBuilder()
        risk_plan = _real_risk_plan(stop_loss=93.5)
        signal = builder.build(_pair_scan_result(_valid_pipeline_result(risk_plan=risk_plan)))
        assert signal.stop_loss == 93.5

    def test_exactly_one_take_profit_retained(self):
        builder = InstitutionalSignalBuilder()
        signal = builder.build(_pair_scan_result(_valid_pipeline_result()))
        assert isinstance(signal.take_profit, float)

    def test_rr_retained(self):
        builder = InstitutionalSignalBuilder()
        risk_plan = _real_risk_plan(rr=4.5)
        signal = builder.build(_pair_scan_result(_valid_pipeline_result(risk_plan=risk_plan)))
        assert signal.risk_reward_ratio == 4.5

    def test_no_tp2_tp3_fields(self):
        builder = InstitutionalSignalBuilder()
        signal = builder.build(_pair_scan_result(_valid_pipeline_result()))
        fields = set(type(signal).model_fields.keys())
        assert fields.isdisjoint({"take_profit_2", "take_profit_3", "tp2", "tp3", "trailing_stop", "break_even"})

    def test_no_telegram_fields(self):
        builder = InstitutionalSignalBuilder()
        signal = builder.build(_pair_scan_result(_valid_pipeline_result()))
        fields = set(type(signal).model_fields.keys())
        assert fields.isdisjoint({"telegram_message_id", "telegram_chat_id", "published"})

    def test_input_not_mutated(self):
        builder = InstitutionalSignalBuilder()
        pair_result = _pair_scan_result(_valid_pipeline_result())
        original_symbol = pair_result.symbol
        builder.build(pair_result)
        assert pair_result.symbol == original_symbol

    def test_rr_at_1_85_builds_successfully(self):
        # Regression test: RiskManagementCalculator validates and passes
        # a RiskPlan at RR >= MIN_RISK_REWARD_RATIO (1.80), so
        # signal_builder must accept RR=1.85 rather than silently
        # discarding it against a stale hardcoded 2.0 cutoff.
        assert MIN_RISK_REWARD_RATIO == 1.80
        builder = InstitutionalSignalBuilder()
        risk_plan = _real_risk_plan(rr=1.85)
        signal = builder.build(_pair_scan_result(_valid_pipeline_result(risk_plan=risk_plan)))
        assert signal.risk_reward_ratio == 1.85


class TestSignalBuilderFailures:
    def test_missing_risk_plan_fails(self):
        builder = InstitutionalSignalBuilder()
        pipeline_result = _valid_pipeline_result()
        broken = pipeline_result.model_copy(update={"risk_plan": None})
        pair_result = PairScanResult.model_construct(
            symbol=broken.symbol,
            status=PairScanStatus.VALID,
            pipeline_result=broken,
            duplicate_key=None,
            duplicate=False,
            started_at_utc=UTC_NOW,
            completed_at_utc=UTC_NOW,
            duration_ms=1.0,
            reason=None,
            error_type=None,
            metadata=None,
        )
        with pytest.raises(SignalBuildError):
            builder.build(pair_result)

    def test_invalid_pipeline_status_fails(self):
        builder = InstitutionalSignalBuilder()
        pair_result = PairScanResult(
            symbol="BTC-USDT",
            status=PairScanStatus.REJECTED,
            pipeline_result=None,
            started_at_utc=UTC_NOW,
            completed_at_utc=UTC_NOW,
            duration_ms=1.0,
            reason="not trending",
        )
        with pytest.raises(SignalBuildError):
            builder.build(pair_result)

    def test_non_valid_scan_status_fails(self):
        builder = InstitutionalSignalBuilder()
        pair_result = PairScanResult(
            symbol="BTC-USDT",
            status=PairScanStatus.ERROR,
            pipeline_result=None,
            started_at_utc=UTC_NOW,
            completed_at_utc=UTC_NOW,
            duration_ms=1.0,
            reason="boom",
            error_type="RuntimeError",
        )
        with pytest.raises(SignalBuildError):
            builder.build(pair_result)

    def test_duplicate_scanner_result_fails(self):
        builder = InstitutionalSignalBuilder()
        pipeline_result = _valid_pipeline_result()
        pair_result = PairScanResult(
            symbol=pipeline_result.symbol,
            status=PairScanStatus.DUPLICATE,
            pipeline_result=pipeline_result,
            duplicate_key="dup-key",
            duplicate=True,
            started_at_utc=UTC_NOW,
            completed_at_utc=UTC_NOW,
            duration_ms=1.0,
            reason="Duplicate institutional setup suppressed.",
        )
        with pytest.raises(SignalBuildError):
            builder.build(pair_result)

    def test_missing_sweep_fails(self):
        builder = InstitutionalSignalBuilder()
        pipeline_result = _valid_pipeline_result()
        broken = pipeline_result.model_copy(update={"liquidity_sweep": None})
        pair_result = PairScanResult.model_construct(
            symbol=broken.symbol,
            status=PairScanStatus.VALID,
            pipeline_result=broken,
            duplicate_key=None,
            duplicate=False,
            started_at_utc=UTC_NOW,
            completed_at_utc=UTC_NOW,
            duration_ms=1.0,
            reason=None,
            error_type=None,
            metadata=None,
        )
        with pytest.raises(SignalBuildError):
            builder.build(pair_result)

    def test_missing_zone_fails(self):
        builder = InstitutionalSignalBuilder()
        pipeline_result = _valid_pipeline_result()
        broken = pipeline_result.model_copy(update={"selected_entry_zone": None})
        pair_result = PairScanResult.model_construct(
            symbol=broken.symbol,
            status=PairScanStatus.VALID,
            pipeline_result=broken,
            duplicate_key=None,
            duplicate=False,
            started_at_utc=UTC_NOW,
            completed_at_utc=UTC_NOW,
            duration_ms=1.0,
            reason=None,
            error_type=None,
            metadata=None,
        )
        with pytest.raises(SignalBuildError):
            builder.build(pair_result)

    def test_rr_below_minimum_fails(self):
        # Guards against the minimum RR being loosened by accident:
        # RR=1.79 is below MIN_RISK_REWARD_RATIO (1.80) and must still
        # raise SignalBuildError.
        assert MIN_RISK_REWARD_RATIO == 1.80
        builder = InstitutionalSignalBuilder()
        risk_plan = _real_risk_plan(rr=1.79)
        pair_result = _pair_scan_result(_valid_pipeline_result(risk_plan=risk_plan))
        with pytest.raises(SignalBuildError):
            builder.build(pair_result)

    def test_missing_structure_break_fails(self):
        builder = InstitutionalSignalBuilder()
        pipeline_result = _valid_pipeline_result()
        broken = pipeline_result.model_copy(update={"selected_structure_break": None})
        pair_result = PairScanResult.model_construct(
            symbol=broken.symbol,
            status=PairScanStatus.VALID,
            pipeline_result=broken,
            duplicate_key=None,
            duplicate=False,
            started_at_utc=UTC_NOW,
            completed_at_utc=UTC_NOW,
            duration_ms=1.0,
            reason=None,
            error_type=None,
            metadata=None,
        )
        with pytest.raises(SignalBuildError):
            builder.build(pair_result)
