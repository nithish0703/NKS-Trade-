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

    async def record_cycle_result(self, cycle_result: ScanCycleResult) -> None:
        async with self._lock:
            self._latest_cycle_result = cycle_result
            for pair_result in cycle_result.pair_results:
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
