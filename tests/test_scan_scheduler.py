"""
Tests for app.scanner.scan_scheduler.MultiPairScanScheduler.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.risk.results import RiskPlan, RiskPlanStatus
from app.scanner.pipeline_results import PipelineStatus, StrategyPipelineResult
from app.scanner.scan_results import PairScanResult, PairScanStatus
from app.scanner.scan_scheduler import MultiPairScanScheduler
from app.scoring.results import ConfidenceClassification, ConfidenceScoreResult

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _valid_pipeline_result(symbol: str) -> StrategyPipelineResult:
    risk_plan = MagicMock(spec=RiskPlan)
    risk_plan.status = RiskPlanStatus.VALID
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


def _pair_result(symbol: str, status: PairScanStatus = PairScanStatus.REJECTED) -> PairScanResult:
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


class _FakeClock:
    """A controllable monotonic clock for deterministic interval tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._current = start

    def __call__(self) -> float:
        return self._current

    def advance(self, seconds: float) -> None:
        self._current += seconds


def _build_pair_scanner(results_by_symbol=None, delay_symbols=None):
    pair_scanner = MagicMock()
    delay_symbols = delay_symbols or {}

    async def _scan_pair(*, symbol, **kwargs):
        if symbol in delay_symbols:
            await asyncio.sleep(delay_symbols[symbol])
        if results_by_symbol and symbol in results_by_symbol:
            outcome = results_by_symbol[symbol]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return _pair_result(symbol, status=PairScanStatus.VALID)

    pair_scanner.scan_pair = AsyncMock(side_effect=_scan_pair)
    return pair_scanner


def _build_scheduler(
    pair_scanner=None,
    pairs=("BTC-USDT", "ETH-USDT", "SOL-USDT"),
    interval_seconds=15,
    clock=None,
):
    return MultiPairScanScheduler(
        pair_scanner=pair_scanner or _build_pair_scanner(),
        configured_pair_provider=lambda: list(pairs),
        scanner_interval_seconds=interval_seconds,
        maximum_concurrent_scans=5,
        clock=clock or _FakeClock(),
    )


async def _run_cycle(scheduler: MultiPairScanScheduler):
    return await scheduler.run_cycle(
        account_balance=10000.0,
        active_trade_count=0,
        active_positions=[],
        active_position_candles={},
    )


