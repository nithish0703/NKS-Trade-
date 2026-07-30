"""
Tests for app.api.dashboard_service.DashboardService.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.dashboard_service import (
    DashboardService,
    build_pair_scan_updated_events,
    calculate_chart_trend,
    calculate_validation_progress,
)
from app.api.runtime_store import DashboardRuntimeStore
from app.models.candle import Candle
from app.models.market_context import MarketContext
from app.models.signal import Direction, MarketRegime, Signal, SignalType
from app.scanner.pipeline_results import PipelineStageResult, PipelineStatus, StrategyPipelineResult
from app.scanner.scan_results import PairScanResult, PairScanStatus, ScanCycleResult
from app.scoring.results import ConfidenceClassification, ConfidenceScoreResult
from app.storage.analytics_repository import RejectionRecord
from app.storage.signal_repository import (
    DASHBOARD_STATUS_ACTIVE,
    DASHBOARD_STATUS_NEW,
    SignalNotFoundError,
    SignalWithStatus,
)
from app.risk.results import (
    CorrelationResult,
    CorrelationStatus,
    PositionRiskResult,
    RiskPlan,
    RiskPlanStatus,
    StopLossResult,
    TakeProfitResult,
)


def _real_risk_plan() -> RiskPlan:
    stop_loss_result = StopLossResult(
        direction="BUY", entry_price=100.0, selected_stop_loss=95.0, candidates=[],
        valid=True, reason="valid",
    )
    take_profit_result = TakeProfitResult(
        direction="BUY", entry_price=100.0, stop_loss=95.0, selected_take_profit=110.0,
        risk_reward_ratio=3.0, candidates=[], valid=True, reason="valid",
    )
    position_risk = PositionRiskResult(
        account_balance=10000.0, risk_percentage=1.0, entry_price=100.0, stop_loss=95.0,
        valid=True, reason="valid",
    )
    correlation_result = CorrelationResult(
        candidate_symbol="BTC-USDT", active_symbols=[], maximum_allowed_correlation=0.7,
        observed_correlations={}, status=CorrelationStatus.ACCEPTABLE, acceptable=True,
        reason="valid",
    )
    return RiskPlan(
        direction="BUY", entry_price=100.0, stop_loss_result=stop_loss_result,
        take_profit_result=take_profit_result, position_risk=position_risk,
        correlation_result=correlation_result, active_trade_count=0,
        maximum_active_trades=5, risk_reward_ratio=3.0, status=RiskPlanStatus.VALID,
        valid=True, reason="valid",
    )

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _signal(
    *,
    trade_id="SMC-1",
    coin="BTC-USDT",
    signal_type=SignalType.PREMIUM,
    confidence_score=95.0,
    direction=Direction.BUY,
    risk_reward_ratio=3.0,
) -> Signal:
    return Signal(
        trade_id=trade_id,
        coin=coin,
        direction=direction,
        entry_price=100.0,
        stop_loss=95.0 if direction == Direction.BUY else 105.0,
        take_profit=110.0 if direction == Direction.BUY else 90.0,
        risk_reward_ratio=risk_reward_ratio,
        confidence_score=confidence_score,
        signal_type=signal_type,
        market_regime=MarketRegime.TRENDING,
        higher_timeframe_bias="BULLISH",
        liquidity_type="EQUAL_HIGH",
        entry_zone_type="ORDER_BLOCK",
        structure_confirmation="BOS",
        volume_confirmation=True,
        atr_status="EXPANDING",
        trading_session="LONDON",
        btc_market_alignment=True,
        detection_time_utc=UTC_NOW,
        institutional_reason="Confirmed setup facts only.",
        setup_key=f"setup-{trade_id}",
        liquidity_sweep_id="sweep-1",
        structure_break_id="break-1",
        entry_zone_id="zone-1",
        retest_id="retest-1",
        created_at_utc=UTC_NOW,
    )


def _build_service(
    signal_repository=None,
    analytics_repository=None,
    runtime_store=None,
    market_data_provider=None,
    telegram_enabled=False,
    websocket_enabled=False,
):
    return DashboardService(
        signal_repository=signal_repository or MagicMock(),
        analytics_repository=analytics_repository,
        runtime_store=runtime_store or DashboardRuntimeStore(),
        market_data_provider=market_data_provider or MagicMock(),
        telegram_enabled=telegram_enabled,
        websocket_enabled=websocket_enabled,
    )


class TestSummary:
    async def test_summary_from_real_repository_data(self):
        repository = MagicMock()
        repository.count = AsyncMock(return_value=5)

        async def _list_recent(limit, signal_type=None, symbol=None):
            if signal_type == SignalType.PREMIUM.value:
                return [_signal(trade_id="P1", signal_type=SignalType.PREMIUM, risk_reward_ratio=3.0)]
            if signal_type == SignalType.STRONG.value:
                return [_signal(trade_id="S1", signal_type=SignalType.STRONG, risk_reward_ratio=2.0)]
            return []

        repository.list_recent = AsyncMock(side_effect=_list_recent)
        service = _build_service(signal_repository=repository)

        summary = await service.get_summary()

        assert summary.total_signals == 5
        assert summary.premium_count == 1
        assert summary.strong_count == 1
        assert summary.average_rr == pytest.approx(2.5)

    async def test_zero_data_summary(self):
        repository = MagicMock()
        repository.count = AsyncMock(return_value=0)
        repository.list_recent = AsyncMock(return_value=[])
        service = _build_service(signal_repository=repository)

        summary = await service.get_summary()

        assert summary.total_signals == 0
        assert summary.premium_count == 0
        assert summary.strong_count == 0
        assert summary.average_rr is None

    async def test_wins_losses_unavailable_state(self):
        repository = MagicMock()
        repository.count = AsyncMock(return_value=0)
        repository.list_recent = AsyncMock(return_value=[])
        service = _build_service(signal_repository=repository)

        summary = await service.get_summary()

        assert summary.wins == 0
        assert summary.losses == 0
        assert summary.win_rate is None
        assert summary.open_signals == 0

    async def test_average_rr_computed_across_premium_and_strong(self):
        repository = MagicMock()
        repository.count = AsyncMock(return_value=2)

        async def _list_recent(limit, signal_type=None, symbol=None):
            if signal_type == SignalType.PREMIUM.value:
                return [_signal(trade_id="P1", risk_reward_ratio=4.0)]
            if signal_type == SignalType.STRONG.value:
                return [_signal(trade_id="S1", signal_type=SignalType.STRONG, risk_reward_ratio=2.0)]
            return []

        repository.list_recent = AsyncMock(side_effect=_list_recent)
        service = _build_service(signal_repository=repository)

        summary = await service.get_summary()
        assert summary.average_rr == pytest.approx(3.0)

    async def test_scanner_running_reflects_runtime_store(self):
        repository = MagicMock()
        repository.count = AsyncMock(return_value=0)
        repository.list_recent = AsyncMock(return_value=[])
        runtime_store = DashboardRuntimeStore()
        runtime_store.set_scanner_running(True)
        service = _build_service(signal_repository=repository, runtime_store=runtime_store)

        summary = await service.get_summary()
        assert summary.scanner_running is True


class TestScanningCoins:
    async def test_scanning_pairs_mapping_preserves_configured_order(self):
        from app.config.pairs import get_configured_pairs

        runtime_store = DashboardRuntimeStore()
        service = _build_service(runtime_store=runtime_store)

        coins = await service.get_scanning_coins()
        configured = get_configured_pairs()
        assert [c.coin for c in coins] == configured
        # No pair results recorded yet: everything is SCANNING with no score.
        assert all(c.status == "SCANNING" and c.score is None for c in coins)

    async def test_valid_pipeline_result_maps_to_ready_with_score(self):
        runtime_store = DashboardRuntimeStore()
        confidence_result = ConfidenceScoreResult(
            raw_score=115.0,
            maximum_raw_score=115,
            normalized_score=96.0,
            classification=ConfidenceClassification.PREMIUM,
            publishable=True,
            mandatory_layers_passed=True,
            layer_scores=[],
            failed_mandatory_layers=[],
            reason="PREMIUM",
        )
        risk_plan = _real_risk_plan()
        pipeline_result = StrategyPipelineResult(
            symbol="BTC-USDT",
            expected_direction="BUY",
            detection_time_utc=UTC_NOW,
            status=PipelineStatus.VALID,
            passed=True,
            stages=[],
            risk_plan=risk_plan,
            confidence_result=confidence_result,
        )
        pair_result = PairScanResult(
            symbol="BTC-USDT",
            status=PairScanStatus.VALID,
            pipeline_result=pipeline_result,
            started_at_utc=UTC_NOW,
            completed_at_utc=UTC_NOW,
            duration_ms=1.0,
        )
        cycle_result = ScanCycleResult(
            cycle_id="c1",
            started_at_utc=UTC_NOW,
            completed_at_utc=UTC_NOW,
            duration_ms=1.0,
            configured_pairs=["BTC-USDT"],
            attempted_pairs=["BTC-USDT"],
            valid_results=[pair_result],
            rejected_results=[],
            duplicate_results=[],
            error_results=[],
            skipped_results=[],
            pair_results=[pair_result],
            total_pairs=1,
            valid_count=1,
            rejected_count=0,
            duplicate_count=0,
            error_count=0,
            skipped_count=0,
        )
        await runtime_store.record_cycle_result(cycle_result)
        service = _build_service(runtime_store=runtime_store)

        coins = await service.get_scanning_coins()
        btc = next(c for c in coins if c.coin == "BTC-USDT")
        assert btc.status == "READY"
        assert btc.score == 96.0
        assert btc.direction == "BUY"

    async def test_rejected_pipeline_result_has_no_score(self):
        runtime_store = DashboardRuntimeStore()
        pipeline_result = StrategyPipelineResult(
            symbol="ETH-USDT",
            expected_direction=None,
            detection_time_utc=UTC_NOW,
            status=PipelineStatus.REJECTED,
            passed=False,
            failed_layer="MARKET_REGIME",
            rejection_reason="not trending",
            stages=[],
        )
        pair_result = PairScanResult(
            symbol="ETH-USDT",
            status=PairScanStatus.REJECTED,
            pipeline_result=pipeline_result,
            started_at_utc=UTC_NOW,
            completed_at_utc=UTC_NOW,
            duration_ms=1.0,
            reason="not trending",
        )
        cycle_result = ScanCycleResult(
            cycle_id="c1",
            started_at_utc=UTC_NOW,
            completed_at_utc=UTC_NOW,
            duration_ms=1.0,
            configured_pairs=["ETH-USDT"],
            attempted_pairs=["ETH-USDT"],
            valid_results=[],
            rejected_results=[pair_result],
            duplicate_results=[],
            error_results=[],
            skipped_results=[],
            pair_results=[pair_result],
            total_pairs=1,
            valid_count=0,
            rejected_count=1,
            duplicate_count=0,
            error_count=0,
            skipped_count=0,
        )
        await runtime_store.record_cycle_result(cycle_result)
        service = _build_service(runtime_store=runtime_store)

        coins = await service.get_scanning_coins()
        eth = next(c for c in coins if c.coin == "ETH-USDT")
        assert eth.status == "REJECTED"
        assert eth.score is None
        assert eth.failed_layer == "MARKET_REGIME"

    async def test_rejected_pipeline_result_surfaces_preview_fields(self):
        from app.scanner.preview_analyzer import PreviewAnalysisResult, PreviewLayerStatus

        runtime_store = DashboardRuntimeStore()
        preview = PreviewAnalysisResult(
            symbol="ETH-USDT",
            preview_direction="BUY",
            preview_progress_raw_score=40.0,
            preview_progress_max_score=120.0,
            preview_progress_percentage=33,
            preview_completed_layers=["MARKET_REGIME", "HTF_BIAS"],
            preview_failed_layers=["LIQUIDITY_SWEEP"],
            preview_data_availability={
                "MARKET_REGIME": PreviewLayerStatus.PASSED,
                "LIQUIDITY_SWEEP": PreviewLayerStatus.FAILED,
            },
        )
        pipeline_result = StrategyPipelineResult(
            symbol="ETH-USDT",
            expected_direction=None,
            detection_time_utc=UTC_NOW,
            status=PipelineStatus.REJECTED,
            passed=False,
            failed_layer="MARKET_REGIME",
            rejection_reason="not trending",
            stages=[],
        )
        pair_result = PairScanResult(
            symbol="ETH-USDT",
            status=PairScanStatus.REJECTED,
            pipeline_result=pipeline_result,
            started_at_utc=UTC_NOW,
            completed_at_utc=UTC_NOW,
            duration_ms=1.0,
            reason="not trending",
            preview_result=preview,
        )
        cycle_result = ScanCycleResult(
            cycle_id="c1", started_at_utc=UTC_NOW, completed_at_utc=UTC_NOW, duration_ms=1.0,
            configured_pairs=["ETH-USDT"], attempted_pairs=["ETH-USDT"],
            valid_results=[], rejected_results=[pair_result], duplicate_results=[],
            error_results=[], skipped_results=[], pair_results=[pair_result],
            total_pairs=1, valid_count=0, rejected_count=1, duplicate_count=0,
            error_count=0, skipped_count=0,
        )
        await runtime_store.record_cycle_result(cycle_result)
        service = _build_service(runtime_store=runtime_store)

        coins = await service.get_scanning_coins()
        eth = next(c for c in coins if c.coin == "ETH-USDT")

        # Real pipeline outcome remains REJECTED with no score/direction...
        assert eth.status == "REJECTED"
        assert eth.direction is None
        assert eth.score is None
        # ...while the independently computed preview is also surfaced,
        # never overwriting the real fields above.
        assert eth.preview_direction == "BUY"
        assert eth.preview_progress_percentage == 33
        assert eth.preview_completed_layers == ["MARKET_REGIME", "HTF_BIAS"]
        assert eth.preview_failed_layers == ["LIQUIDITY_SWEEP"]
        assert eth.preview_data_availability == {
            "MARKET_REGIME": "PASSED",
            "LIQUIDITY_SWEEP": "FAILED",
        }

    async def test_no_preview_result_leaves_preview_fields_none(self):
        runtime_store = DashboardRuntimeStore()
        pipeline_result = StrategyPipelineResult(
            symbol="ETH-USDT",
            expected_direction=None,
            detection_time_utc=UTC_NOW,
            status=PipelineStatus.REJECTED,
            passed=False,
            failed_layer="MARKET_REGIME",
            rejection_reason="not trending",
            stages=[],
        )
        pair_result = PairScanResult(
            symbol="ETH-USDT",
            status=PairScanStatus.REJECTED,
            pipeline_result=pipeline_result,
            started_at_utc=UTC_NOW,
            completed_at_utc=UTC_NOW,
            duration_ms=1.0,
            reason="not trending",
        )
        cycle_result = ScanCycleResult(
            cycle_id="c1", started_at_utc=UTC_NOW, completed_at_utc=UTC_NOW, duration_ms=1.0,
            configured_pairs=["ETH-USDT"], attempted_pairs=["ETH-USDT"],
            valid_results=[], rejected_results=[pair_result], duplicate_results=[],
            error_results=[], skipped_results=[], pair_results=[pair_result],
            total_pairs=1, valid_count=0, rejected_count=1, duplicate_count=0,
            error_count=0, skipped_count=0,
        )
        await runtime_store.record_cycle_result(cycle_result)
        service = _build_service(runtime_store=runtime_store)

        coins = await service.get_scanning_coins()
        eth = next(c for c in coins if c.coin == "ETH-USDT")
        assert eth.preview_direction is None
        assert eth.preview_progress_percentage is None
        assert eth.preview_completed_layers is None


# Full 14-stage ordering, mirroring app.scanner.strategy_engine._STAGE_DEFINITIONS.
_ALL_STAGE_NAMES = (
    "MARKET_REGIME", "HTF_BIAS", "LIQUIDITY_SWEEP", "STRUCTURE_SHIFT",
    "VOLUME_CONFIRMATION", "ENTRY_ZONE", "PREMIUM_DISCOUNT", "RETEST_CONFIRMATION",
    "SESSION_FILTER", "BTC_ALIGNMENT", "FAKE_BREAKOUT_FILTER", "CANDLE_QUALITY",
    "RISK_MANAGEMENT", "CONFIDENCE_SCORING",
)
_NON_MANDATORY_STAGES = {
    "PREMIUM_DISCOUNT", "RETEST_CONFIRMATION", "SESSION_FILTER",
    "BTC_ALIGNMENT", "FAKE_BREAKOUT_FILTER",
}


def _stages(passed_through: str) -> list[PipelineStageResult]:
    """
    Build a full 17-stage list where every stage up to and including
    `passed_through` is executed and passed, and every stage after it is
    not executed. Mirrors _build_rejected_result's real backfill shape.
    """
    stages = []
    passed_index = _ALL_STAGE_NAMES.index(passed_through)
    for order, name in enumerate(_ALL_STAGE_NAMES, start=1):
        executed = order - 1 <= passed_index
        stages.append(
            PipelineStageResult(
                stage_order=order,
                layer_name=name,
                mandatory=name not in _NON_MANDATORY_STAGES,
                executed=executed,
                passed=executed,
                duration_ms=1.0,
            )
        )
    return stages


def _stages_with_failure(failed_at: str) -> list[PipelineStageResult]:
    """Like _stages, but the named stage executes and fails (all later stages not executed)."""
    stages = []
    failed_index = _ALL_STAGE_NAMES.index(failed_at)
    for order, name in enumerate(_ALL_STAGE_NAMES, start=1):
        index = order - 1
        executed = index <= failed_index
        passed = executed and index < failed_index
        stages.append(
            PipelineStageResult(
                stage_order=order,
                layer_name=name,
                mandatory=name not in _NON_MANDATORY_STAGES,
                executed=executed,
                passed=passed,
                duration_ms=1.0,
            )
        )
    return stages


def _candle(close: float, symbol="BTC-USDT", timeframe="15m", minutes_ago=0) -> Candle:
    ts = UTC_NOW - timedelta(minutes=minutes_ago)
    return Candle(
        timestamp=ts, open=close, high=close + 1, low=close - 1, close=close,
        volume=100.0, symbol=symbol, timeframe=timeframe,
    )


def _context_with_candles(candles) -> MarketContext:
    return MarketContext(
        symbol="BTC-USDT",
        detection_time_utc=UTC_NOW,
        candles_by_timeframe={"15m": candles},
        btc_candles_by_timeframe={},
    )


class TestChartTrend:
    def test_higher_close_gives_up(self):
        candles = [_candle(100.0, minutes_ago=15), _candle(105.0, minutes_ago=0)]
        assert calculate_chart_trend(_context_with_candles(candles)) == "UP"

    def test_lower_close_gives_down(self):
        candles = [_candle(105.0, minutes_ago=15), _candle(100.0, minutes_ago=0)]
        assert calculate_chart_trend(_context_with_candles(candles)) == "DOWN"

    def test_equal_close_gives_none(self):
        candles = [_candle(100.0, minutes_ago=15), _candle(100.0, minutes_ago=0)]
        assert calculate_chart_trend(_context_with_candles(candles)) is None

    def test_fewer_than_two_candles_gives_none(self):
        candles = [_candle(100.0)]
        assert calculate_chart_trend(_context_with_candles(candles)) is None

    def test_no_candles_gives_none(self):
        assert calculate_chart_trend(_context_with_candles([])) is None

    def test_none_market_context_gives_none(self):
        assert calculate_chart_trend(None) is None

    def test_never_derived_from_htf_bias(self):
        # Distinct from direction/preview_direction: chart_trend must be
        # computable even when htf_bias_result is absent entirely.
        context = MarketContext(
            symbol="BTC-USDT",
            detection_time_utc=UTC_NOW,
            candles_by_timeframe={"15m": [_candle(100.0, minutes_ago=15), _candle(105.0, minutes_ago=0)]},
            btc_candles_by_timeframe={},
            htf_bias_result=None,
        )
        assert calculate_chart_trend(context) == "UP"


class TestValidationProgressCalculation:
    def test_market_regime_only_gives_15_raw_points(self):
        raw, maximum, percentage, last_layer = calculate_validation_progress(
            _stages("MARKET_REGIME")
        )
        assert raw == 15
        assert maximum == 115
        assert percentage == 13
        assert last_layer == "MARKET_REGIME"

    def test_market_regime_plus_htf_bias_gives_40_raw_points(self):
        # MARKET_REGIME=15 + HTF_BIAS=25 (the real SCORE_HTF_BIAS constant
        # from app.config.thresholds, which ConfidenceScoringEngine also
        # uses) = 40/115 = 35%.
        raw, maximum, percentage, last_layer = calculate_validation_progress(_stages("HTF_BIAS"))
        assert raw == 40
        assert percentage == 35
        assert last_layer == "HTF_BIAS"

    def test_failed_layer_gets_zero_points(self):
        raw, _, _, last_layer = calculate_validation_progress(_stages_with_failure("HTF_BIAS"))
        # MARKET_REGIME (15) passed, HTF_BIAS executed but failed (0 points).
        assert raw == 15
        assert last_layer == "HTF_BIAS"

    def test_non_executed_layers_get_zero(self):
        raw, _, _, _ = calculate_validation_progress(_stages("MARKET_REGIME"))
        # Only MARKET_REGIME's 15 points; every later non-executed scoring
        # layer (HTF_BIAS=25, LIQUIDITY_SWEEP=15, ...) contributes zero.
        assert raw == 15

    def test_all_scoring_layers_pass_gives_115_and_100_percent(self):
        raw, maximum, percentage, last_layer = calculate_validation_progress(
            _stages("CONFIDENCE_SCORING")
        )
        assert raw == 115
        assert maximum == 115
        assert percentage == 100
        assert last_layer == "CONFIDENCE_SCORING"

    def test_empty_stages_returns_zero_and_no_last_layer(self):
        raw, maximum, percentage, last_layer = calculate_validation_progress([])
        assert raw == 0.0
        assert maximum == 115
        assert percentage == 0
        assert last_layer is None

    def test_none_stages_returns_zero_and_no_last_layer(self):
        raw, maximum, percentage, last_layer = calculate_validation_progress(None)
        assert raw == 0.0
        assert percentage == 0
        assert last_layer is None

    def test_non_scoring_stages_never_contribute_points(self):
        # CANDLE_QUALITY (stage 12) executes and passes but is not in the
        # 115-point weight map (it's a structural/account-safety gate, not
        # a scoring layer), so it must not add any points beyond the 11
        # scoring layers that ran before it (which sum to the full 115).
        raw, _, _, _ = calculate_validation_progress(_stages("CANDLE_QUALITY"))
        assert raw == 115


class TestValidationProgressDoesNotAffectStrategy:
    async def test_rejected_setup_retains_partial_progress_score(self):
        runtime_store = DashboardRuntimeStore()
        pipeline_result = StrategyPipelineResult(
            symbol="ETH-USDT",
            expected_direction=None,
            detection_time_utc=UTC_NOW,
            status=PipelineStatus.REJECTED,
            passed=False,
            failed_layer="LIQUIDITY_SWEEP",
            rejection_reason="Institutional liquidity sweep missing",
            stages=_stages_with_failure("LIQUIDITY_SWEEP"),
        )
        pair_result = PairScanResult(
            symbol="ETH-USDT",
            status=PairScanStatus.REJECTED,
            pipeline_result=pipeline_result,
            started_at_utc=UTC_NOW,
            completed_at_utc=UTC_NOW,
            duration_ms=1.0,
            reason="Institutional liquidity sweep missing",
        )
        cycle_result = ScanCycleResult(
            cycle_id="c1", started_at_utc=UTC_NOW, completed_at_utc=UTC_NOW, duration_ms=1.0,
            configured_pairs=["ETH-USDT"], attempted_pairs=["ETH-USDT"],
            valid_results=[], rejected_results=[pair_result], duplicate_results=[],
            error_results=[], skipped_results=[], pair_results=[pair_result],
            total_pairs=1, valid_count=0, rejected_count=1, duplicate_count=0,
            error_count=0, skipped_count=0,
        )
        await runtime_store.record_cycle_result(cycle_result)
        service = _build_service(runtime_store=runtime_store)

        coins = await service.get_scanning_coins()
        eth = next(c for c in coins if c.coin == "ETH-USDT")

        # Still rejected internally...
        assert eth.status == "REJECTED"
        assert eth.score is None
        # ...but a partial validation-progress percentage is still shown.
        assert eth.validation_progress_raw_score == 40  # MARKET_REGIME + HTF_BIAS passed
        assert eth.validation_progress_percentage == 35
        assert eth.last_executed_layer == "LIQUIDITY_SWEEP"

    async def test_partial_score_never_changes_final_confidence(self):
        runtime_store = DashboardRuntimeStore()
        confidence_result = ConfidenceScoreResult(
            raw_score=115.0, maximum_raw_score=115, normalized_score=96.0,
            classification=ConfidenceClassification.PREMIUM, publishable=True,
            mandatory_layers_passed=True, layer_scores=[], failed_mandatory_layers=[],
            reason="PREMIUM",
        )
        risk_plan = _real_risk_plan()
        pipeline_result = StrategyPipelineResult(
            symbol="BTC-USDT", expected_direction="BUY", detection_time_utc=UTC_NOW,
            status=PipelineStatus.VALID, passed=True,
            stages=_stages("CONFIDENCE_SCORING"),
            risk_plan=risk_plan, confidence_result=confidence_result,
        )
        pair_result = PairScanResult(
            symbol="BTC-USDT", status=PairScanStatus.VALID, pipeline_result=pipeline_result,
            started_at_utc=UTC_NOW, completed_at_utc=UTC_NOW, duration_ms=1.0,
        )
        cycle_result = ScanCycleResult(
            cycle_id="c1", started_at_utc=UTC_NOW, completed_at_utc=UTC_NOW, duration_ms=1.0,
            configured_pairs=["BTC-USDT"], attempted_pairs=["BTC-USDT"],
            valid_results=[pair_result], rejected_results=[], duplicate_results=[],
            error_results=[], skipped_results=[], pair_results=[pair_result],
            total_pairs=1, valid_count=1, rejected_count=0, duplicate_count=0,
            error_count=0, skipped_count=0,
        )
        await runtime_store.record_cycle_result(cycle_result)
        service = _build_service(runtime_store=runtime_store)

        coins = await service.get_scanning_coins()
        btc = next(c for c in coins if c.coin == "BTC-USDT")

        # validation_progress_percentage (100, from all stages passing) is
        # a completely different value than the final confidence score
        # (96.0), proving one never overwrites or substitutes the other.
        assert btc.score == 96.0
        assert btc.validation_progress_percentage == 100
        assert btc.score != btc.validation_progress_percentage


class TestDirectionResolution:
    async def _coin_for(self, pipeline_result, pair_status=PairScanStatus.REJECTED):
        runtime_store = DashboardRuntimeStore()
        reason = (
            pipeline_result.rejection_reason
            if pipeline_result is not None
            else "Required market data or indicator calculation is unavailable."
        )
        pair_result = PairScanResult(
            symbol="BTC-USDT", status=pair_status, pipeline_result=pipeline_result,
            started_at_utc=UTC_NOW, completed_at_utc=UTC_NOW, duration_ms=1.0,
            reason=reason,
            error_type="PipelineDataUnavailableError" if pipeline_result is None else None,
        )
        cycle_result = ScanCycleResult(
            cycle_id="c1", started_at_utc=UTC_NOW, completed_at_utc=UTC_NOW, duration_ms=1.0,
            configured_pairs=["BTC-USDT"], attempted_pairs=["BTC-USDT"],
            valid_results=[pair_result] if pair_status == PairScanStatus.VALID else [],
            rejected_results=[pair_result] if pair_status == PairScanStatus.REJECTED else [],
            duplicate_results=[],
            error_results=[pair_result] if pair_status == PairScanStatus.ERROR else [],
            skipped_results=[], pair_results=[pair_result],
            total_pairs=1,
            valid_count=1 if pair_status == PairScanStatus.VALID else 0,
            rejected_count=1 if pair_status == PairScanStatus.REJECTED else 0,
            duplicate_count=0,
            error_count=1 if pair_status == PairScanStatus.ERROR else 0,
            skipped_count=0,
        )
        await runtime_store.record_cycle_result(cycle_result)
        service = _build_service(runtime_store=runtime_store)
        coins = await service.get_scanning_coins()
        return next(c for c in coins if c.coin == "BTC-USDT")

    async def test_bullish_htf_bias_maps_to_buy(self):
        confidence_result = ConfidenceScoreResult(
            raw_score=115.0, maximum_raw_score=115, normalized_score=96.0,
            classification=ConfidenceClassification.PREMIUM, publishable=True,
            mandatory_layers_passed=True, layer_scores=[], failed_mandatory_layers=[],
            reason="PREMIUM",
        )
        pipeline_result = StrategyPipelineResult(
            symbol="BTC-USDT", expected_direction="BUY", detection_time_utc=UTC_NOW,
            status=PipelineStatus.VALID, passed=True, stages=[],
            risk_plan=_real_risk_plan(), confidence_result=confidence_result,
        )
        coin = await self._coin_for(pipeline_result, pair_status=PairScanStatus.VALID)
        assert coin.direction == "BUY"

    async def test_bearish_htf_bias_maps_to_sell(self):
        pipeline_result = StrategyPipelineResult(
            symbol="BTC-USDT", expected_direction="SELL", detection_time_utc=UTC_NOW,
            status=PipelineStatus.REJECTED, passed=False,
            failed_layer="LIQUIDITY_SWEEP", rejection_reason="no sweep", stages=[],
        )
        coin = await self._coin_for(pipeline_result)
        assert coin.direction == "SELL"

    async def test_mixed_or_unknown_htf_bias_maps_to_null(self):
        pipeline_result = StrategyPipelineResult(
            symbol="BTC-USDT", expected_direction=None, detection_time_utc=UTC_NOW,
            status=PipelineStatus.REJECTED, passed=False,
            failed_layer="HTF_BIAS", rejection_reason="Higher-timeframe bias is MIXED or UNKNOWN.",
            stages=[],
        )
        coin = await self._coin_for(pipeline_result)
        assert coin.direction is None

    async def test_rejection_before_htf_resolution_maps_to_null(self):
        pipeline_result = StrategyPipelineResult(
            symbol="BTC-USDT", expected_direction=None, detection_time_utc=UTC_NOW,
            status=PipelineStatus.REJECTED, passed=False,
            failed_layer="MARKET_REGIME", rejection_reason="not trending", stages=[],
        )
        coin = await self._coin_for(pipeline_result)
        assert coin.direction is None

    async def test_technical_error_maps_direction_safely_to_null(self):
        # A technical ERROR is raised before any StrategyPipelineResult is
        # built, so PairScanResult.pipeline_result is None.
        coin = await self._coin_for(None, pair_status=PairScanStatus.ERROR)
        assert coin.direction is None
        assert coin.status == "ERROR"


class TestDashboardServiceDoesNotRecalculateStrategy:
    async def test_no_medium_signals_exposed_via_scanning_coins(self):
        # MEDIUM is never a valid PipelineStatus.VALID outcome (only
        # PREMIUM/STRONG publish); confirm scanning-coins never fabricates
        # or exposes a MEDIUM classification anywhere in its response.
        runtime_store = DashboardRuntimeStore()
        service = _build_service(runtime_store=runtime_store)
        coins = await service.get_scanning_coins()
        assert all(getattr(c, "signal_type", None) != "MEDIUM" for c in coins)
        assert all("MEDIUM" not in (c.status or "") for c in coins)

    def test_dashboard_service_never_imports_scoring_engine(self):
        import app.api.dashboard_service as module

        assert not hasattr(module, "ConfidenceScoringEngine")
        assert not hasattr(module, "ConfidenceCalculator")


class TestPairScanUpdatedEvents:
    def _cycle_with(self, pair_result: PairScanResult) -> ScanCycleResult:
        return ScanCycleResult(
            cycle_id="c1", started_at_utc=UTC_NOW, completed_at_utc=UTC_NOW, duration_ms=1.0,
            configured_pairs=[pair_result.symbol], attempted_pairs=[pair_result.symbol],
            valid_results=[], rejected_results=[], duplicate_results=[], error_results=[],
            skipped_results=[], pair_results=[pair_result], total_pairs=1,
            valid_count=0, rejected_count=0, duplicate_count=0, error_count=0, skipped_count=0,
        )

    def test_one_event_per_pair_with_safe_fields_only(self):
        pipeline_result = StrategyPipelineResult(
            symbol="ETH-USDT", expected_direction=None, detection_time_utc=UTC_NOW,
            status=PipelineStatus.REJECTED, passed=False,
            failed_layer="LIQUIDITY_SWEEP", rejection_reason="Institutional liquidity sweep missing",
            stages=_stages_with_failure("LIQUIDITY_SWEEP"),
        )
        pair_result = PairScanResult(
            symbol="ETH-USDT", status=PairScanStatus.REJECTED, pipeline_result=pipeline_result,
            started_at_utc=UTC_NOW, completed_at_utc=UTC_NOW, duration_ms=1.0,
            reason="Institutional liquidity sweep missing",
        )
        events = build_pair_scan_updated_events(self._cycle_with(pair_result))

        assert len(events) == 1
        event = events[0]
        assert event.event.value == "PAIR_SCAN_UPDATED"
        assert event.data == {
            "coin": "ETH-USDT",
            "direction": None,
            "validation_progress_percentage": 35,
            "last_executed_layer": "LIQUIDITY_SWEEP",
            "failed_layer": "LIQUIDITY_SWEEP",
            "reason": "Institutional liquidity sweep missing",
        }

    def test_no_secrets_candles_or_stack_traces_in_event_data(self):
        pipeline_result = StrategyPipelineResult(
            symbol="BTC-USDT", expected_direction="BUY", detection_time_utc=UTC_NOW,
            status=PipelineStatus.VALID, passed=True, stages=_stages("CONFIDENCE_SCORING"),
            risk_plan=_real_risk_plan(),
            confidence_result=ConfidenceScoreResult(
                raw_score=115.0, maximum_raw_score=115, normalized_score=100.0,
                classification=ConfidenceClassification.PREMIUM, publishable=True,
                mandatory_layers_passed=True, layer_scores=[], failed_mandatory_layers=[],
                reason="PREMIUM",
            ),
        )
        pair_result = PairScanResult(
            symbol="BTC-USDT", status=PairScanStatus.VALID, pipeline_result=pipeline_result,
            started_at_utc=UTC_NOW, completed_at_utc=UTC_NOW, duration_ms=1.0,
        )
        events = build_pair_scan_updated_events(self._cycle_with(pair_result))
        payload_text = str(events[0].data)
        assert "Traceback" not in payload_text
        assert "TELEGRAM_BOT_TOKEN" not in payload_text
        assert "candles" not in payload_text.lower()


def _with_status(signal: Signal, dashboard_status: str = DASHBOARD_STATUS_NEW) -> SignalWithStatus:
    return SignalWithStatus(signal=signal, dashboard_status=dashboard_status)


class TestPremiumStrongFiltering:
    async def test_premium_filtering(self):
        repository = MagicMock()
        repository.list_recent_with_status = AsyncMock(
            return_value=[
                _with_status(_signal(trade_id="P1", signal_type=SignalType.PREMIUM, confidence_score=92.0))
            ]
        )
        service = _build_service(signal_repository=repository)

        signals = await service.get_premium_signals()
        assert len(signals) == 1
        assert signals[0].confidence_score >= 90.0

    async def test_premium_excludes_below_90(self):
        repository = MagicMock()
        repository.list_recent_with_status = AsyncMock(
            return_value=[
                _with_status(_signal(trade_id="P1", signal_type=SignalType.PREMIUM, confidence_score=85.0))
            ]
        )
        service = _build_service(signal_repository=repository)

        signals = await service.get_premium_signals()
        assert signals == []

    async def test_strong_filtering(self):
        repository = MagicMock()
        repository.list_recent_with_status = AsyncMock(
            return_value=[
                _with_status(_signal(trade_id="S1", signal_type=SignalType.STRONG, confidence_score=85.0))
            ]
        )
        service = _build_service(signal_repository=repository)

        signals = await service.get_strong_signals()
        assert len(signals) == 1
        assert 80.0 <= signals[0].confidence_score < 90.0

    async def test_strong_excludes_at_or_above_90(self):
        repository = MagicMock()
        repository.list_recent_with_status = AsyncMock(
            return_value=[
                _with_status(_signal(trade_id="S1", signal_type=SignalType.STRONG, confidence_score=90.0))
            ]
        )
        service = _build_service(signal_repository=repository)

        signals = await service.get_strong_signals()
        assert signals == []

    async def test_medium_never_exposed_via_premium_or_strong(self):
        repository = MagicMock()
        repository.list_recent_with_status = AsyncMock(return_value=[])
        service = _build_service(signal_repository=repository)

        premium = await service.get_premium_signals()
        strong = await service.get_strong_signals()
        assert premium == []
        assert strong == []
        # Confirm the repository was only ever queried for PREMIUM/STRONG,
        # never for MEDIUM/IGNORE.
        queried_types = {
            call.kwargs.get("signal_type") for call in repository.list_recent_with_status.call_args_list
        }
        assert queried_types <= {SignalType.PREMIUM.value, SignalType.STRONG.value}

    async def test_active_signal_excluded_from_premium_and_strong(self):
        # Once a signal is activated via the dashboard "Trade" action, it
        # must no longer appear in Premium/Strong -- get_premium_signals/
        # get_strong_signals only ever request dashboard_status=NEW.
        repository = MagicMock()
        repository.list_recent_with_status = AsyncMock(return_value=[])
        service = _build_service(signal_repository=repository)

        await service.get_premium_signals()
        await service.get_strong_signals()

        for call in repository.list_recent_with_status.call_args_list:
            assert call.kwargs.get("dashboard_status") == DASHBOARD_STATUS_NEW


class TestRejectedErrorNeverValid:
    async def test_rejected_and_error_signals_never_exposed_as_valid(self):
        # DashboardService only ever calls list_recent_with_status with
        # PREMIUM/STRONG signal_type filters for premium/strong/active
        # signal endpoints; REJECTED/ERROR pipeline outcomes are never
        # persisted as Signal rows at all (enforced upstream by
        # SignalStorageService), so there is no code path by which they
        # could appear here.
        repository = MagicMock()
        repository.list_recent_with_status = AsyncMock(return_value=[])
        service = _build_service(signal_repository=repository)

        active = await service.get_active_signals()
        premium = await service.get_premium_signals()
        strong = await service.get_strong_signals()
        assert active == []
        assert premium == []
        assert strong == []


class TestActiveSignals:
    async def test_current_price_unavailable_returns_none(self):
        repository = MagicMock()
        repository.list_recent_with_status = AsyncMock(
            side_effect=lambda limit, signal_type=None, symbol=None, dashboard_status=None: (
                [_with_status(_signal(trade_id="P1"), DASHBOARD_STATUS_ACTIVE)]
                if signal_type == SignalType.PREMIUM.value
                else []
            )
        )
        market_data_provider = MagicMock()
        market_data_provider.fetch_ticker_price = AsyncMock(return_value=None)
        service = _build_service(signal_repository=repository, market_data_provider=market_data_provider)

        signals = await service.get_active_signals()
        assert signals[0].current_price is None
        assert signals[0].distance_to_take_profit_percentage is None

    async def test_distance_to_tp_calculation_buy(self):
        repository = MagicMock()
        repository.list_recent_with_status = AsyncMock(
            side_effect=lambda limit, signal_type=None, symbol=None, dashboard_status=None: (
                [_with_status(_signal(trade_id="P1", direction=Direction.BUY), DASHBOARD_STATUS_ACTIVE)]
                if signal_type == SignalType.PREMIUM.value
                else []
            )
        )
        market_data_provider = MagicMock()
        market_data_provider.fetch_ticker_price = AsyncMock(return_value=100.0)
        service = _build_service(signal_repository=repository, market_data_provider=market_data_provider)

        signals = await service.get_active_signals()
        # take_profit=110, current_price=100 -> (110-100)/100*100 = 10%
        assert signals[0].distance_to_take_profit_percentage == pytest.approx(10.0)

    async def test_distance_to_tp_calculation_sell(self):
        repository = MagicMock()
        repository.list_recent_with_status = AsyncMock(
            side_effect=lambda limit, signal_type=None, symbol=None, dashboard_status=None: (
                [_with_status(_signal(trade_id="S1", direction=Direction.SELL), DASHBOARD_STATUS_ACTIVE)]
                if signal_type == SignalType.PREMIUM.value
                else []
            )
        )
        market_data_provider = MagicMock()
        market_data_provider.fetch_ticker_price = AsyncMock(return_value=100.0)
        service = _build_service(signal_repository=repository, market_data_provider=market_data_provider)

        signals = await service.get_active_signals()
        # take_profit=90, current_price=100 -> (100-90)/100*100 = 10%
        assert signals[0].distance_to_take_profit_percentage == pytest.approx(10.0)

    async def test_active_signals_only_query_active_status(self):
        repository = MagicMock()
        repository.list_recent_with_status = AsyncMock(return_value=[])
        service = _build_service(signal_repository=repository)

        await service.get_active_signals()

        for call in repository.list_recent_with_status.call_args_list:
            assert call.kwargs.get("dashboard_status") == DASHBOARD_STATUS_ACTIVE


class TestSignalDetails:
    async def test_signal_details_endpoint(self):
        repository = MagicMock()
        repository.get_by_trade_id_with_status = AsyncMock(
            return_value=_with_status(_signal(trade_id="P1"))
        )
        service = _build_service(signal_repository=repository)

        details = await service.get_signal_details("P1")
        assert details is not None
        assert details.trade_id == "P1"
        assert details.institutional_reason == "Confirmed setup facts only."
        assert details.dashboard_status == DASHBOARD_STATUS_NEW

    async def test_signal_details_not_found(self):
        repository = MagicMock()
        repository.get_by_trade_id_with_status = AsyncMock(return_value=None)
        service = _build_service(signal_repository=repository)

        details = await service.get_signal_details("does-not-exist")
        assert details is None


class TestActivateSignal:
    async def test_activates_and_returns_active_signal(self):
        signal = _signal(trade_id="P1", signal_type=SignalType.PREMIUM)
        repository = MagicMock()
        repository.mark_active = AsyncMock(return_value=_with_status(signal, DASHBOARD_STATUS_ACTIVE))
        market_data_provider = MagicMock()
        market_data_provider.fetch_ticker_price = AsyncMock(return_value=None)
        service = _build_service(signal_repository=repository, market_data_provider=market_data_provider)

        result = await service.activate_signal("P1")

        assert result is not None
        assert result.trade_id == "P1"
        assert result.dashboard_status == DASHBOARD_STATUS_ACTIVE
        repository.mark_active.assert_awaited_once_with("P1")

    async def test_returns_none_when_signal_not_found(self):
        repository = MagicMock()
        repository.mark_active = AsyncMock(side_effect=SignalNotFoundError("not found"))
        service = _build_service(signal_repository=repository)

        result = await service.activate_signal("does-not-exist")
        assert result is None

    async def test_activation_preserves_every_signal_value_exactly(self):
        signal = _signal(
            trade_id="P1",
            signal_type=SignalType.PREMIUM,
            confidence_score=93.5,
            direction=Direction.BUY,
        )
        repository = MagicMock()
        repository.mark_active = AsyncMock(return_value=_with_status(signal, DASHBOARD_STATUS_ACTIVE))
        market_data_provider = MagicMock()
        market_data_provider.fetch_ticker_price = AsyncMock(return_value=None)
        service = _build_service(signal_repository=repository, market_data_provider=market_data_provider)

        result = await service.activate_signal("P1")

        assert result.coin == signal.coin
        assert result.direction == signal.direction.value
        assert result.entry_price == signal.entry_price
        assert result.take_profit == signal.take_profit
        assert result.stop_loss == signal.stop_loss
        assert result.confidence_score == signal.confidence_score
        assert result.signal_type == signal.signal_type.value
        assert result.detection_time_utc == signal.detection_time_utc

    async def test_activation_never_calls_exchange_order_methods(self):
        # Purely a UI state transition: the market_data_provider is only
        # ever used for a read-only ticker-price lookup (for display),
        # never any order-placing method.
        signal = _signal(trade_id="P1")
        repository = MagicMock()
        repository.mark_active = AsyncMock(return_value=_with_status(signal, DASHBOARD_STATUS_ACTIVE))
        market_data_provider = MagicMock(spec=["fetch_ticker_price"])
        market_data_provider.fetch_ticker_price = AsyncMock(return_value=None)
        service = _build_service(signal_repository=repository, market_data_provider=market_data_provider)

        await service.activate_signal("P1")  # must not raise / must not call anything beyond the spec


class TestRecentRejections:
    async def test_recent_rejections_returned(self):
        analytics_repository = MagicMock()
        analytics_repository.list_recent = AsyncMock(
            return_value=[
                RejectionRecord(
                    symbol="BTC-USDT",
                    failed_layer="MARKET_REGIME",
                    rejection_reason="not trending",
                    detection_time_utc=UTC_NOW,
                    created_at_utc=UTC_NOW,
                )
            ]
        )
        service = _build_service(analytics_repository=analytics_repository)

        rejections = await service.get_recent_rejections()
        assert len(rejections) == 1
        assert rejections[0].coin == "BTC-USDT"

    async def test_no_analytics_repository_returns_empty(self):
        service = _build_service(analytics_repository=None)
        rejections = await service.get_recent_rejections()
        assert rejections == []


class TestHealth:
    async def test_health_reports_database_reachable(self):
        repository = MagicMock()
        repository.count = AsyncMock(return_value=0)
        service = _build_service(signal_repository=repository, telegram_enabled=True, websocket_enabled=True)

        health = await service.get_health()
        assert health.database_reachable is True
        assert health.telegram_enabled is True
        assert health.websocket_enabled is True

    async def test_health_reports_database_unreachable(self):
        repository = MagicMock()
        repository.count = AsyncMock(side_effect=RuntimeError("db down"))
        service = _build_service(signal_repository=repository)

        health = await service.get_health()
        assert health.database_reachable is False
