"""
In-process shared runtime state for the dashboard API.

The dashboard API and the scanner run in the same process/event loop so
that dashboard routes can read the latest scan-cycle results and
runtime status directly, without any additional IPC. This store is
purely observational: nothing here can affect strategy or trading
behaviour, and the scanner runs unaffected if no dashboard is attached.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from app.scanner.scan_results import PairScanResult, ScanCycleResult
from app.scanner.scanner_events import ScannerEvent


class DashboardRuntimeStore:
    """
    Holds the latest scan-cycle result, per-pair results, and recent
    scanner events for dashboard consumption. Thread/async-safe for the
    single-event-loop, multiple-coroutine access pattern used by FastAPI.
    """

    def __init__(self, *, max_recent_events: int = 200) -> None:
        self._lock = asyncio.Lock()
        self._latest_cycle_result: Optional[ScanCycleResult] = None
        self._latest_pair_results: dict[str, PairScanResult] = {}
        self._recent_events: list[ScannerEvent] = []
        self._max_recent_events = max_recent_events
        self._scanner_running = False
        self._last_updated_utc: Optional[datetime] = None
        # Captured once, at construction, which happens exactly once per
        # process lifetime (in app.api.main's lifespan) -- this is the
        # API process's actual start time, used for the dashboard's
        # "server started at" / uptime display.
        self._started_at_utc = datetime.now(timezone.utc)

    @property
    def started_at_utc(self) -> datetime:
        return self._started_at_utc

    async def record_cycle_result(self, cycle_result: ScanCycleResult) -> None:
        async with self._lock:
            self._latest_cycle_result = cycle_result
            for pair_result in cycle_result.pair_results:
                self._latest_pair_results[pair_result.symbol] = pair_result
            self._last_updated_utc = datetime.now(timezone.utc)

    async def record_pair_result(self, pair_result: PairScanResult) -> None:
        """
        Record a single pair's result the instant its scan finishes, so
        REST polling (get_scanning_coins) reflects each coin in real
        time instead of waiting for the whole cycle to complete. Safe to
        call interleaved with an in-flight `record_cycle_result` for the
        same cycle -- the eventual `record_cycle_result` call always
        wins for that symbol since it runs after every pair's task,
        including this one, has completed.
        """
        async with self._lock:
            self._latest_pair_results[pair_result.symbol] = pair_result
            self._last_updated_utc = datetime.now(timezone.utc)

    async def record_event(self, event: ScannerEvent) -> None:
        async with self._lock:
            self._recent_events.append(event)
            if len(self._recent_events) > self._max_recent_events:
                self._recent_events = self._recent_events[-self._max_recent_events :]

    def set_scanner_running(self, running: bool) -> None:
        self._scanner_running = running

    @property
    def scanner_running(self) -> bool:
        return self._scanner_running

    async def get_latest_cycle_result(self) -> Optional[ScanCycleResult]:
        async with self._lock:
            return self._latest_cycle_result

    async def get_latest_pair_results(self) -> dict[str, PairScanResult]:
        async with self._lock:
            return dict(self._latest_pair_results)

    async def get_recent_events(self, limit: int = 50) -> list[ScannerEvent]:
        async with self._lock:
            if limit <= 0:
                return []
            return list(self._recent_events[-limit:])

    async def get_last_updated_utc(self) -> Optional[datetime]:
        async with self._lock:
            return self._last_updated_utc
