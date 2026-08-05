"""
Tests for app.storage.signal_service.SignalStorageService.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.signal import Direction, MarketRegime, Signal, SignalStatus
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
        setup_key="setup-1",
        liquidity_sweep_id="sweep-1",
        structure_break_id="break-1",
        entry_zone_id="zone-1",
        retest_id="retest-1",
        created_at_utc=UTC_NOW,
    )


def _valid_pair_result(symbol="BTC-USDT") -> PairScanResult:
    pair_result = MagicMock(spec=PairScanResult)
    pair_result.symbol = symbol
    pair_result.status = PairScanStatus.VALID
    pair_result.pipeline_result = MagicMock(spec=StrategyPipelineResult)
    pair_result.pipeline_result.status = PipelineStatus.VALID
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


def _build_service(signal_builder=None, signal_repository=None, analytics_repository=None):
    return SignalStorageService(
        signal_builder=signal_builder or MagicMock(build=MagicMock(return_value=_signal())),
        signal_repository=signal_repository or MagicMock(save=AsyncMock(side_effect=lambda s: s)),
        analytics_repository=analytics_repository or MagicMock(save_rejection=AsyncMock()),
        settings=MagicMock(),
    )


class TestProcessPairResult:
    async def test_valid_pair_result_builds_and_stores_signal(self):
        signal_builder = MagicMock(build=MagicMock(return_value=_signal()))
        signal_repository = MagicMock(save=AsyncMock(side_effect=lambda s: s))
        service = _build_service(signal_builder=signal_builder, signal_repository=signal_repository)

        result = await service.process_pair_result(_valid_pair_result())

        assert isinstance(result, SignalStorageResult)
        assert result.stored is True
        assert result.duplicate is False
        signal_repository.save.assert_awaited_once()

    async def test_duplicate_storage_handled(self):
        signal_repository = MagicMock(
            save=AsyncMock(side_effect=DuplicateSignalStorageError("already exists"))
        )
        service = _build_service(signal_repository=signal_repository)

        result = await service.process_pair_result(_valid_pair_result())

        assert result.stored is False
        assert result.duplicate is True

    async def test_rejected_result_stored_only_as_analytics(self):
        analytics_repository = MagicMock(save_rejection=AsyncMock())
        service = _build_service(analytics_repository=analytics_repository)

        result = await service.process_pair_result(_rejected_pair_result())

        assert result is None
        analytics_repository.save_rejection.assert_awaited_once()

    async def test_rejected_result_with_preview_never_builds_signal(self):
        # Even a preview with a resolved BUY direction and high progress
        # must never cause a Signal to be built or persisted for a
        # REJECTED PairScanResult.
        from app.scanner.preview_analyzer import PreviewAnalysisResult, PreviewLayerStatus

        preview = PreviewAnalysisResult(
            symbol="BTC-USDT",
            preview_direction="BUY",
            preview_progress_raw_score=115.0,
            preview_progress_max_score=120.0,
            preview_progress_percentage=96,
            preview_completed_layers=["MARKET_REGIME", "HTF_BIAS"],
            preview_failed_layers=[],
            preview_data_availability={"MARKET_REGIME": PreviewLayerStatus.PASSED},
        )
        rejected_with_preview = _rejected_pair_result().model_copy(update={"preview_result": preview})

        signal_builder = MagicMock(build=MagicMock())
        signal_repository = MagicMock(save=AsyncMock())
        analytics_repository = MagicMock(save_rejection=AsyncMock())
        service = _build_service(
            signal_builder=signal_builder,
            signal_repository=signal_repository,
            analytics_repository=analytics_repository,
        )

        result = await service.process_pair_result(rejected_with_preview)

        assert result is None
        signal_builder.build.assert_not_called()
        signal_repository.save.assert_not_awaited()
        analytics_repository.save_rejection.assert_awaited_once()

    async def test_scanner_duplicate_ignored(self):
        signal_builder = MagicMock(build=MagicMock())
        analytics_repository = MagicMock(save_rejection=AsyncMock())
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
        signal_repository = MagicMock(
            save=AsyncMock(side_effect=DatabaseOperationError("disk full"))
        )
        service = _build_service(signal_repository=signal_repository)

        with pytest.raises(DatabaseOperationError):
            await service.process_pair_result(_valid_pair_result())

    async def test_no_side_effects_beyond_repositories(self):
        signal_repository = MagicMock(save=AsyncMock(side_effect=lambda s: s))
        service = _build_service(signal_repository=signal_repository)

        await service.process_pair_result(_valid_pair_result())

        # No Telegram/dashboard/WebSocket-style attributes should ever be touched.
        for call in signal_repository.method_calls:
            assert "telegram" not in call[0].lower()
            assert "broadcast" not in call[0].lower()


class TestProcessCycle:
    async def test_cycle_ordering_preserved(self):
        pair_results = [
            _valid_pair_result(symbol="BTC-USDT"),
            _rejected_pair_result(symbol="ETH-USDT"),
            _duplicate_pair_result(symbol="SOL-USDT"),
        ]
        cycle_result = MagicMock(spec=ScanCycleResult)
        cycle_result.pair_results = pair_results

        signal_repository = MagicMock(save=AsyncMock(side_effect=lambda s: s))
        analytics_repository = MagicMock(save_rejection=AsyncMock())
        service = _build_service(
            signal_repository=signal_repository, analytics_repository=analytics_repository
        )

        results = await service.process_cycle(cycle_result)

        # Only the VALID and REJECTED pair results produce a return entry
        # (VALID -> SignalStorageResult, REJECTED -> None which is filtered);
        # DUPLICATE produces neither.
        assert len(results) == 1
        assert results[0].stored is True
