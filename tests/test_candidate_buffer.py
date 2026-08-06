"""
Tests for app.scanner.candidate_buffer.ValidSignalCandidateBuffer.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.risk.results import RiskPlan, RiskPlanStatus
from app.scanner.candidate_buffer import ValidSignalCandidateBuffer
from app.scanner.pipeline_results import PipelineStatus, StrategyPipelineResult
from app.scanner.scan_results import PairScanResult, PairScanStatus

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _risk_plan():
    risk_plan = MagicMock(spec=RiskPlan)
    risk_plan.status = RiskPlanStatus.VALID
    return risk_plan


def _pipeline_result():
    return StrategyPipelineResult(
        symbol="BTC-USDT",
        expected_direction="BUY",
        detection_time_utc=UTC_NOW,
        status=PipelineStatus.VALID,
        passed=True,
        stages=[],
        risk_plan=_risk_plan(),
    )


def _pair_result(
    *,
    symbol="BTC-USDT",
    status=PairScanStatus.VALID,
    duplicate=False,
    duplicate_key=None,
    reason=None,
    error_type=None,
):
    pipeline_result = None
    if status in (PairScanStatus.VALID, PairScanStatus.DUPLICATE):
        pipeline_result = _pipeline_result()

    return PairScanResult(
        symbol=symbol,
        status=status,
        pipeline_result=pipeline_result,
        duplicate_key=duplicate_key or ("dup-key" if duplicate else None),
        duplicate=duplicate,
        started_at_utc=UTC_NOW,
        completed_at_utc=UTC_NOW,
        duration_ms=1.0,
        reason=reason or ("Duplicate institutional setup suppressed." if duplicate else None),
        error_type=error_type,
    )


class TestValidSignalCandidateBuffer:
    async def test_valid_candidate_accepted(self):
        buffer = ValidSignalCandidateBuffer(maximum_size=10)
        result = _pair_result()
        await buffer.add(result)
        assert await buffer.size() == 1

    async def test_rejected_result_refused(self):
        buffer = ValidSignalCandidateBuffer(maximum_size=10)
        result = _pair_result(status=PairScanStatus.REJECTED, reason="not trending")
        await buffer.add(result)
        assert await buffer.size() == 0

    async def test_duplicate_result_refused(self):
        buffer = ValidSignalCandidateBuffer(maximum_size=10)
        result = _pair_result(status=PairScanStatus.DUPLICATE, duplicate=True)
        await buffer.add(result)
        assert await buffer.size() == 0

    async def test_insertion_order_preserved(self):
        buffer = ValidSignalCandidateBuffer(maximum_size=10)
        first = _pair_result(symbol="BTC-USDT")
        second = _pair_result(symbol="ETH-USDT")
        await buffer.add(first)
        await buffer.add(second)
        items = await buffer.get_all()
        assert [item.symbol for item in items] == ["BTC-USDT", "ETH-USDT"]

    async def test_maximum_size_enforced(self):
        buffer = ValidSignalCandidateBuffer(maximum_size=2)
        for index in range(5):
            await buffer.add(_pair_result(symbol=f"SYMBOL-{index}"))
        items = await buffer.get_all()
        assert len(items) == 2
        assert [item.symbol for item in items] == ["SYMBOL-3", "SYMBOL-4"]

    async def test_get_all_does_not_mutate_internal_buffer(self):
        buffer = ValidSignalCandidateBuffer(maximum_size=10)
        await buffer.add(_pair_result())
        items = await buffer.get_all()
        items.append(_pair_result(symbol="ETH-USDT"))
        assert await buffer.size() == 1

    async def test_drain_returns_and_clears(self):
        buffer = ValidSignalCandidateBuffer(maximum_size=10)
        await buffer.add(_pair_result())
        drained = await buffer.drain()
        assert len(drained) == 1
        assert await buffer.size() == 0

    async def test_clear_works(self):
        buffer = ValidSignalCandidateBuffer(maximum_size=10)
        await buffer.add(_pair_result())
        await buffer.clear()
        assert await buffer.size() == 0

    async def test_concurrent_adds_safe(self):
        buffer = ValidSignalCandidateBuffer(maximum_size=100)
        results = [_pair_result(symbol=f"SYMBOL-{index}") for index in range(20)]
        await asyncio.gather(*[buffer.add(result) for result in results])
        assert await buffer.size() == 20
