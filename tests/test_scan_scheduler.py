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
from app.scanner.scan_scheduler import MultiPairScanScheduler, _seconds_until_next_interval_boundary

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _valid_pipeline_result(symbol: str) -> StrategyPipelineResult:
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


def _pair_result(
    symbol: str, status: PairScanStatus = PairScanStatus.REJECTED
) -> PairScanResult:
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
    wall_clock=None,
    retry_count_provider=None,
):
    kwargs = dict(
        pair_scanner=pair_scanner or _build_pair_scanner(),
        configured_pair_provider=lambda: list(pairs),
        scanner_interval_seconds=interval_seconds,
        maximum_concurrent_scans=5,
        clock=clock or _FakeClock(),
    )
    if wall_clock is not None:
        kwargs["wall_clock"] = wall_clock
    if retry_count_provider is not None:
        kwargs["retry_count_provider"] = retry_count_provider
    return MultiPairScanScheduler(**kwargs)


async def _run_cycle(scheduler: MultiPairScanScheduler):
    return await scheduler.run_cycle(
        account_balance=10000.0,
        active_trade_count=0,
        active_positions=[],
        active_position_candles={},
    )


class TestSecondsUntilNextIntervalBoundary:
    def test_seven_seconds_into_a_fifteen_second_window(self):
        now = datetime(2026, 1, 1, 10, 0, 7, tzinfo=timezone.utc)
        assert _seconds_until_next_interval_boundary(now, 15) == pytest.approx(8.0)

    def test_exactly_on_a_boundary_returns_a_full_interval(self):
        now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        assert _seconds_until_next_interval_boundary(now, 15) == pytest.approx(15.0)

    def test_five_minute_candle_boundary(self):
        # 10:02:30 UTC -> next 5-minute boundary is 10:05:00 (150s away).
        now = datetime(2026, 1, 1, 10, 2, 30, tzinfo=timezone.utc)
        assert _seconds_until_next_interval_boundary(now, 300) == pytest.approx(150.0)

    def test_never_negative(self):
        now = datetime(2026, 1, 1, 10, 0, 0, 999999, tzinfo=timezone.utc)
        assert _seconds_until_next_interval_boundary(now, 300) > 0

    def test_non_positive_interval_returned_unchanged(self):
        now = datetime(2026, 1, 1, 10, 0, 7, tzinfo=timezone.utc)
        assert _seconds_until_next_interval_boundary(now, 0) == 0


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

    async def test_valid_results_ranked_alphabetically_by_symbol(self):
        # VALID results carry no score of any kind any more (the signal
        # decision is binary CONFIRMED/REJECTED), so ranking is now a
        # stable, deterministic alphabetical ordering by symbol -- purely
        # a display-order convenience, never a priority/quality ordering.
        pair_scanner = _build_pair_scanner(
            results_by_symbol={
                "BTC-USDT": _pair_result("BTC-USDT", status=PairScanStatus.VALID),
                "ETH-USDT": _pair_result("ETH-USDT", status=PairScanStatus.VALID),
                "SOL-USDT": _pair_result("SOL-USDT", status=PairScanStatus.VALID),
            }
        )
        scheduler = _build_scheduler(
            pair_scanner=pair_scanner, pairs=("SOL-USDT", "ETH-USDT", "BTC-USDT")
        )
        cycle_result = await _run_cycle(scheduler)
        ranked_symbols = [r.symbol for r in cycle_result.valid_results]
        assert ranked_symbols == ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

    async def test_ranking_never_drops_a_qualifying_coin(self):
        # Every coin that passed (VALID) must still be present in the
        # ranked output, however many there are -- ranking only reorders,
        # never truncates.
        symbols = [f"COIN{i}-USDT" for i in range(37)]
        pair_scanner = _build_pair_scanner(
            results_by_symbol={
                symbol: _pair_result(symbol, status=PairScanStatus.VALID)
                for symbol in symbols
            }
        )
        scheduler = _build_scheduler(pair_scanner=pair_scanner, pairs=tuple(symbols))
        cycle_result = await _run_cycle(scheduler)
        assert len(cycle_result.valid_results) == 37
        assert {r.symbol for r in cycle_result.valid_results} == set(symbols)

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


