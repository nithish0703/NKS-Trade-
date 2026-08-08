"""
Phase 1 acceptance tests: both reports must run against an empty
database without crashing, and against a populated database the
funnel's CONFIRMED count must reconcile with the number of signals
actually stored in that window. Also covers the seeded scenarios the
Phase 1 spec calls out by name: empty, single trade, all-wins,
all-losses, and mixed.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.analytics.funnel_report import format_funnel_report, generate_funnel_report
from app.analytics.performance_report import format_performance_report, generate_performance_report
from app.models.signal import Direction, Signal, SignalStatus
from app.models.validation_result import ValidationResult
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
from app.scanner.pipeline_results import PipelineStageResult, PipelineStatus, StrategyPipelineResult
from app.storage.analytics_repository import AnalyticsRepository
from app.storage.database import DatabaseManager
from app.storage.signal_repository import PASSIVE_OUTCOME_LOSS, PASSIVE_OUTCOME_WIN, SignalRepository

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

_STAGE_NAMES = ("HTF_BIAS", "LIQUIDITY_SWEEP", "BOS", "IFVG", "ORDER_FLOW", "RISK_MANAGEMENT")


def _stage(stage_order: int, layer_name: str, *, passed: bool) -> PipelineStageResult:
    return PipelineStageResult(
        stage_order=stage_order,
        layer_name=layer_name,
        mandatory=layer_name != "ORDER_FLOW",
        executed=True,
        passed=passed,
        reason="test",
        validation_result=ValidationResult(passed=passed, layer_name=layer_name, reason="test"),
        duration_ms=1.0,
    )


def _real_risk_plan(*, entry_price=100.0, stop_loss=95.0, take_profit=110.0, rr=3.0) -> RiskPlan:
    stop_loss_result = StopLossResult(
        direction="BUY",
        entry_price=entry_price,
        selected_stop_loss=stop_loss,
        selected_source=StopLossSource.ATR,
        candidates=[],
        valid=True,
        reason="valid",
    )
    take_profit_result = TakeProfitResult(
        direction="BUY",
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
        account_balance=10_000.0,
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
        direction="BUY",
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


def _confirmed_pipeline_result(symbol: str, *, entry_price=100.0, stop_loss=95.0, take_profit=110.0) -> StrategyPipelineResult:
    stages = [_stage(i + 1, name, passed=True) for i, name in enumerate(_STAGE_NAMES)]
    return StrategyPipelineResult(
        symbol=symbol,
        expected_direction="BUY",
        detection_time_utc=UTC_NOW,
        status=PipelineStatus.VALID,
        passed=True,
        stages=stages,
        risk_plan=_real_risk_plan(entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit),
        order_flow_confidence="HIGH",
        entry_grade="A",
    )


def _rejected_pipeline_result(symbol: str, *, failed_at: str = "LIQUIDITY_SWEEP") -> StrategyPipelineResult:
    failed_index = _STAGE_NAMES.index(failed_at)
    stages = [
        _stage(i + 1, name, passed=(i < failed_index)) for i, name in enumerate(_STAGE_NAMES[: failed_index + 1])
    ]
    return StrategyPipelineResult(
        symbol=symbol,
        expected_direction=None,
        detection_time_utc=UTC_NOW,
        status=PipelineStatus.REJECTED,
        passed=False,
        failed_layer=failed_at,
        rejection_reason=f"{failed_at} did not pass.",
        stages=stages,
    )


def _confirmed_signal(*, trade_id: str, coin="BTC-USDT", entry_price=100.0, stop_loss=95.0, take_profit=110.0) -> Signal:
    return Signal(
        trade_id=trade_id,
        coin=coin,
        direction=Direction.BUY,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward_ratio=3.0,
        status=SignalStatus.CONFIRMED,
        liquidity_type="EQUAL_HIGH",
        entry_zone_type="ORDER_BLOCK",
        structure_confirmation="BOS",
        detection_time_utc=UTC_NOW,
        institutional_reason="test",
        setup_key=f"setup-{trade_id}",
        liquidity_sweep_id="sweep-1",
        structure_break_id="break-1",
        entry_zone_id="zone-1",
        created_at_utc=UTC_NOW,
    )


@pytest_asyncio.fixture
async def repos(tmp_path):
    manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'phase1.db'}")
    await manager.initialize()
    analytics_repository = AnalyticsRepository(manager, enabled=True)
    signal_repository = SignalRepository(manager)
    yield analytics_repository, signal_repository
    await manager.dispose()


class TestEmptyDatabase:
    async def test_funnel_report_on_empty_db_prints_no_data_yet(self, repos):
        analytics_repository, _ = repos
        report = await generate_funnel_report(analytics_repository, window_days=7, now=UTC_NOW)
        assert report.stages == []
        assert report.confirmed_count == 0
        text = format_funnel_report(report)
        assert "no data yet" in text

    async def test_performance_report_on_empty_db_prints_no_data_yet(self, repos):
        _, signal_repository = repos
        report = await generate_performance_report(signal_repository, window_days=7, now=UTC_NOW)
        assert report.overall.trade_count == 0
        text = format_performance_report(report)
        assert "no data yet" in text


class TestFunnelConfirmedReconcilesWithStoredSignals:
    async def test_confirmed_count_equals_stored_signal_count(self, repos):
        analytics_repository, signal_repository = repos

        # Two genuinely CONFIRMED scans (all 6 stages pass) and one
        # REJECTED scan, each also actually persisted/not-persisted the
        # same way SignalStorageService would in production.
        confirmed_a = _confirmed_pipeline_result("BTC-USDT")
        confirmed_b = _confirmed_pipeline_result("ETH-USDT")
        rejected = _rejected_pipeline_result("SOL-USDT")

        for pipeline_result in (confirmed_a, confirmed_b, rejected):
            await analytics_repository.save_stage_results(pipeline_result)
        await analytics_repository.save_rejection(rejected)

        await signal_repository.save(_confirmed_signal(trade_id="SMC-A", coin="BTC-USDT"))
        await signal_repository.save(_confirmed_signal(trade_id="SMC-B", coin="ETH-USDT"))

        funnel = await generate_funnel_report(analytics_repository, window_days=7, now=UTC_NOW + timedelta(hours=1))
        stored_signal_count = await signal_repository.count()

        assert funnel.confirmed_count == 2
        assert stored_signal_count == 2
        assert funnel.confirmed_count == stored_signal_count

    async def test_rejection_appears_in_top_reasons_for_its_failed_layer(self, repos):
        analytics_repository, _ = repos
        rejected = _rejected_pipeline_result("SOL-USDT", failed_at="LIQUIDITY_SWEEP")
        await analytics_repository.save_stage_results(rejected)
        await analytics_repository.save_rejection(rejected)

        funnel = await generate_funnel_report(analytics_repository, window_days=7, now=UTC_NOW + timedelta(hours=1))

        assert "LIQUIDITY_SWEEP" in funnel.top_rejection_reasons_by_stage
        assert funnel.top_rejection_reasons_by_stage["LIQUIDITY_SWEEP"][0].reason == "LIQUIDITY_SWEEP did not pass."


class TestPerformanceReportSeededScenarios:
    async def test_single_trade(self, repos):
        _, signal_repository = repos
        await signal_repository.save(_confirmed_signal(trade_id="SMC-1"))
        await signal_repository.close_passive(
            "SMC-1", outcome=PASSIVE_OUTCOME_WIN, exit_price=110.0, closed_at_utc=UTC_NOW
        )

        report = await generate_performance_report(
            signal_repository, window_days=7, now=UTC_NOW + timedelta(hours=1)
        )

        assert report.overall.trade_count == 1
        assert report.overall.win_rate_percentage == 100.0

    async def test_all_wins(self, repos):
        _, signal_repository = repos
        for i in range(3):
            trade_id = f"SMC-WIN-{i}"
            await signal_repository.save(_confirmed_signal(trade_id=trade_id))
            await signal_repository.close_passive(
                trade_id, outcome=PASSIVE_OUTCOME_WIN, exit_price=110.0, closed_at_utc=UTC_NOW
            )

        report = await generate_performance_report(
            signal_repository, window_days=7, now=UTC_NOW + timedelta(hours=1)
        )

        assert report.overall.trade_count == 3
        assert report.overall.win_rate_percentage == 100.0
        assert report.overall.average_loss_r is None
        assert report.overall.profit_factor is None

    async def test_all_losses(self, repos):
        _, signal_repository = repos
        for i in range(3):
            trade_id = f"SMC-LOSS-{i}"
            await signal_repository.save(_confirmed_signal(trade_id=trade_id))
            await signal_repository.close_passive(
                trade_id, outcome=PASSIVE_OUTCOME_LOSS, exit_price=95.0, closed_at_utc=UTC_NOW
            )

        report = await generate_performance_report(
            signal_repository, window_days=7, now=UTC_NOW + timedelta(hours=1)
        )

        assert report.overall.trade_count == 3
        assert report.overall.win_rate_percentage == 0.0
        assert report.overall.average_win_r is None
        assert report.overall.profit_factor == 0.0

    async def test_mixed(self, repos):
        _, signal_repository = repos
        outcomes = [PASSIVE_OUTCOME_WIN, PASSIVE_OUTCOME_LOSS, PASSIVE_OUTCOME_WIN, PASSIVE_OUTCOME_LOSS]
        for i, outcome in enumerate(outcomes):
            trade_id = f"SMC-MIXED-{i}"
            exit_price = 110.0 if outcome == PASSIVE_OUTCOME_WIN else 95.0
            await signal_repository.save(_confirmed_signal(trade_id=trade_id))
            await signal_repository.close_passive(
                trade_id, outcome=outcome, exit_price=exit_price, closed_at_utc=UTC_NOW
            )

        report = await generate_performance_report(
            signal_repository, window_days=7, now=UTC_NOW + timedelta(hours=1)
        )

        assert report.overall.trade_count == 4
        assert report.overall.win_rate_percentage == 50.0
        assert report.overall.expectancy_r is not None

    async def test_still_open_trades_are_not_counted_as_closed(self, repos):
        _, signal_repository = repos
        await signal_repository.save(_confirmed_signal(trade_id="SMC-OPEN"))

        report = await generate_performance_report(
            signal_repository, window_days=7, now=UTC_NOW + timedelta(hours=1)
        )

        assert report.overall.trade_count == 0
        assert report.still_open_count == 1
