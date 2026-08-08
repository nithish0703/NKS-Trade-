"""
Tests for app.storage.signal_service.SignalStorageService.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.signal import Direction, Signal, SignalStatus
from app.scanner.pipeline_results import PipelineStatus, StrategyPipelineResult
from app.scanner.scan_results import PairScanResult, PairScanStatus, ScanCycleResult
from app.scanner.signal_builder import SignalBuildError
from app.storage.database import DatabaseOperationError
from app.storage.signal_repository import DuplicateSignalStorageError
from app.storage.signal_service import SignalStorageResult, SignalStorageService

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _signal(trade_id="SMC-1") -> Signal:
    return Signal(
        trade_id=trade_id,
        coin="BTC-USDT",
        direction=Direction.BUY,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        risk_reward_ratio=3.0,
        status=SignalStatus.CONFIRMED,
        liquidity_type="EQUAL_HIGH",
        entry_zone_type="ORDER_BLOCK",
        structure_confirmation="BOS",
        detection_time_utc=UTC_NOW,
        institutional_reason="Confirmed setup facts only.",
        setup_key="setup-1",
        liquidity_sweep_id="sweep-1",
        structure_break_id="break-1",
        entry_zone_id="zone-1",
        created_at_utc=UTC_NOW,
    )


def _valid_pair_result(symbol="BTC-USDT") -> PairScanResult:
    pair_result = MagicMock(spec=PairScanResult)
    pair_result.symbol = symbol
    pair_result.status = PairScanStatus.VALID
    pair_result.pipeline_result = MagicMock(spec=StrategyPipelineResult)
    pair_result.pipeline_result.status = PipelineStatus.VALID
    # MagicMock(spec=StrategyPipelineResult) only recognizes attributes
    # present on the class object itself; pydantic instance fields
    # (risk_plan, order_flow_confidence, entry_grade, stages) aren't
    # visible to `spec` and must be set explicitly or every read raises
    # AttributeError.
    pair_result.pipeline_result.risk_plan = None
    pair_result.pipeline_result.order_flow_confidence = None
    pair_result.pipeline_result.entry_grade = None
    pair_result.pipeline_result.stages = []
    return pair_result


def _rejected_pair_result(symbol="BTC-USDT") -> PairScanResult:
    pipeline_result = StrategyPipelineResult(
        symbol=symbol,
        expected_direction=None,
        detection_time_utc=UTC_NOW,
        status=PipelineStatus.REJECTED,
        passed=False,
        failed_layer="MARKET_REGIME",
        rejection_reason="not trending",
        stages=[],
    )
    return PairScanResult(
        symbol=symbol,
        status=PairScanStatus.REJECTED,
        pipeline_result=pipeline_result,
        started_at_utc=UTC_NOW,
        completed_at_utc=UTC_NOW,
        duration_ms=1.0,
        reason="not trending",
    )


def _duplicate_pair_result(symbol="BTC-USDT") -> PairScanResult:
    pair_result = MagicMock(spec=PairScanResult)
    pair_result.symbol = symbol
    pair_result.status = PairScanStatus.DUPLICATE
    # A duplicate is still a genuine pipeline run underneath (the
    # pipeline reached VALID again; PairScanner just recognized the
    # setup as already-seen) -- stage analytics still get recorded for
    # it, so `pipeline_result` must be present, not None.
    pair_result.pipeline_result = MagicMock(spec=StrategyPipelineResult)
    pair_result.pipeline_result.status = PipelineStatus.VALID
    pair_result.pipeline_result.stages = []
    return pair_result


def _error_pair_result(symbol="BTC-USDT") -> PairScanResult:
    return PairScanResult(
        symbol=symbol,
        status=PairScanStatus.ERROR,
        pipeline_result=None,
        started_at_utc=UTC_NOW,
        completed_at_utc=UTC_NOW,
        duration_ms=1.0,
        reason="boom",
        error_type="RuntimeError",
    )


def _skipped_pair_result(symbol="BTC-USDT") -> PairScanResult:
    return PairScanResult(
        symbol=symbol,
        status=PairScanStatus.SKIPPED,
        pipeline_result=None,
        started_at_utc=UTC_NOW,
        completed_at_utc=UTC_NOW,
        duration_ms=1.0,
        reason="skipped",
    )


def _analytics_repo(**overrides):
    """
    Build an analytics_repository mock with sensible defaults for both
    save_rejection() (REJECTED results) and save_stage_results()
    (every result that ran the pipeline). Any attribute can be
    overridden by passing it as a keyword.
    """
    defaults = dict(
        save_rejection=AsyncMock(),
        save_stage_results=AsyncMock(),
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


def _build_service(signal_builder=None, signal_repository=None, analytics_repository=None):
    return SignalStorageService(
        signal_builder=signal_builder or MagicMock(build=MagicMock(return_value=_signal())),
        signal_repository=signal_repository or _repo(),
        analytics_repository=analytics_repository or _analytics_repo(),
        settings=MagicMock(),
    )


def _repo(**overrides):
    """
    Build a signal_repository mock with sensible defaults:
    - save() echoes back whatever Signal it was given
    - list_recent_with_status() reports no existing ACTIVE signal for the
      coin (i.e. the new "already active" skip in SignalStorageService
      never triggers unless a test explicitly asks it to)
    Any attribute can be overridden by passing it as a keyword.
    """
    defaults = dict(
        save=AsyncMock(side_effect=lambda s, **_kwargs: s),
        list_recent_with_status=AsyncMock(return_value=[]),
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


class TestProcessPairResult:
    async def test_valid_pair_result_builds_and_stores_signal(self):
        signal_builder = MagicMock(build=MagicMock(return_value=_signal()))
        signal_repository = _repo()
        service = _build_service(signal_builder=signal_builder, signal_repository=signal_repository)

        result = await service.process_pair_result(_valid_pair_result())

        assert isinstance(result, SignalStorageResult)
        assert result.stored is True
        assert result.duplicate is False
        signal_repository.save.assert_awaited_once()

    async def test_duplicate_storage_handled(self):
        signal_repository = _repo(save=AsyncMock(side_effect=DuplicateSignalStorageError("already exists")))
        service = _build_service(signal_repository=signal_repository)

        result = await service.process_pair_result(_valid_pair_result())

        assert result.stored is False
        assert result.duplicate is True

    async def test_rejected_result_stored_only_as_analytics(self):
        analytics_repository = _analytics_repo()
        service = _build_service(analytics_repository=analytics_repository)

        result = await service.process_pair_result(_rejected_pair_result())

        assert result is None
        analytics_repository.save_rejection.assert_awaited_once()

    async def test_scanner_duplicate_ignored(self):
        signal_builder = MagicMock(build=MagicMock())
        analytics_repository = _analytics_repo()
        service = _build_service(signal_builder=signal_builder, analytics_repository=analytics_repository)

        result = await service.process_pair_result(_duplicate_pair_result())

        assert result is None
        signal_builder.build.assert_not_called()
        analytics_repository.save_rejection.assert_not_awaited()

    async def test_error_ignored(self):
        signal_builder = MagicMock(build=MagicMock())
        service = _build_service(signal_builder=signal_builder)

        result = await service.process_pair_result(_error_pair_result())

        assert result is None
        signal_builder.build.assert_not_called()

    async def test_skipped_ignored(self):
        signal_builder = MagicMock(build=MagicMock())
        service = _build_service(signal_builder=signal_builder)

        result = await service.process_pair_result(_skipped_pair_result())

        assert result is None
        signal_builder.build.assert_not_called()

    async def test_signal_build_error_returns_unstored_result(self):
        signal_builder = MagicMock(build=MagicMock(side_effect=SignalBuildError("bad setup")))
        service = _build_service(signal_builder=signal_builder)

        result = await service.process_pair_result(_valid_pair_result())

        assert result.stored is False
        assert result.duplicate is False
        assert result.signal is None

    async def test_storage_failure_surfaced_safely(self):
        signal_repository = _repo(save=AsyncMock(side_effect=DatabaseOperationError("disk full")))
        service = _build_service(signal_repository=signal_repository)

        with pytest.raises(DatabaseOperationError):
            await service.process_pair_result(_valid_pair_result())

    async def test_no_side_effects_beyond_repositories(self):
        signal_repository = _repo()
        service = _build_service(signal_repository=signal_repository)

        await service.process_pair_result(_valid_pair_result())

        # No Telegram/dashboard/WebSocket-style attributes should ever be touched.
        for call in signal_repository.method_calls:
            assert "telegram" not in call[0].lower()
            assert "broadcast" not in call[0].lower()

    async def test_skips_new_signal_when_coin_already_active(self):
        # An ACTIVE dashboard signal already exists for this coin, so no
        # new signal should be built/saved, and DuplicateGuard must not
        # be marked (nothing was persisted).
        existing_active = MagicMock()
        signal_repository = _repo(list_recent_with_status=AsyncMock(return_value=[existing_active]))
        duplicate_guard = MagicMock(register=AsyncMock())
        service = SignalStorageService(
            signal_builder=MagicMock(build=MagicMock(return_value=_signal())),
            signal_repository=signal_repository,
            analytics_repository=_analytics_repo(),
            settings=MagicMock(),
            duplicate_guard=duplicate_guard,
        )

        result = await service.process_pair_result(_valid_pair_result())

        assert result.stored is False
        assert result.duplicate is False
        assert result.signal is not None
        assert "ACTIVE" in result.reason
        signal_repository.list_recent_with_status.assert_awaited_once_with(
            limit=1, symbol="BTC-USDT", dashboard_status="ACTIVE"
        )
        signal_repository.save.assert_not_awaited()
        duplicate_guard.register.assert_not_awaited()

    async def test_saves_new_signal_when_coin_not_active(self):
        signal_repository = _repo(list_recent_with_status=AsyncMock(return_value=[]))
        service = _build_service(signal_repository=signal_repository)

        result = await service.process_pair_result(_valid_pair_result())

        assert result.stored is True
        signal_repository.save.assert_awaited_once()


class TestProcessCycle:
    async def test_cycle_ordering_preserved(self):
        pair_results = [
            _valid_pair_result(symbol="BTC-USDT"),
            _rejected_pair_result(symbol="ETH-USDT"),
            _duplicate_pair_result(symbol="SOL-USDT"),
        ]
        cycle_result = MagicMock(spec=ScanCycleResult)
        cycle_result.pair_results = pair_results

        signal_repository = _repo()
        analytics_repository = _analytics_repo()
        service = _build_service(
            signal_repository=signal_repository, analytics_repository=analytics_repository
        )

        results = await service.process_cycle(cycle_result)

        # Only the VALID and REJECTED pair results produce a return entry
        # (VALID -> SignalStorageResult, REJECTED -> None which is filtered);
        # DUPLICATE produces neither.
        assert len(results) == 1
        assert results[0].stored is True