class TestRunCycleLogging:
    async def test_scan_started_logged_with_pair_count(self, caplog):
        scheduler = _build_scheduler(pairs=("BTC-USDT", "ETH-USDT"))
        with caplog.at_level("INFO"):
            await _run_cycle(scheduler)
        assert any("scan started for 2 pairs" in message for message in caplog.messages)

    async def test_scan_completed_logged_with_all_category_counts(self, caplog):
        pair_scanner = _build_pair_scanner(
            results_by_symbol={
                "ETH-USDT": _pair_result("ETH-USDT", status=PairScanStatus.REJECTED),
                "SOL-USDT": _pair_result("SOL-USDT", status=PairScanStatus.ERROR),
            }
        )
        scheduler = _build_scheduler(
            pair_scanner=pair_scanner, pairs=("BTC-USDT", "ETH-USDT", "SOL-USDT")
        )
        with caplog.at_level("INFO"):
            await _run_cycle(scheduler)
        completed_messages = [m for m in caplog.messages if "scan completed" in m]
        assert len(completed_messages) == 1
        message = completed_messages[0]
        assert "1 valid" in message
        assert "1 rejected" in message
        assert "1 error" in message
        assert "0 duplicate" in message

    async def test_retry_count_delta_included_when_provider_given(self, caplog):
        call_count = {"n": 0}

        def _retry_count_provider():
            call_count["n"] += 1
            # First call (before scanning) returns 10, second call (after
            # scanning) returns 13 -- a delta of 3 retries this cycle.
            return 10 if call_count["n"] == 1 else 13

        scheduler = _build_scheduler(
            pairs=("BTC-USDT",), retry_count_provider=_retry_count_provider
        )
        with caplog.at_level("INFO"):
            await _run_cycle(scheduler)
        completed_messages = [m for m in caplog.messages if "scan completed" in m]
        assert "retries=3" in completed_messages[0]

    async def test_retries_reported_as_not_available_without_a_provider(self, caplog):
        scheduler = _build_scheduler(pairs=("BTC-USDT",))
        with caplog.at_level("INFO"):
            await _run_cycle(scheduler)
        completed_messages = [m for m in caplog.messages if "scan completed" in m]
        assert "retries=n/a" in completed_messages[0]

    async def test_rejection_breakdown_still_present_in_completed_log(self, caplog):
        pair_scanner = _build_pair_scanner(
            results_by_symbol={"ETH-USDT": _pair_result("ETH-USDT", status=PairScanStatus.REJECTED)}
        )
        scheduler = _build_scheduler(pair_scanner=pair_scanner, pairs=("BTC-USDT", "ETH-USDT"))
        with caplog.at_level("INFO"):
            await _run_cycle(scheduler)
        completed_messages = [m for m in caplog.messages if "scan completed" in m]
        assert "Rejected breakdown" in completed_messages[0]


class TestRunForever:
    async def test_sleep_aligns_to_next_wall_clock_interval_boundary(self):
        # Interval is 15s; wall clock reads 10:00:07 UTC (7s into the
        # current 15-second boundary window starting at 10:00:00), so the
        # scheduler must sleep exactly 8s to reach the next boundary
        # (10:00:15) -- not a fixed offset from cycle-start clock time.
        clock = _FakeClock()
        wall_clock = lambda: datetime(2026, 1, 1, 10, 0, 7, tzinfo=timezone.utc)

        pair_scanner = _build_pair_scanner()
        scheduler = _build_scheduler(
            pair_scanner=pair_scanner,
            pairs=("BTC-USDT",),
            interval_seconds=15,
            clock=clock,
            wall_clock=wall_clock,
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

        assert wait_for_calls[0] == pytest.approx(8.0)

    async def test_exact_boundary_sleeps_a_full_interval_not_zero(self):
        # Wall clock lands exactly on a boundary (10:00:00): the next
        # candle-close boundary is a full interval away, not 0.
        clock = _FakeClock()
        wall_clock = lambda: datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)

        pair_scanner = _build_pair_scanner()
        scheduler = _build_scheduler(
            pair_scanner=pair_scanner,
            pairs=("BTC-USDT",),
            interval_seconds=15,
            clock=clock,
            wall_clock=wall_clock,
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

        assert wait_for_calls[0] == pytest.approx(15.0)

    async def test_sleep_never_negative_even_after_a_long_cycle(self):
        # Regardless of how long the cycle itself took, the computed
        # sleep is always derived from the current wall-clock reading at
        # the moment sleep is computed, so it can never go negative.
        clock = _FakeClock()
        wall_clock = lambda: datetime(2026, 1, 1, 10, 0, 13, tzinfo=timezone.utc)

        async def _slow_scan_pair(**kwargs):
            clock.advance(20.0)  # longer than the 15-second interval
            return _pair_result(kwargs["symbol"], status=PairScanStatus.VALID)

        pair_scanner = MagicMock()
        pair_scanner.scan_pair = AsyncMock(side_effect=_slow_scan_pair)
        scheduler = _build_scheduler(
            pair_scanner=pair_scanner,
            pairs=("BTC-USDT",),
            interval_seconds=15,
            clock=clock,
            wall_clock=wall_clock,
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

        assert wait_for_calls[0] >= 0.0

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