class TestRunCycle:
    async def test_all_configured_pairs_attempted(self):
        scheduler = _build_scheduler(pairs=("BTC-USDT", "ETH-USDT", "SOL-USDT"))
        cycle_result = await _run_cycle(scheduler)
        assert set(cycle_result.attempted_pairs) == {"BTC-USDT", "ETH-USDT", "SOL-USDT"}
        assert cycle_result.total_pairs == 3

    async def test_pair_ordering_preserved(self):
        pairs = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT")
        scheduler = _build_scheduler(pairs=pairs)
        cycle_result = await _run_cycle(scheduler)
        assert [r.symbol for r in cycle_result.pair_results] == list(pairs)

    async def test_concurrent_task_execution(self):
        # Two pairs each sleep 0.05s; if scanned sequentially this would take
        # >=0.1s of real time, but concurrently it should take ~0.05s.
        pair_scanner = _build_pair_scanner(delay_symbols={"BTC-USDT": 0.05, "ETH-USDT": 0.05})
        scheduler = _build_scheduler(pair_scanner=pair_scanner, pairs=("BTC-USDT", "ETH-USDT"))
        loop = asyncio.get_event_loop()
        start = loop.time()
        await _run_cycle(scheduler)
        elapsed = loop.time() - start
        assert elapsed < 0.09

    async def test_one_pair_error_does_not_cancel_others(self):
        pair_scanner = _build_pair_scanner(
            results_by_symbol={"ETH-USDT": RuntimeError("boom")}
        )
        scheduler = _build_scheduler(
            pair_scanner=pair_scanner, pairs=("BTC-USDT", "ETH-USDT", "SOL-USDT")
        )
        cycle_result = await _run_cycle(scheduler)
        assert cycle_result.total_pairs == 3
        statuses = {r.symbol: r.status for r in cycle_result.pair_results}
        assert statuses["ETH-USDT"] == PairScanStatus.ERROR
        assert statuses["BTC-USDT"] == PairScanStatus.VALID
        assert statuses["SOL-USDT"] == PairScanStatus.VALID

    async def test_correct_category_counts(self):
        pair_scanner = _build_pair_scanner(
            results_by_symbol={
                "ETH-USDT": _pair_result("ETH-USDT", status=PairScanStatus.REJECTED),
                "SOL-USDT": _pair_result("SOL-USDT", status=PairScanStatus.DUPLICATE),
            }
        )
        scheduler = _build_scheduler(
            pair_scanner=pair_scanner, pairs=("BTC-USDT", "ETH-USDT", "SOL-USDT")
        )
        cycle_result = await _run_cycle(scheduler)
        assert cycle_result.valid_count == 1
        assert cycle_result.rejected_count == 1
        assert cycle_result.duplicate_count == 1
        assert cycle_result.error_count == 0

    async def test_deterministic_cycle_id(self):
        clock = _FakeClock()
        scheduler = _build_scheduler(clock=clock)
        first = await _run_cycle(scheduler)
        assert first.cycle_id

    async def test_shared_detection_time_for_all_pairs(self):
        pair_scanner = _build_pair_scanner()
        scheduler = _build_scheduler(pair_scanner=pair_scanner)
        await _run_cycle(scheduler)
        detection_times = {
            call.kwargs["detection_time_utc"] for call in pair_scanner.scan_pair.await_args_list
        }
        assert len(detection_times) == 1

    async def test_inputs_not_mutated(self):
        scheduler = _build_scheduler()
        active_positions = []
        active_position_candles = {}
        await scheduler.run_cycle(
            account_balance=10000.0,
            active_trade_count=0,
            active_positions=active_positions,
            active_position_candles=active_position_candles,
        )
        assert active_positions == []
        assert active_position_candles == {}


