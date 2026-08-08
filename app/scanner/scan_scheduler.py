"""
Schedules recurring, non-overlapping multi-pair scan cycles.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Optional, Protocol, Sequence

from app.models.candle import Candle
from app.scanner.pair_scanner import PairScanner
from app.scanner.scan_results import (
    PairScanResult,
    PairScanStatus,
    ScanCycleResult,
    ScannerRuntimeStatus,
)

ClockProvider = Callable[[], float]
WallClockProvider = Callable[[], datetime]


class LeaseGuard(Protocol):
    """
    Structural type for a DB-backed mutual-exclusion lease (see
    app.storage.monitor_lease.MonitorLeaseGuard), used only as a type
    hint here -- this module never imports app.storage directly, to
    avoid the circular import app.storage's package __init__ would
    otherwise create (see app.scanner.engine_factory's own comment on
    the same issue).
    """

    async def try_acquire(self, now: datetime) -> bool: ...


def _default_clock() -> float:
    return asyncio.get_event_loop().time()


def _default_wall_clock() -> datetime:
    return datetime.now(timezone.utc)


def _build_cycle_id(sequence_number: int, started_at_utc: datetime) -> str:
    digest_input = f"{sequence_number}|{started_at_utc.isoformat()}"
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]


def _seconds_until_next_interval_boundary(now: datetime, interval_seconds: float) -> float:
    """
    Seconds remaining until the next wall-clock UTC boundary that is a
    multiple of `interval_seconds` since the epoch (e.g. for the default
    300-second/5-minute interval: :00, :05, :10, ... past every hour).

    This is what lets the scanner trigger immediately when a new
    5-minute candle closes, rather than drifting from real candle-close
    timestamps based on process start time or cycle-duration variance.
    Returns `interval_seconds` itself (a full interval) if `now` lands
    exactly on a boundary, and `interval_seconds` unchanged for any
    non-positive interval (never divides by zero or returns a negative
    wait).
    """
    if interval_seconds <= 0:
        return interval_seconds
    elapsed_in_interval = now.timestamp() % interval_seconds
    remaining = interval_seconds - elapsed_in_interval
    if remaining <= 0:
        return interval_seconds
    return remaining


class MultiPairScanScheduler:
    """
    Runs one full scan cycle across all configured pairs concurrently
    (bounded by the PairScanner's semaphore) and can run cycles forever
    on a fixed interval without overlapping.
    """

    def __init__(
        self,
        *,
        pair_scanner: PairScanner,
        configured_pair_provider: Callable[[], Sequence[str]],
        scanner_interval_seconds: float,
        maximum_concurrent_scans: int,
        retry_count_provider: Optional[Callable[[], int]] = None,
        clock: ClockProvider = _default_clock,
        wall_clock: WallClockProvider = _default_wall_clock,
        logger: Optional[logging.Logger] = None,
        lease_guard: Optional[LeaseGuard] = None,
    ) -> None:
        self._pair_scanner = pair_scanner
        self._configured_pair_provider = configured_pair_provider
        self._scanner_interval_seconds = scanner_interval_seconds
        self._maximum_concurrent_scans = maximum_concurrent_scans
        # Optional accessor returning the process-lifetime total retry
        # count from the underlying market-data provider (e.g.
        # BinanceFuturesMarketDataProvider.total_retry_count), used only
        # to log a per-cycle retry delta -- never read by any
        # scan/strategy logic.
        self._retry_count_provider = retry_count_provider
        self._clock = clock
        self._wall_clock = wall_clock
        self._logger = logger or logging.getLogger(__name__)
        # DB-backed lease (see app.storage.monitor_lease.MonitorLeaseGuard,
        # wired in by app.scanner.engine_factory.build_scanner_service)
        # so at most one process's scanner actually fetches candles and
        # scans a given cycle when `python main.py scan` and the
        # dashboard API's uvicorn process both point at the same
        # database -- otherwise every candle fetch is fully duplicated
        # between the two, roughly doubling Binance request weight for
        # no benefit. None (the default) never gates a cycle, matching
        # pre-lease behaviour for any caller that doesn't wire one in
        # (e.g. direct unit tests).
        self._lease_guard = lease_guard

        self._shutdown_event = asyncio.Event()
        self._running = False
        self._cycles_completed = 0
        self._cycle_sequence = 0
        self._last_cycle_result: Optional[ScanCycleResult] = None
        self._last_error: Optional[str] = None

    async def run_cycle(
        self,
        *,
        account_balance: float,
        active_trade_count: int,
        active_positions: Sequence[object],
        active_position_candles: Mapping[str, Sequence[Candle]],
    ) -> ScanCycleResult:
        started_at_utc = datetime.now(timezone.utc)
        start_clock = self._clock()

        self._cycle_sequence += 1
        cycle_id = _build_cycle_id(self._cycle_sequence, started_at_utc)

        if self._lease_guard is not None and not await self._lease_guard.try_acquire(started_at_utc):
            self._logger.info(
                "Cycle %s: lease held by another process; skipping candle fetch/scan this cycle.",
                cycle_id,
            )
            return self._build_lease_skipped_result(cycle_id, started_at_utc)

        configured_pairs = list(self._configured_pair_provider())
        detection_time_utc = started_at_utc

        self._logger.info(
            "Cycle %s: scan started for %d pairs at %s.",
            cycle_id,
            len(configured_pairs),
            started_at_utc.isoformat(),
        )
        retry_count_before = self._retry_count_provider() if self._retry_count_provider else None

        tasks = [
            asyncio.create_task(
                self._pair_scanner.scan_pair(
                    symbol=symbol,
                    account_balance=account_balance,
                    active_trade_count=active_trade_count,
                    active_positions=active_positions,
                    active_position_candles=active_position_candles,
                    detection_time_utc=detection_time_utc,
                )
            )
            for symbol in configured_pairs
        ]

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        pair_results: list[PairScanResult] = []
        for symbol, raw_result in zip(configured_pairs, raw_results):
            if isinstance(raw_result, BaseException):
                self._logger.warning(
                    "Unhandled exception scanning %s: %s", symbol, type(raw_result).__name__
                )
                pair_results.append(self._build_task_error_result(symbol, raw_result, started_at_utc))
            else:
                pair_results.append(raw_result)

        completed_at_utc = datetime.now(timezone.utc)
        duration_ms = max(0.0, (self._clock() - start_clock) * 1000)

        valid_results = [r for r in pair_results if r.status == PairScanStatus.VALID]
        valid_results = self._rank_valid_results(valid_results)
        rejected_results = [r for r in pair_results if r.status == PairScanStatus.REJECTED]
        duplicate_results = [r for r in pair_results if r.status == PairScanStatus.DUPLICATE]
        error_results = [r for r in pair_results if r.status == PairScanStatus.ERROR]
        skipped_results = [r for r in pair_results if r.status == PairScanStatus.SKIPPED]

        rejection_breakdown = self._build_rejection_breakdown(rejected_results)
        retry_count_delta = None
        if self._retry_count_provider is not None and retry_count_before is not None:
            retry_count_delta = self._retry_count_provider() - retry_count_before

        self._logger.info(
            "Cycle %s: scan completed at %s (%.0fms). %d pairs scanned, %d valid, "
            "%d rejected, %d error, %d duplicate, retries=%s. Rejected breakdown: %s",
            cycle_id,
            completed_at_utc.isoformat(),
            duration_ms,
            len(configured_pairs),
            len(valid_results),
            len(rejected_results),
            len(error_results),
            len(duplicate_results),
            retry_count_delta if retry_count_delta is not None else "n/a",
            rejection_breakdown,
        )

        return ScanCycleResult(
            cycle_id=cycle_id,
            started_at_utc=started_at_utc,
            completed_at_utc=completed_at_utc,
            duration_ms=duration_ms,
            configured_pairs=configured_pairs,
            attempted_pairs=configured_pairs,
            valid_results=valid_results,
            rejected_results=rejected_results,
            duplicate_results=duplicate_results,
            error_results=error_results,
            skipped_results=skipped_results,
            pair_results=pair_results,
            total_pairs=len(configured_pairs),
            valid_count=len(valid_results),
            rejected_count=len(rejected_results),
            duplicate_count=len(duplicate_results),
            error_count=len(error_results),
            skipped_count=len(skipped_results),
            rejection_breakdown=rejection_breakdown,
        )

    @staticmethod
    def _build_lease_skipped_result(cycle_id: str, started_at_utc: datetime) -> ScanCycleResult:
        """
        An empty cycle result for when another process currently holds
        the scan lease: no pairs are attempted (no candle fetch, no
        strategy evaluation), distinguished via `metadata` so a caller
        can tell "this process didn't do the work this cycle" apart
        from "this process scanned and found nothing."
        """
        completed_at_utc = datetime.now(timezone.utc)
        return ScanCycleResult(
            cycle_id=cycle_id,
            started_at_utc=started_at_utc,
            completed_at_utc=completed_at_utc,
            duration_ms=0.0,
            configured_pairs=[],
            attempted_pairs=[],
            valid_results=[],
            rejected_results=[],
            duplicate_results=[],
            error_results=[],
            skipped_results=[],
            pair_results=[],
            total_pairs=0,
            valid_count=0,
            rejected_count=0,
            duplicate_count=0,
            error_count=0,
            skipped_count=0,
            rejection_breakdown={},
            metadata={"lease_skipped": True},
        )

    @staticmethod
    def _build_rejection_breakdown(rejected_results: list[PairScanResult]) -> dict[str, int]:
        """
        Count rejected pairs grouped by pipeline layer, so a scan cycle's
        signal frequency can be diagnosed at a glance: is a low VALID
        count coming from one of the six hard-mandatory strategy gates
        (market regime, HTF bias, liquidity sweep, structure shift,
        volume confirmation, entry zone), or from setups that clear all
        gates but don't reach the publishable confidence threshold?

        Grouped by `pipeline_result.failed_layer` when a hard-mandatory
        gate rejected the pipeline outright; results rejected after
        clearing every gate (e.g. below the publishable confidence
        score) have no `failed_layer` and fall back to their generic
        `reason` string instead of being dropped from the breakdown.
        """
        breakdown: dict[str, int] = {}
        for result in rejected_results:
            layer = result.pipeline_result.failed_layer if result.pipeline_result else None
            key = layer or result.reason or "UNKNOWN"
            breakdown[key] = breakdown.get(key, 0) + 1
        return breakdown

    @staticmethod
    def _rank_valid_results(valid_results: list[PairScanResult]) -> list[PairScanResult]:
        """
        Order every VALID result by symbol name, so the cycle's output
        has a stable, deterministic order across all confirmed coins
        rather than depending on concurrent-task completion order.
        VALID results carry no score of any kind (the signal decision
        is binary CONFIRMED/REJECTED) -- there is nothing left to rank
        setups against each other by, so this is a display-order
        convenience only, never a priority/quality ordering.
        """
        return sorted(valid_results, key=lambda r: r.symbol)

    @staticmethod
    def _build_task_error_result(
        symbol: str, exc: BaseException, started_at_utc: datetime
    ) -> PairScanResult:
        completed_at_utc = datetime.now(timezone.utc)
        return PairScanResult(
            symbol=symbol,
            status=PairScanStatus.ERROR,
            pipeline_result=None,
            duplicate_key=None,
            duplicate=False,
            started_at_utc=started_at_utc,
            completed_at_utc=completed_at_utc,
            duration_ms=0.0,
            reason="Unhandled exception during pair scan task.",
            error_type=type(exc).__name__,
        )

    async def run_forever(
        self,
        *,
        account_balance: float,
        active_state_provider: Callable[[], Awaitable[Any]],
        on_cycle_complete: Optional[Callable[[ScanCycleResult], Awaitable[None]]] = None,
    ) -> None:
        """
        Run scan cycles on the configured interval until shutdown is
        requested. Cycles never overlap: the next cycle only starts after
        the previous one (and its interval sleep) has completed.

        `on_cycle_complete`, when provided, is awaited with each
        successfully completed ScanCycleResult before the interval sleep,
        letting callers (e.g. ScannerService) react to cycle results
        without the scheduler needing to know about them.
        """
        self._running = True
        try:
            while not self._shutdown_event.is_set():
                active_state = await active_state_provider()

                try:
                    cycle_result = await self.run_cycle(
                        account_balance=account_balance,
                        active_trade_count=active_state.active_trade_count,
                        active_positions=active_state.active_positions,
                        active_position_candles=active_state.active_position_candles,
                    )
                    self._last_cycle_result = cycle_result
                    self._cycles_completed += 1
                    self._last_error = None
                    if on_cycle_complete is not None:
                        await on_cycle_complete(cycle_result)
                except Exception as exc:  # noqa: BLE001 - keep the scheduler alive across cycles
                    self._last_error = str(exc)
                    self._logger.error("Scan cycle failed: %s", exc)

                if self._shutdown_event.is_set():
                    break

                # Sleep until the next real UTC interval boundary (e.g.
                # the next 5-minute candle close) rather than a fixed
                # offset from cycle-start clock time, so the scanner
                # cadence stays aligned with candle closes indefinitely
                # instead of drifting after restarts or slow cycles.
                remaining_seconds = _seconds_until_next_interval_boundary(
                    self._wall_clock(), self._scanner_interval_seconds
                )

                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(), timeout=remaining_seconds
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            self._running = False

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    def get_runtime_status(self) -> ScannerRuntimeStatus:
        last_cycle = self._last_cycle_result
        return ScannerRuntimeStatus(
            running=self._running,
            shutdown_requested=self._shutdown_event.is_set(),
            cycles_completed=self._cycles_completed,
            last_cycle_started_at=last_cycle.started_at_utc if last_cycle else None,
            last_cycle_completed_at=last_cycle.completed_at_utc if last_cycle else None,
            last_cycle_id=last_cycle.cycle_id if last_cycle else None,
            last_error=self._last_error,
        )

    async def __aenter__(self) -> "MultiPairScanScheduler":
        return self

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.request_shutdown()
