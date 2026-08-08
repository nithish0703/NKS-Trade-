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
    return StrategyPipelineResult(
        symbol=symbol,
        expected_direction="BUY",
        detection_time_utc=UTC_NOW,
        status=PipelineStatus.VALID,
        passed=True,
        stages=[],
        risk_plan=risk_plan,
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

    async def test_on_cycle_result_wired_and_prints_each_cycle(self, capsys):
        """
        The bug this fix closes: run_forever()'s recurring cycles were
        never observable because build_scanner_service() was never
        given an on_cycle_result callback in the CLI scan path (only
        scan-once printed its result, directly in its own handler).
        """
        service = MagicMock()
        service.signal_storage_service = None
        service.notification_service = None
        service.run_forever = AsyncMock()
        service.request_shutdown = MagicMock()
        service.get_runtime_status = MagicMock(
            return_value=ScannerRuntimeStatus(
                running=False, shutdown_requested=True, cycles_completed=1, last_cycle_id="cycle-1"
            )
        )
        captured_kwargs = {}

        def _capture_build_scanner_service(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return service

        with patch("main.build_scanner_service", side_effect=_capture_build_scanner_service):
            await main._run_scan_forever(10000.0)

        assert "on_cycle_result" in captured_kwargs
        on_cycle_result = captured_kwargs["on_cycle_result"]

        # Invoke it directly, as ScannerService would for each completed
        # cycle, and confirm it prints the same summary scan-once prints.
        await on_cycle_result(
            _cycle_result(
                [
                    _pair_result(symbol="BTC-USDT", status=PairScanStatus.VALID),
                    _pair_result(symbol="ETH-USDT", status=PairScanStatus.REJECTED),
                ]
            )
        )
        output = capsys.readouterr().out
        assert "cycle_id=cycle-1" in output
        assert "total_pairs=2" in output
        assert "valid=1" in output
        assert "rejected=1" in output

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


class TestSignalOutcomeMonitorWiring:
    def test_no_monitor_when_persistence_disabled(self):
        service = MagicMock()
        service.signal_storage_service = None

        assert main._build_signal_outcome_monitor(service) is None

    def test_no_monitor_when_setting_disabled(self):
        service = MagicMock()
        service.signal_storage_service = MagicMock()
        settings = MagicMock()
        settings.signal_outcome_monitor_enabled = False

        with patch("app.config.settings.get_settings", return_value=settings):
            assert main._build_signal_outcome_monitor(service) is None

    def test_monitor_built_with_lease_guard_when_enabled(self):
        service = MagicMock()
        signal_storage_service = MagicMock()
        signal_storage_service.database_manager = MagicMock()
        signal_storage_service.signal_repository = MagicMock()
        service.signal_storage_service = signal_storage_service
        settings = MagicMock()
        settings.signal_outcome_monitor_enabled = True
        settings.signal_outcome_monitor_interval_seconds = 60
        settings.exchange_base_url = "https://fapi.binance.com"
        settings.request_timeout_seconds = 10

        with patch("app.config.settings.get_settings", return_value=settings), patch(
            "app.data.binance_market_data_provider.BinanceFuturesMarketDataProvider"
        ):
            monitor = main._build_signal_outcome_monitor(service)

        assert monitor is not None

    async def test_monitor_started_and_shut_down_alongside_scanner(self):
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

        monitor = MagicMock()
        monitor.run_forever = AsyncMock()
        monitor.request_shutdown = MagicMock()

        with patch("main.build_scanner_service", return_value=service), patch(
            "main._build_signal_outcome_monitor", return_value=monitor
        ):
            await main._run_scan_forever(10000.0)

        monitor.request_shutdown.assert_called_once()

    async def test_no_monitor_task_when_none_built(self):
        # Confirms _run_scan_forever tolerates _build_signal_outcome_monitor
        # returning None (persistence disabled / monitor disabled) without
        # attempting to start or shut down anything monitor-related.
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

        with patch("main.build_scanner_service", return_value=service), patch(
            "main._build_signal_outcome_monitor", return_value=None
        ):
            await main._run_scan_forever(10000.0)  # must not raise


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

    def test_scan_args_flag_absent_defaults_false(self):
        balance, force_release = main._parse_scan_args(["main.py"])
        assert force_release is False

    def test_scan_args_flag_present_parsed_true(self):
        balance, force_release = main._parse_scan_args(["main.py", "--force-release-lease"])
        assert force_release is True

    def test_scan_args_flag_does_not_become_the_balance(self):
        balance, force_release = main._parse_scan_args(["main.py", "--force-release-lease"])
        assert balance == main._DEFAULT_DEVELOPMENT_BALANCE

    def test_scan_args_flag_parses_regardless_of_position(self):
        # _parse_args' positional-balance handling for scan/scan-once
        # (a single-positional-arg mode reusing the two-positional
        # analyze-mode parser) is pre-existing, unrelated behaviour --
        # this test only asserts the flag itself parses correctly
        # whether it appears before or interleaved with other args.
        _, force_release_leading = main._parse_scan_args(["main.py", "--force-release-lease", "5000"])
        _, force_release_trailing = main._parse_scan_args(["main.py", "5000", "--force-release-lease"])
        assert force_release_leading is True
        assert force_release_trailing is True


class TestForceReleaseLease:
    """
    Covers the `--force-release-lease` escape hatch: an operator-facing
    way to clear a stuck lease without asking for help, on top of the
    stable-holder-id fix that makes restarts reclaim their own lease
    automatically in the common case.
    """

    async def test_disabled_persistence_is_a_noop(self, capsys):
        settings = MagicMock()
        settings.enable_signal_persistence = False

        with patch("app.config.settings.get_settings", return_value=settings):
            await main._force_release_scan_leases()

        output = capsys.readouterr().out
        assert "persistence is disabled" in output.lower()

    async def test_releases_both_lease_names(self, capsys):
        settings = MagicMock()
        settings.enable_signal_persistence = True
        settings.database_url = "sqlite+aiosqlite:///:memory:"

        database_manager = MagicMock()
        database_manager.initialize = AsyncMock()
        database_manager.dispose = AsyncMock()

        release_calls = []

        async def _fake_release_lease(db, *, lease_name):
            release_calls.append(lease_name)
            return True

        with patch("app.config.settings.get_settings", return_value=settings), patch(
            "main.DatabaseManager", return_value=database_manager
        ), patch("app.storage.monitor_lease.release_lease", side_effect=_fake_release_lease):
            await main._force_release_scan_leases()

        assert release_calls == ["scanner_cycle", "signal_outcome_monitor"]
        database_manager.dispose.assert_awaited_once()

    async def test_run_scan_forever_calls_release_when_flag_set(self):
        service = MagicMock()
        service.signal_storage_service = None
        service.notification_service = None
        service.run_forever = AsyncMock()
        service.request_shutdown = MagicMock()
        service.get_runtime_status = MagicMock(
            return_value=ScannerRuntimeStatus(
                running=False, shutdown_requested=True, cycles_completed=0, last_cycle_id=None
            )
        )
        release_mock = AsyncMock()

        with patch("main.build_scanner_service", return_value=service), patch(
            "main._force_release_scan_leases", release_mock
        ):
            await main._run_scan_forever(10000.0, force_release_lease=True)

        release_mock.assert_awaited_once()

    async def test_run_scan_forever_skips_release_by_default(self):
        service = MagicMock()
        service.signal_storage_service = None
        service.notification_service = None
        service.run_forever = AsyncMock()
        service.request_shutdown = MagicMock()
        service.get_runtime_status = MagicMock(
            return_value=ScannerRuntimeStatus(
                running=False, shutdown_requested=True, cycles_completed=0, last_cycle_id=None
            )
        )
        release_mock = AsyncMock()

        with patch("main.build_scanner_service", return_value=service), patch(
            "main._force_release_scan_leases", release_mock
        ):
            await main._run_scan_forever(10000.0)

        release_mock.assert_not_awaited()