class TestRunForever:
    async def test_remaining_interval_sleep_calculated_correctly(self):
        clock = _FakeClock()

        async def _fast_scan_pair(**kwargs):
            clock.advance(2.0)  # cycle takes 2 seconds of "wall clock"
            return _pair_result(kwargs["symbol"], status=PairScanStatus.VALID)

        pair_scanner = MagicMock()
        pair_scanner.scan_pair = AsyncMock(side_effect=_fast_scan_pair)
        scheduler = _build_scheduler(
            pair_scanner=pair_scanner, pairs=("BTC-USDT",), interval_seconds=15, clock=clock
        )

        wait_for_calls = []
        original_wait_for = asyncio.wait_for

        async def _tracking_wait_for(coro, timeout):
            wait_for_calls.append(timeout)
            scheduler.request_shutdown()
            return await original_wait_for(coro, timeout=0.001)

        async def _get_state():
            state = MagicMock()
            state.active_trade_count = 0
            state.active_positions = []
            state.active_position_candles = {}
            return state

        import app.scanner.scan_scheduler as scheduler_module

        original = scheduler_module.asyncio.wait_for
        scheduler_module.asyncio.wait_for = _tracking_wait_for
        try:
            await scheduler.run_forever(account_balance=10000.0, active_state_provider=_get_state)
        finally:
            scheduler_module.asyncio.wait_for = original

        assert wait_for_calls[0] == pytest.approx(13.0)

    async def test_long_running_cycle_does_not_use_negative_sleep(self):
        clock = _FakeClock()

        async def _slow_scan_pair(**kwargs):
            clock.advance(20.0)  # longer than the 15-second interval
            return _pair_result(kwargs["symbol"], status=PairScanStatus.VALID)

        pair_scanner = MagicMock()
        pair_scanner.scan_pair = AsyncMock(side_effect=_slow_scan_pair)
        scheduler = _build_scheduler(
            pair_scanner=pair_scanner, pairs=("BTC-USDT",), interval_seconds=15, clock=clock
        )

        wait_for_calls = []

        async def _get_state():
            state = MagicMock()
            state.active_trade_count = 0
            state.active_positions = []
            state.active_position_candles = {}
            return state

        import app.scanner.scan_scheduler as scheduler_module

        original = scheduler_module.asyncio.wait_for

        async def _tracking_wait_for(coro, timeout):
            wait_for_calls.append(timeout)
            scheduler.request_shutdown()
            return await original(coro, timeout=0.001)

        scheduler_module.asyncio.wait_for = _tracking_wait_for
        try:
            await scheduler.run_forever(account_balance=10000.0, active_state_provider=_get_state)
        finally:
            scheduler_module.asyncio.wait_for = original

        assert wait_for_calls[0] == 0.0

    async def test_no_overlapping_cycles(self):
        clock = _FakeClock()
        concurrent_cycles = 0
        max_concurrent_cycles = 0

        async def _tracked_scan_pair(**kwargs):
            nonlocal concurrent_cycles, max_concurrent_cycles
            concurrent_cycles += 1
            max_concurrent_cycles = max(max_concurrent_cycles, concurrent_cycles)
            await asyncio.sleep(0.01)
            concurrent_cycles -= 1
            return _pair_result(kwargs["symbol"], status=PairScanStatus.VALID)

        pair_scanner = MagicMock()
        pair_scanner.scan_pair = AsyncMock(side_effect=_tracked_scan_pair)
        scheduler = _build_scheduler(
            pair_scanner=pair_scanner, pairs=("BTC-USDT",), interval_seconds=0.001, clock=clock
        )

        cycles_run = 0

        async def _get_state():
            nonlocal cycles_run
            cycles_run += 1
            if cycles_run > 3:
                scheduler.request_shutdown()
            state = MagicMock()
            state.active_trade_count = 0
            state.active_positions = []
            state.active_position_candles = {}
            return state

        await scheduler.run_forever(account_balance=10000.0, active_state_provider=_get_state)
        assert max_concurrent_cycles == 1

    async def test_shutdown_before_next_cycle(self):
        scheduler = _build_scheduler(pairs=("BTC-USDT",), interval_seconds=0.001)
        scheduler.request_shutdown()

        async def _get_state():
            state = MagicMock()
            state.active_trade_count = 0
            state.active_positions = []
            state.active_position_candles = {}
            return state

        await scheduler.run_forever(account_balance=10000.0, active_state_provider=_get_state)
        status = scheduler.get_runtime_status()
        assert status.cycles_completed == 0

    async def test_graceful_shutdown_during_sleep(self):
        scheduler = _build_scheduler(pairs=("BTC-USDT",), interval_seconds=5)

        async def _get_state():
            state = MagicMock()
            state.active_trade_count = 0
            state.active_positions = []
            state.active_position_candles = {}
            return state

        run_task = asyncio.create_task(
            scheduler.run_forever(account_balance=10000.0, active_state_provider=_get_state)
        )
        await asyncio.sleep(0.01)
        scheduler.request_shutdown()
        await asyncio.wait_for(run_task, timeout=2.0)
        status = scheduler.get_runtime_status()
        assert status.running is False
        assert status.cycles_completed == 1

    async def test_runtime_status_updated(self):
        scheduler = _build_scheduler(pairs=("BTC-USDT",), interval_seconds=0.001)

        cycles_run = 0

        async def _get_state():
            nonlocal cycles_run
            cycles_run += 1
            if cycles_run > 1:
                scheduler.request_shutdown()
            state = MagicMock()
            state.active_trade_count = 0
            state.active_positions = []
            state.active_position_candles = {}
            return state

        await scheduler.run_forever(account_balance=10000.0, active_state_provider=_get_state)
        status = scheduler.get_runtime_status()
        assert status.cycles_completed >= 1
        assert status.last_cycle_id is not None
        assert status.running is False


class TestRequestShutdown:
    def test_request_shutdown_sets_event(self):
        scheduler = _build_scheduler()
        assert scheduler.get_runtime_status().shutdown_requested is False
        scheduler.request_shutdown()
        assert scheduler.get_runtime_status().shutdown_requested is True
