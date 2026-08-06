"""
Tests for app.scanner.scanner_service.ScannerService.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.risk.results import RiskPlan, RiskPlanStatus
from app.scanner.active_state import ActiveTradingState
from app.scanner.pipeline_results import PipelineStatus, StrategyPipelineResult
from app.scanner.scan_results import PairScanResult, PairScanStatus, ScanCycleResult
from app.scanner.scanner_service import ScannerService

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _valid_pipeline_result(symbol="BTC-USDT"):
    risk_plan = MagicMock(spec=RiskPlan)
    risk_plan.status = RiskPlanStatus.VALID
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
    elif status == PairScanStatus.DUPLICATE:
        kwargs["pipeline_result"] = _valid_pipeline_result(symbol)
        kwargs["duplicate"] = True
        kwargs["duplicate_key"] = f"dup-{symbol}"
        kwargs["reason"] = "Duplicate institutional setup suppressed."
    elif status == PairScanStatus.ERROR:
        kwargs["reason"] = "boom"
        kwargs["error_type"] = "RuntimeError"
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
        duration_ms=1.0,
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


def _active_state():
    return ActiveTradingState(
        active_trade_count=0,
        active_positions=[],
        active_position_candles={},
        captured_at_utc=UTC_NOW,
    )


def _build_service(
    scheduler=None,
    candidate_buffer=None,
    active_state_provider=None,
    signal_storage_service=None,
    storage_failure_is_fatal=False,
    notification_service=None,
    notification_failure_is_fatal=False,
    logger=None,
):
    return ScannerService(
        scheduler=scheduler or MagicMock(),
        candidate_buffer=candidate_buffer or MagicMock(add=AsyncMock()),
        active_state_provider=active_state_provider or MagicMock(get_state=AsyncMock(return_value=_active_state())),
        signal_storage_service=signal_storage_service,
        storage_failure_is_fatal=storage_failure_is_fatal,
        notification_service=notification_service,
        notification_failure_is_fatal=notification_failure_is_fatal,
        logger=logger,
    )


class TestRunSingleCycle:
    async def test_single_cycle_retrieves_active_state(self):
        active_state_provider = MagicMock(get_state=AsyncMock(return_value=_active_state()))
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))
        service = _build_service(scheduler=scheduler, active_state_provider=active_state_provider)

        await service.run_single_cycle(10000.0)

        active_state_provider.get_state.assert_called_once()

    async def test_scheduler_receives_fresh_state(self):
        active_state = ActiveTradingState(
            active_trade_count=2,
            active_positions=["pos-1"],
            active_position_candles={},
            captured_at_utc=UTC_NOW,
        )
        active_state_provider = MagicMock(get_state=AsyncMock(return_value=active_state))
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))
        service = _build_service(scheduler=scheduler, active_state_provider=active_state_provider)

        await service.run_single_cycle(10000.0)

        _, kwargs = scheduler.run_cycle.call_args
        assert kwargs["active_trade_count"] == 2
        assert kwargs["active_positions"] == ["pos-1"]

    async def test_valid_results_added_to_buffer(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(
            return_value=_cycle_result([_pair_result(symbol="BTC-USDT", status=PairScanStatus.VALID)])
        )
        candidate_buffer = MagicMock(add=AsyncMock())
        service = _build_service(scheduler=scheduler, candidate_buffer=candidate_buffer)

        await service.run_single_cycle(10000.0)

        candidate_buffer.add.assert_called_once()

    async def test_rejected_results_not_added(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(
            return_value=_cycle_result(
                [_pair_result(symbol="ETH-USDT", status=PairScanStatus.REJECTED)]
            )
        )
        candidate_buffer = MagicMock(add=AsyncMock())
        service = _build_service(scheduler=scheduler, candidate_buffer=candidate_buffer)

        await service.run_single_cycle(10000.0)

        candidate_buffer.add.assert_not_called()

    async def test_duplicates_not_added(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(
            return_value=_cycle_result(
                [_pair_result(symbol="SOL-USDT", status=PairScanStatus.DUPLICATE)]
            )
        )
        candidate_buffer = MagicMock(add=AsyncMock())
        service = _build_service(scheduler=scheduler, candidate_buffer=candidate_buffer)

        await service.run_single_cycle(10000.0)

        candidate_buffer.add.assert_not_called()

    async def test_errors_not_added(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(
            return_value=_cycle_result([_pair_result(symbol="XRP-USDT", status=PairScanStatus.ERROR)])
        )
        candidate_buffer = MagicMock(add=AsyncMock())
        service = _build_service(scheduler=scheduler, candidate_buffer=candidate_buffer)

        await service.run_single_cycle(10000.0)

        candidate_buffer.add.assert_not_called()

    async def test_no_publishing_or_persistence(self):
        scheduler = MagicMock()
        cycle_result = _cycle_result([_pair_result()])
        scheduler.run_cycle = AsyncMock(return_value=cycle_result)
        service = _build_service(scheduler=scheduler)

        result = await service.run_single_cycle(10000.0)

        assert result is cycle_result
        result_fields = set(type(result).model_fields.keys())
        forbidden = {"telegram_status", "dashboard_status", "persisted"}
        assert result_fields.isdisjoint(forbidden)


class TestRunForever:
    async def test_run_forever_executes_repeated_cycles(self):
        scheduler = MagicMock()

        async def _fake_run_forever(*, account_balance, active_state_provider, on_cycle_complete=None):
            for _ in range(3):
                await active_state_provider()
                if on_cycle_complete is not None:
                    await on_cycle_complete(_cycle_result([_pair_result()]))

        scheduler.run_forever = AsyncMock(side_effect=_fake_run_forever)
        candidate_buffer = MagicMock(add=AsyncMock())
        service = _build_service(scheduler=scheduler, candidate_buffer=candidate_buffer)

        await service.run_forever(10000.0)

        assert candidate_buffer.add.await_count == 3

    async def test_shutdown_propagated(self):
        scheduler = MagicMock()
        service = _build_service(scheduler=scheduler)
        service.request_shutdown()
        scheduler.request_shutdown.assert_called_once()

    async def test_unexpected_service_error_not_suppressed(self):
        scheduler = MagicMock()
        scheduler.run_forever = AsyncMock(side_effect=RuntimeError("boom"))
        service = _build_service(scheduler=scheduler)

        with pytest.raises(RuntimeError):
            await service.run_forever(10000.0)


class TestSignalPersistence:
    async def test_persistence_called_after_completed_cycle_when_enabled(self):
        scheduler = MagicMock()
        cycle_result = _cycle_result([_pair_result()])
        scheduler.run_cycle = AsyncMock(return_value=cycle_result)
        signal_storage_service = MagicMock(process_cycle=AsyncMock())
        service = _build_service(scheduler=scheduler, signal_storage_service=signal_storage_service)

        await service.run_single_cycle(10000.0)

        signal_storage_service.process_cycle.assert_awaited_once_with(cycle_result)

    async def test_persistence_not_called_when_disabled(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))
        service = _build_service(scheduler=scheduler, signal_storage_service=None)

        # No signal_storage_service configured; run_single_cycle must not raise
        # or attempt to call anything persistence-related.
        result = await service.run_single_cycle(10000.0)
        assert result is not None

    async def test_persistence_failure_logged(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))
        signal_storage_service = MagicMock(process_cycle=AsyncMock(side_effect=RuntimeError("disk full")))
        logger = MagicMock()
        service = ScannerService(
            scheduler=scheduler,
            candidate_buffer=MagicMock(add=AsyncMock()),
            active_state_provider=MagicMock(get_state=AsyncMock(return_value=_active_state())),
            signal_storage_service=signal_storage_service,
            storage_failure_is_fatal=False,
            logger=logger,
        )

        await service.run_single_cycle(10000.0)

        logger.error.assert_called_once()

    async def test_non_fatal_storage_failure_does_not_stop_scanner(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))
        signal_storage_service = MagicMock(process_cycle=AsyncMock(side_effect=RuntimeError("disk full")))
        service = _build_service(
            scheduler=scheduler,
            signal_storage_service=signal_storage_service,
            storage_failure_is_fatal=False,
        )

        # Must not raise.
        result = await service.run_single_cycle(10000.0)
        assert result is not None

    async def test_fatal_storage_failure_propagates_when_configured(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))
        signal_storage_service = MagicMock(process_cycle=AsyncMock(side_effect=RuntimeError("disk full")))
        service = _build_service(
            scheduler=scheduler,
            signal_storage_service=signal_storage_service,
            storage_failure_is_fatal=True,
        )

        with pytest.raises(RuntimeError):
            await service.run_single_cycle(10000.0)

    async def test_candidate_buffer_behaviour_unchanged_with_persistence_enabled(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(
            return_value=_cycle_result([_pair_result(status=PairScanStatus.VALID)])
        )
        candidate_buffer = MagicMock(add=AsyncMock())
        signal_storage_service = MagicMock(process_cycle=AsyncMock())
        service = _build_service(
            scheduler=scheduler,
            candidate_buffer=candidate_buffer,
            signal_storage_service=signal_storage_service,
        )

        await service.run_single_cycle(10000.0)

        candidate_buffer.add.assert_called_once()
        signal_storage_service.process_cycle.assert_awaited_once()


class TestNotifications:
    async def test_storage_occurs_before_notification(self):
        call_order = []

        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))

        async def _process_cycle(cycle_result):
            call_order.append("storage")
            return ["stored-result"]

        async def _process_storage_results(results):
            call_order.append("notification")
            return []

        signal_storage_service = MagicMock(process_cycle=AsyncMock(side_effect=_process_cycle))
        notification_service = MagicMock(
            process_storage_results=AsyncMock(side_effect=_process_storage_results)
        )
        service = _build_service(
            scheduler=scheduler,
            signal_storage_service=signal_storage_service,
            notification_service=notification_service,
        )

        await service.run_single_cycle(10000.0)

        assert call_order == ["storage", "notification"]

    async def test_newly_stored_signals_notified(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))
        storage_results = ["stored-result-1", "stored-result-2"]
        signal_storage_service = MagicMock(process_cycle=AsyncMock(return_value=storage_results))
        notification_service = MagicMock(process_storage_results=AsyncMock(return_value=[]))
        service = _build_service(
            scheduler=scheduler,
            signal_storage_service=signal_storage_service,
            notification_service=notification_service,
        )

        await service.run_single_cycle(10000.0)

        notification_service.process_storage_results.assert_awaited_once_with(storage_results)

    async def test_duplicate_stored_signal_not_notified(self):
        # process_cycle returning an empty list (e.g. all duplicates were
        # filtered upstream) means the notification service is never invoked
        # with anything to send.
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))
        signal_storage_service = MagicMock(process_cycle=AsyncMock(return_value=[]))
        notification_service = MagicMock(process_storage_results=AsyncMock())
        service = _build_service(
            scheduler=scheduler,
            signal_storage_service=signal_storage_service,
            notification_service=notification_service,
        )

        await service.run_single_cycle(10000.0)

        notification_service.process_storage_results.assert_not_awaited()

    async def test_telegram_failure_logged(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))
        signal_storage_service = MagicMock(process_cycle=AsyncMock(return_value=["stored-result"]))
        notification_service = MagicMock(
            process_storage_results=AsyncMock(side_effect=RuntimeError("telegram down"))
        )
        logger = MagicMock()
        service = _build_service(
            scheduler=scheduler,
            signal_storage_service=signal_storage_service,
            notification_service=notification_service,
            logger=logger,
        )

        await service.run_single_cycle(10000.0)

        logger.error.assert_called_once()

    async def test_telegram_failure_does_not_stop_scanner(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))
        signal_storage_service = MagicMock(process_cycle=AsyncMock(return_value=["stored-result"]))
        notification_service = MagicMock(
            process_storage_results=AsyncMock(side_effect=RuntimeError("telegram down"))
        )
        service = _build_service(
            scheduler=scheduler,
            signal_storage_service=signal_storage_service,
            notification_service=notification_service,
            notification_failure_is_fatal=False,
        )

        # Must not raise.
        result = await service.run_single_cycle(10000.0)
        assert result is not None

    async def test_candidate_buffer_unchanged_with_notifications_enabled(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(
            return_value=_cycle_result([_pair_result(status=PairScanStatus.VALID)])
        )
        candidate_buffer = MagicMock(add=AsyncMock())
        signal_storage_service = MagicMock(process_cycle=AsyncMock(return_value=["stored-result"]))
        notification_service = MagicMock(process_storage_results=AsyncMock(return_value=[]))
        service = _build_service(
            scheduler=scheduler,
            candidate_buffer=candidate_buffer,
            signal_storage_service=signal_storage_service,
            notification_service=notification_service,
        )

        await service.run_single_cycle(10000.0)

        candidate_buffer.add.assert_called_once()

    async def test_no_notification_when_service_disabled(self):
        scheduler = MagicMock()
        scheduler.run_cycle = AsyncMock(return_value=_cycle_result([_pair_result()]))
        signal_storage_service = MagicMock(process_cycle=AsyncMock(return_value=["stored-result"]))
        service = _build_service(
            scheduler=scheduler,
            signal_storage_service=signal_storage_service,
            notification_service=None,
        )

        # Must not raise even though there's nothing to notify with.
        result = await service.run_single_cycle(10000.0)
        assert result is not None
