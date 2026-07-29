"""
Tests for the analyze / scan-once / scan CLI modes in main.py.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main
from app.risk.results import (
    CorrelationResult,
    CorrelationStatus,
    PositionRiskResult,
    RiskPlan,
    RiskPlanStatus,
    StopLossResult,
    TakeProfitResult,
)
from app.scanner.pipeline_results import PipelineStatus, StrategyPipelineResult
from app.scanner.scan_results import PairScanResult, PairScanStatus, ScanCycleResult, ScannerRuntimeStatus
from app.scoring.results import ConfidenceClassification, ConfidenceScoreResult

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _real_risk_plan() -> RiskPlan:
    stop_loss_result = StopLossResult(
        direction="BUY",
        entry_price=100.0,
        selected_stop_loss=95.0,
        candidates=[],
        valid=True,
        reason="valid",
    )
    take_profit_result = TakeProfitResult(
        direction="BUY",
        entry_price=100.0,
        stop_loss=95.0,
        selected_take_profit=110.0,
        risk_reward_ratio=2.0,
        candidates=[],
        valid=True,
        reason="valid",
    )
    position_risk = PositionRiskResult(
        account_balance=10000.0,
        risk_percentage=1.0,
        entry_price=100.0,
        stop_loss=95.0,
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
        entry_price=100.0,
        stop_loss_result=stop_loss_result,
        take_profit_result=take_profit_result,
        position_risk=position_risk,
        correlation_result=correlation_result,
        active_trade_count=0,
        maximum_active_trades=5,
        risk_reward_ratio=2.0,
        status=RiskPlanStatus.VALID,
        valid=True,
        reason="valid",
    )


def _valid_pipeline_result(symbol="BTC-USDT"):
    risk_plan = _real_risk_plan()
    confidence = ConfidenceScoreResult(
        raw_score=115.0,
        maximum_raw_score=120,
        normalized_score=95.8,
        classification=ConfidenceClassification.PREMIUM,
        publishable=True,
        mandatory_layers_passed=True,
        layer_scores=[],
        failed_mandatory_layers=[],
        reason="PREMIUM_CONFIDENCE",
    )
    return StrategyPipelineResult(
        symbol=symbol,
        expected_direction="BUY",
        detection_time_utc=UTC_NOW,
        status=PipelineStatus.VALID,
        passed=True,
        stages=[],
        risk_plan=risk_plan,
        confidence_result=confidence,
    )


def _pair_result(symbol="BTC-USDT", status=PairScanStatus.VALID):
    kwargs = dict(
        symbol=symbol,
        status=status,
        pipeline_result=None,
        duplicate_key=None,
        duplicate=False,
        started_at_utc=UTC_NOW,
        completed_at_utc=UTC_NOW,
        duration_ms=1.0,
        reason=None,
        error_type=None,
    )
    if status == PairScanStatus.VALID:
        kwargs["pipeline_result"] = _valid_pipeline_result(symbol)
    elif status == PairScanStatus.REJECTED:
        kwargs["reason"] = "not trending"
    return PairScanResult(**kwargs)


def _cycle_result(pair_results):
    valid = [r for r in pair_results if r.status == PairScanStatus.VALID]
    rejected = [r for r in pair_results if r.status == PairScanStatus.REJECTED]
    duplicate = [r for r in pair_results if r.status == PairScanStatus.DUPLICATE]
    error = [r for r in pair_results if r.status == PairScanStatus.ERROR]
    skipped = [r for r in pair_results if r.status == PairScanStatus.SKIPPED]
    symbols = [r.symbol for r in pair_results]
    return ScanCycleResult(
        cycle_id="cycle-1",
        started_at_utc=UTC_NOW,
        completed_at_utc=UTC_NOW,
        duration_ms=42.0,
        configured_pairs=symbols,
        attempted_pairs=symbols,
        valid_results=valid,
        rejected_results=rejected,
        duplicate_results=duplicate,
        error_results=error,
        skipped_results=skipped,
        pair_results=pair_results,
        total_pairs=len(symbols),
        valid_count=len(valid),
        rejected_count=len(rejected),
        duplicate_count=len(duplicate),
        error_count=len(error),
        skipped_count=len(skipped),
    )


class TestAnalyzeModePreserved:
    async def test_analyze_mode_still_works(self, capsys):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_pipeline_result())
        with patch("main.build_strategy_engine", return_value=engine):
            await main._run_manual_analysis("BTC-USDT", 10000.0)
        output = capsys.readouterr().out
        assert "symbol=BTC-USDT" in output
        assert "status=VALID" in output


class TestScanOnceMode:
    async def test_scan_once_runs_one_cycle_only(self, capsys):
        service = MagicMock()
        service.signal_storage_service = None
        service.notification_service = None
        service.run_single_cycle = AsyncMock(
            return_value=_cycle_result([_pair_result(status=PairScanStatus.VALID)])
        )
        with patch("main.build_scanner_service", return_value=service):
            await main._run_scan_once(10000.0)
        service.run_single_cycle.assert_called_once()

    async def test_concise_cycle_summary(self, capsys):
        service = MagicMock()
        service.signal_storage_service = None
        service.notification_service = None
        service.run_single_cycle = AsyncMock(
            return_value=_cycle_result(
                [
                    _pair_result(symbol="BTC-USDT", status=PairScanStatus.VALID),
                    _pair_result(symbol="ETH-USDT", status=PairScanStatus.REJECTED),
                ]
            )
        )
        with patch("main.build_scanner_service", return_value=service):
            await main._run_scan_once(10000.0)
        output = capsys.readouterr().out
        assert "cycle_id=cycle-1" in output
        assert "total_pairs=2" in output
        assert "valid=1" in output
        assert "rejected=1" in output
        assert "duplicates=0" in output
        assert "errors=0" in output
        assert "duration_ms=42.0" in output

    async def test_valid_result_details_shown(self, capsys):
        service = MagicMock()
        service.signal_storage_service = None
        service.notification_service = None
        service.run_single_cycle = AsyncMock(
            return_value=_cycle_result([_pair_result(symbol="BTC-USDT", status=PairScanStatus.VALID)])
        )
        with patch("main.build_scanner_service", return_value=service):
            await main._run_scan_once(10000.0)
        output = capsys.readouterr().out
        assert "valid: symbol=BTC-USDT" in output
        assert "direction=BUY" in output
        assert "classification=PREMIUM" in output

    async def test_no_full_candle_payload(self, capsys):
        service = MagicMock()
        service.signal_storage_service = None
        service.notification_service = None
        service.run_single_cycle = AsyncMock(
            return_value=_cycle_result([_pair_result(status=PairScanStatus.VALID)])
        )
        with patch("main.build_scanner_service", return_value=service):
            await main._run_scan_once(10000.0)
        output = capsys.readouterr().out
        assert "candles_by_timeframe" not in output
        assert "OHLCV" not in output

    async def test_no_secrets_printed(self, capsys):
        service = MagicMock()
        service.signal_storage_service = None
        service.notification_service = None
        service.run_single_cycle = AsyncMock(
            return_value=_cycle_result([_pair_result(status=PairScanStatus.VALID)])
        )
        with patch("main.build_scanner_service", return_value=service):
            await main._run_scan_once(10000.0)
        output = capsys.readouterr().out
        assert "api_key" not in output.lower()
        assert "secret" not in output.lower()

    async def test_no_telegram_or_database_action(self, capsys):
        service = MagicMock()
        service.signal_storage_service = None
        service.notification_service = None
        service.run_single_cycle = AsyncMock(
            return_value=_cycle_result([_pair_result(status=PairScanStatus.VALID)])
        )
        with patch("main.build_scanner_service", return_value=service):
            await main._run_scan_once(10000.0)
        # No Telegram/database attribute should ever be touched on the service mock
        # beyond run_single_cycle.
        called_attrs = {call[0] for call in service.method_calls}
        assert not any("telegram" in attr.lower() for attr in called_attrs)
        assert not any("database" in attr.lower() or "persist" in attr.lower() for attr in called_attrs)


class TestScanForeverMode:
    async def test_scan_mode_starts_recurring_service(self):
        service = MagicMock()
        service.signal_storage_service = None
        service.notification_service = None
        service.run_forever = AsyncMock()
        service.request_shutdown = MagicMock()
        service.get_runtime_status = MagicMock(
            return_value=ScannerRuntimeStatus(
                running=False,
                shutdown_requested=True,
                cycles_completed=2,
                last_cycle_id="cycle-2",
            )
        )
        with patch("main.build_scanner_service", return_value=service):
            await main._run_scan_forever(10000.0)
        service.run_forever.assert_called_once_with(10000.0)

    async def test_ctrl_c_requests_graceful_shutdown(self):
        service = MagicMock()
        service.signal_storage_service = None
        service.notification_service = None
        service.run_forever = AsyncMock(side_effect=KeyboardInterrupt())
        service.request_shutdown = MagicMock()
        service.get_runtime_status = MagicMock(
            return_value=ScannerRuntimeStatus(
                running=False,
                shutdown_requested=True,
                cycles_completed=1,
                last_cycle_id="cycle-1",
            )
        )
        with patch("main.build_scanner_service", return_value=service):
            await main._run_scan_forever(10000.0)
        service.request_shutdown.assert_called_once()

    async def test_final_scanner_status_printed(self, capsys):
        service = MagicMock()
        service.signal_storage_service = None
        service.notification_service = None
        service.run_forever = AsyncMock()
        service.request_shutdown = MagicMock()
        service.get_runtime_status = MagicMock(
            return_value=ScannerRuntimeStatus(
                running=False,
                shutdown_requested=True,
                cycles_completed=5,
                last_cycle_id="cycle-5",
            )
        )
        with patch("main.build_scanner_service", return_value=service):
            await main._run_scan_forever(10000.0)
        output = capsys.readouterr().out
        assert "cycles_completed=5" in output
        assert "last_cycle_id=cycle-5" in output

    async def test_no_full_candle_data_or_secrets_in_scan_output(self, capsys):
        service = MagicMock()
        service.signal_storage_service = None
        service.notification_service = None
        service.run_forever = AsyncMock()
        service.request_shutdown = MagicMock()
        service.get_runtime_status = MagicMock(
            return_value=ScannerRuntimeStatus(
                running=False, shutdown_requested=True, cycles_completed=1, last_cycle_id="c"
            )
        )
        with patch("main.build_scanner_service", return_value=service):
            await main._run_scan_forever(10000.0)
        output = capsys.readouterr().out
        assert "api_key" not in output.lower()
        assert "candles_by_timeframe" not in output


class TestModeParsing:
    def test_default_mode_is_analyze(self):
        mode, remaining = main._parse_mode_and_args(["main.py", "BTC-USDT"])
        assert mode == "analyze"
        assert remaining == ["main.py", "BTC-USDT"]

    def test_scan_once_mode_parsed(self):
        mode, remaining = main._parse_mode_and_args(["main.py", "scan-once"])
        assert mode == "scan-once"

    def test_scan_mode_parsed(self):
        mode, remaining = main._parse_mode_and_args(["main.py", "scan"])
        assert mode == "scan"

    def test_analyze_mode_explicit(self):
        mode, remaining = main._parse_mode_and_args(["main.py", "analyze", "ETH-USDT"])
        assert mode == "analyze"
        assert remaining == ["main.py", "ETH-USDT"]
