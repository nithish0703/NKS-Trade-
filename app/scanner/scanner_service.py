"""
Top-level scanner service orchestrating recurring scan cycles, the
valid-signal candidate buffer, and optional local signal persistence.
Does not publish, broadcast, or trade.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from app.scanner.active_state import ActiveTradingStateProvider
from app.scanner.candidate_buffer import ValidSignalCandidateBuffer
from app.scanner.scan_results import PairScanStatus, ScanCycleResult, ScannerRuntimeStatus
from app.scanner.scan_scheduler import MultiPairScanScheduler
from app.scanner.scanner_events import ScannerEvent, ScannerEventType

EventCallback = Callable[[ScannerEvent], Awaitable[None]]
CycleResultCallback = Callable[[ScanCycleResult], Awaitable[None]]


class ScannerService:
    """Wires the scan scheduler, active-state provider, candidate buffer, and signal storage together."""

    def __init__(
        self,
        *,
        scheduler: MultiPairScanScheduler,
        candidate_buffer: ValidSignalCandidateBuffer,
        active_state_provider: ActiveTradingStateProvider,
        signal_storage_service: Optional[Any] = None,
        storage_failure_is_fatal: bool = False,
        notification_service: Optional[Any] = None,
        notification_failure_is_fatal: bool = False,
        pair_discovery_service: Optional[Any] = None,
        on_event: Optional[EventCallback] = None,
        on_cycle_result: Optional[CycleResultCallback] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._scheduler = scheduler
        self._candidate_buffer = candidate_buffer
        self._active_state_provider = active_state_provider
        self._signal_storage_service = signal_storage_service
        self._storage_failure_is_fatal = storage_failure_is_fatal
        self._notification_service = notification_service
        self._notification_failure_is_fatal = notification_failure_is_fatal
        self._pair_discovery_service = pair_discovery_service
        self._on_event = on_event
        self._on_cycle_result = on_cycle_result
        self._logger = logger or logging.getLogger(__name__)

    @property
    def signal_storage_service(self) -> Optional[Any]:
        return self._signal_storage_service

    @property
    def notification_service(self) -> Optional[Any]:
        return self._notification_service

    @property
    def pair_discovery_service(self) -> Optional[Any]:
        return self._pair_discovery_service

    async def run_single_cycle(self, account_balance: float) -> ScanCycleResult:
        active_state = await self._active_state_provider.get_state()

        await self._emit(ScannerEventType.SCAN_CYCLE_STARTED, {})

        cycle_result = await self._scheduler.run_cycle(
            account_balance=account_balance,
            active_trade_count=active_state.active_trade_count,
            active_positions=active_state.active_positions,
            active_position_candles=active_state.active_position_candles,
        )

        await self._on_cycle_complete(cycle_result)

        return cycle_result

    async def run_forever(self, account_balance: float) -> None:
        async def _get_state():
            return await self._active_state_provider.get_state()

        async def _on_cycle_complete_with_start_event(cycle_result: ScanCycleResult) -> None:
            await self._on_cycle_complete(cycle_result)

        discovery_task = None
        if self._pair_discovery_service is not None:
            discovery_task = asyncio.create_task(self._pair_discovery_service.run_forever())

        try:
            await self._scheduler.run_forever(
                account_balance=account_balance,
                active_state_provider=_get_state,
                on_cycle_complete=_on_cycle_complete_with_start_event,
            )
        finally:
            if discovery_task is not None:
                self._pair_discovery_service.request_shutdown()
                await discovery_task

    async def _on_cycle_complete(self, cycle_result: ScanCycleResult) -> None:
        await self._store_valid_candidates(cycle_result)
        storage_results = await self._persist_signals(cycle_result)
        await self._notify_stored_signals(storage_results)

        if self._on_cycle_result is not None:
            try:
                await self._on_cycle_result(cycle_result)
            except Exception as exc:  # noqa: BLE001 - an observer failure must never affect the scanner
                self._logger.warning("Scanner cycle-result observer failed: %s", exc)

        await self._emit(
            ScannerEventType.SCAN_CYCLE_COMPLETED,
            {
                "cycle_id": cycle_result.cycle_id,
                "total_pairs": cycle_result.total_pairs,
                "valid_count": cycle_result.valid_count,
                "rejected_count": cycle_result.rejected_count,
                "duplicate_count": cycle_result.duplicate_count,
                "error_count": cycle_result.error_count,
                "duration_ms": cycle_result.duration_ms,
            },
        )

    async def _store_valid_candidates(self, cycle_result: ScanCycleResult) -> None:
        for pair_result in cycle_result.pair_results:
            if pair_result.status == PairScanStatus.VALID:
                await self._candidate_buffer.add(pair_result)

    async def _persist_signals(self, cycle_result: ScanCycleResult) -> list:
        if self._signal_storage_service is None:
            return []

        try:
            storage_results = await self._signal_storage_service.process_cycle(cycle_result)
        except Exception as exc:  # noqa: BLE001 - a storage failure must not crash the scanner
            self._logger.error("Signal persistence failed for cycle %s: %s", cycle_result.cycle_id, exc)
            if self._storage_failure_is_fatal:
                raise
            return []

        await self._emit_storage_events(storage_results)

        return storage_results

    async def _emit_storage_events(self, storage_results: list) -> None:
        # Event emission is purely observational: a storage_result of an
        # unexpected shape (e.g. a test double) must never affect the
        # scanner's own persistence/notification behaviour above.
        for storage_result in storage_results:
            try:
                signal = storage_result.signal
                stored = storage_result.stored
                duplicate = storage_result.duplicate
            except AttributeError:
                continue

            if signal is None:
                continue

            if stored:
                await self._emit(
                    ScannerEventType.SIGNAL_STORED,
                    {
                        "trade_id": signal.trade_id,
                        "coin": signal.coin,
                        "direction": signal.direction.value,
                        "status": signal.status.value,
                    },
                )
            elif duplicate:
                await self._emit(
                    ScannerEventType.SIGNAL_DUPLICATE,
                    {"coin": signal.coin, "setup_key": signal.setup_key},
                )

    async def _notify_stored_signals(self, storage_results: list) -> None:
        if self._notification_service is None or not storage_results:
            return

        try:
            notification_results = await self._notification_service.process_storage_results(
                storage_results
            )
        except Exception as exc:  # noqa: BLE001 - a notification failure must not crash the scanner
            self._logger.error("Telegram notification failed: %s", exc)
            if self._notification_failure_is_fatal:
                raise
            return

        for result in notification_results:
            # Compared against the NotificationStatus string values
            # directly (rather than importing the enum) to avoid a
            # circular import between app.scanner and app.notifications.
            if result.status == "SENT":
                await self._emit(
                    ScannerEventType.TELEGRAM_SENT,
                    {"trade_id": result.trade_id, "chat_id_suffix": result.chat_id_suffix},
                )
            elif result.status == "FAILED":
                await self._emit(
                    ScannerEventType.TELEGRAM_FAILED,
                    {
                        "trade_id": result.trade_id,
                        "chat_id_suffix": result.chat_id_suffix,
                        "reason": result.reason,
                    },
                )

    async def _emit(self, event_type: ScannerEventType, data: dict) -> None:
        if self._on_event is None:
            return
        try:
            await self._on_event(
                ScannerEvent(event=event_type, timestamp_utc=datetime.now(timezone.utc), data=data)
            )
        except Exception as exc:  # noqa: BLE001 - a broadcast failure must never affect the scanner
            self._logger.warning("Scanner event broadcast failed for %s: %s", event_type.value, exc)

    def request_shutdown(self) -> None:
        self._scheduler.request_shutdown()
        if self._pair_discovery_service is not None:
            self._pair_discovery_service.request_shutdown()

    async def request_shutdown_and_notify(self) -> None:
        self.request_shutdown()
        await self._emit(ScannerEventType.SCANNER_STATUS_CHANGED, {"shutdown_requested": True})

    def get_runtime_status(self) -> ScannerRuntimeStatus:
        return self._scheduler.get_runtime_status()
