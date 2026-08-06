"""
Tests for app.scanner.pair_scanner.PairScanner.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.market_context import MarketContext
from app.risk.results import RiskPlan, RiskPlanStatus
from app.scanner.duplicate_guard import DuplicateSignalGuard
from app.scanner.pair_scanner import PairScanner
from app.scanner.pipeline_exceptions import PipelineDataUnavailableError
from app.scanner.pipeline_results import PipelineStatus, StrategyPipelineResult
from app.scanner.scan_results import PairScanStatus

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _risk_plan():
    risk_plan = MagicMock(spec=RiskPlan)
    risk_plan.status = RiskPlanStatus.VALID
    return risk_plan


def _sweep(sweep_id="sweep-1"):
    sweep = MagicMock()
    sweep.sweep_id = sweep_id
    return sweep


def _zone(zone_id="zone-1"):
    zone = MagicMock()
    zone.zone_id = zone_id
    return zone


def _break(break_id="break-1"):
    structure_break = MagicMock()
    structure_break.break_id = break_id
    return structure_break


def _valid_pipeline_result(symbol="BTC-USDT"):
    return StrategyPipelineResult(
        symbol=symbol,
        expected_direction="BUY",
        detection_time_utc=UTC_NOW,
        status=PipelineStatus.VALID,
        passed=True,
        stages=[],
        liquidity_sweep=_sweep(),
        selected_entry_zone=_zone(),
        selected_structure_break=_break(),
        risk_plan=_risk_plan(),
    )


def _market_context(symbol="BTC-USDT") -> MarketContext:
    return MarketContext(
        symbol=symbol,
        detection_time_utc=UTC_NOW,
        candles_by_timeframe={},
    )


def _rejected_pipeline_result(symbol="BTC-USDT", market_context=None):
    return StrategyPipelineResult(
        symbol=symbol,
        expected_direction=None,
        detection_time_utc=UTC_NOW,
        status=PipelineStatus.REJECTED,
        passed=False,
        failed_layer="HTF_BIAS",
        rejection_reason="Higher-timeframe bias is MIXED or UNKNOWN.",
        stages=[],
        market_context=market_context,
    )


def _build_scanner(
    strategy_engine=None,
    duplicate_guard=None,
    semaphore=None,
    on_pair_result=None,
) -> PairScanner:
    return PairScanner(
        strategy_engine=strategy_engine or MagicMock(),
        duplicate_guard=duplicate_guard or DuplicateSignalGuard(retention_seconds=3600, maximum_entries=1000),
        semaphore=semaphore or asyncio.Semaphore(5),
        on_pair_result=on_pair_result,
    )


async def _scan(scanner: PairScanner, symbol="BTC-USDT"):
    return await scanner.scan_pair(
        symbol=symbol,
        account_balance=10000.0,
        active_trade_count=0,
        active_positions=[],
        active_position_candles={},
        detection_time_utc=UTC_NOW,
    )


class TestPairScannerOutcomes:
    async def test_valid_result(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_pipeline_result())
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        assert result.status == PairScanStatus.VALID

    async def test_rejected_pipeline_result(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_rejected_pipeline_result())
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        assert result.status == PairScanStatus.REJECTED
        assert result.reason == "Higher-timeframe bias is MIXED or UNKNOWN."

    async def test_error_pipeline_result(self):
        error_result = StrategyPipelineResult(
            symbol="BTC-USDT",
            expected_direction=None,
            detection_time_utc=UTC_NOW,
            status=PipelineStatus.ERROR,
            passed=False,
            stages=[],
        )
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=error_result)
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        assert result.status == PairScanStatus.ERROR

    async def test_duplicate_valid_result(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_pipeline_result())
        guard = DuplicateSignalGuard(retention_seconds=3600, maximum_entries=1000)
        scanner = _build_scanner(strategy_engine=engine, duplicate_guard=guard)
        first = await _scan(scanner)
        second = await _scan(scanner)
        assert first.status == PairScanStatus.VALID
        assert second.status == PairScanStatus.DUPLICATE
        assert second.duplicate is True

    async def test_new_valid_result(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_pipeline_result())
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        assert result.status == PairScanStatus.VALID
        assert result.duplicate is False

    async def test_unexpected_engine_exception_converted_to_error(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(side_effect=RuntimeError("boom"))
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        assert result.status == PairScanStatus.ERROR
        assert result.error_type == "RuntimeError"

    async def test_pipeline_data_unavailable_error_converted_to_error(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(
            side_effect=PipelineDataUnavailableError(
                layer_name="MARKET_DATA_PREPARATION", symbol="BTC-USDT", reason="no data"
            )
        )
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        assert result.status == PairScanStatus.ERROR
        assert result.error_type == "PipelineDataUnavailableError"


class TestPairScannerConcurrency:
    async def test_semaphore_is_acquired(self):
        semaphore = asyncio.Semaphore(1)
        engine = MagicMock()

        acquired_during_call = False

        async def _analyze(**kwargs):
            nonlocal acquired_during_call
            acquired_during_call = semaphore.locked()
            return _valid_pipeline_result()

        engine.analyze_symbol = AsyncMock(side_effect=_analyze)
        scanner = _build_scanner(strategy_engine=engine, semaphore=semaphore)
        await _scan(scanner)
        assert acquired_during_call is True
        assert not semaphore.locked()

    async def test_semaphore_released_after_exception(self):
        semaphore = asyncio.Semaphore(1)
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(side_effect=RuntimeError("boom"))
        scanner = _build_scanner(strategy_engine=engine, semaphore=semaphore)
        await _scan(scanner)
        assert not semaphore.locked()


class TestPairScannerMetadata:
    async def test_duration_recorded(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_pipeline_result())
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        assert result.duration_ms >= 0.0
        assert result.completed_at_utc >= result.started_at_utc

    async def test_no_persistence_or_publishing_side_effect(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_pipeline_result())
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        result_fields = set(type(result).model_fields.keys())
        forbidden = {"telegram_status", "dashboard_status", "exchange_order_id", "persisted"}
        assert result_fields.isdisjoint(forbidden)


class TestOnPairResultCallback:
    async def test_callback_invoked_with_the_final_result(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_pipeline_result())

        received = []

        async def _on_pair_result(result):
            received.append(result)

        scanner = _build_scanner(strategy_engine=engine, on_pair_result=_on_pair_result)
        result = await _scan(scanner)

        assert received == [result]

    async def test_callback_invoked_for_every_status_including_error(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(side_effect=RuntimeError("boom"))

        received = []

        async def _on_pair_result(result):
            received.append(result)

        scanner = _build_scanner(strategy_engine=engine, on_pair_result=_on_pair_result)
        result = await _scan(scanner)

        assert len(received) == 1
        assert received[0].status == PairScanStatus.ERROR

    async def test_callback_exception_never_affects_the_returned_result(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_pipeline_result())

        async def _on_pair_result(result):
            raise RuntimeError("observer boom")

        scanner = _build_scanner(strategy_engine=engine, on_pair_result=_on_pair_result)
        result = await _scan(scanner)  # must not raise

        assert result.status == PairScanStatus.VALID

    async def test_no_callback_configured_is_a_no_op(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_pipeline_result())

        scanner = _build_scanner(strategy_engine=engine, on_pair_result=None)
        result = await _scan(scanner)  # must not raise

        assert result.status == PairScanStatus.VALID
