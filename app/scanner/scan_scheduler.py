"""
Schedules recurring, non-overlapping multi-pair scan cycles.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence

from app.models.candle import Candle
from app.scanner.pair_scanner import PairScanner
from app.scanner.scan_results import (
    PairScanResult,
    PairScanStatus,
    ScanCycleResult,
    ScannerRuntimeStatus,
)

ClockProvider = Callable[[], float]


def _default_clock() -> float:
    return asyncio.get_event_loop().time()


def _build_cycle_id(sequence_number: int, started_at_utc: datetime) -> str:
    digest_input = f"{sequence_number}|{started_at_utc.isoformat()}"
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]


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
        clock: ClockProvider = _default_clock,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._pair_scanner = pair_scanner
        self._configured_pair_provider = configured_pair_provider
        self._scanner_interval_seconds = scanner_interval_seconds
        self._maximum_concurrent_scans = maximum_concurrent_scans
        self._clock = clock
        self._logger = logger or logging.getLogger(__name__)

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

        configured_pairs = list(self._configured_pair_provider())
        detection_time_utc = started_at_utc

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
        rejected_results = [r for r in pair_results if r.status == PairScanStatus.REJECTED]
        duplicate_results = [r for r in pair_results if r.status == PairScanStatus.DUPLICATE]
        error_results = [r for r in pair_results if r.status == PairScanStatus.ERROR]
        skipped_results = [r for r in pair_results if r.status == PairScanStatus.SKIPPED]

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
        )

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
                cycle_start_clock = self._clock()

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

                elapsed_seconds = self._clock() - cycle_start_clock
                remaining_seconds = max(0.0, self._scanner_interval_seconds - elapsed_seconds)

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
