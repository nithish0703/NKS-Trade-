"""
In-memory buffer holding new VALID (CONFIRMED) scanner candidates,
pending later signal building and persistence (not implemented here).
"""

import asyncio

from app.scanner.scan_results import PairScanResult, PairScanStatus


class ValidSignalCandidateBuffer:
    """
    Temporarily holds new VALID (every required condition met,
    non-duplicate) scanner results in insertion order, bounded by a
    maximum size.

    Does not publish, persist, or send any notification.
    """

    def __init__(self, *, maximum_size: int) -> None:
        if maximum_size <= 0:
            raise ValueError("maximum_size must be positive.")
        self._maximum_size = maximum_size
        self._items: list[PairScanResult] = []
        self._lock = asyncio.Lock()

    async def add(self, result: PairScanResult) -> None:
        if result.status != PairScanStatus.VALID:
            return
        if result.pipeline_result is None:
            return
        if result.duplicate:
            return

        async with self._lock:
            self._items.append(result)
            if len(self._items) > self._maximum_size:
                overflow = len(self._items) - self._maximum_size
                self._items = self._items[overflow:]

    async def get_all(self) -> list[PairScanResult]:
        async with self._lock:
            return list(self._items)

    async def drain(self) -> list[PairScanResult]:
        async with self._lock:
            drained = list(self._items)
            self._items = []
            return drained

    async def size(self) -> int:
        async with self._lock:
            return len(self._items)

    async def clear(self) -> None:
        async with self._lock:
            self._items = []
