"""
Top-level service coordinating signal building and local persistence
for scanner results. Never publishes, broadcasts, or executes trades.
"""

import logging
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from app.models.signal import Signal
from app.scanner.duplicate_guard import DuplicateSignalGuard
from app.scanner.pipeline_results import PipelineStatus
from app.scanner.scan_results import PairScanResult, PairScanStatus, ScanCycleResult
from app.scanner.signal_builder import InstitutionalSignalBuilder, SignalBuildError
from app.storage.analytics_repository import AnalyticsRepository
from app.storage.database import DatabaseOperationError, StorageError
from app.storage.signal_repository import (
    DASHBOARD_STATUS_ACTIVE,
    DuplicateSignalStorageError,
    SignalRepository,
)

# Temporary DEBUG-only logger for the DuplicateGuard lifecycle audit.
# Does not affect production behaviour: only emits when DEBUG level
# is enabled, and no logic below reads from or branches on it.
_debug_logger = logging.getLogger(__name__)


class SignalStorageResult(BaseModel):
    """Outcome of attempting to build and persist a Signal for one pair result."""

    model_config = ConfigDict(frozen=True)

    signal: Optional[Signal] = None
    stored: bool
    duplicate: bool
    reason: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class SignalStorageService:
    """
    Builds and persists final signals from scanner results, and
    optionally records rejection analytics. Performs no publishing,
    no Telegram, no dashboard, no WebSocket, and no live trading.
    """

    def __init__(
        self,
        *,
        signal_builder: InstitutionalSignalBuilder,
        signal_repository: SignalRepository,
        analytics_repository: AnalyticsRepository,
        settings: Any,
        duplicate_guard: Optional[DuplicateSignalGuard] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._signal_builder = signal_builder
        self._signal_repository = signal_repository
        self._database_manager = signal_repository.database_manager
        self._analytics_repository = analytics_repository
        self._settings = settings
        # Marks a setup as seen in DuplicateGuard only after it has been
        # successfully persisted (see _process_valid_result below).
        # Optional so existing callers/tests that construct this service
        # without a guard keep working unchanged; when omitted, no
        # DuplicateGuard marking occurs here (the guard simply won't be
        # informed, matching prior behaviour for those callers).
        self._duplicate_guard = duplicate_guard
        self._logger = logger or logging.getLogger(__name__)

    @property
    def database_manager(self):
        return self._database_manager

    async def process_pair_result(
        self, pair_scan_result: PairScanResult
    ) -> Optional[SignalStorageResult]:
        """
        Build and persist a Signal for a VALID PairScanResult, optionally
        record rejection analytics for a REJECTED result, and otherwise
        do nothing. Never mutates `pair_scan_result`.
        """
        if pair_scan_result.status == PairScanStatus.VALID:
            return await self._process_valid_result(pair_scan_result)

        if pair_scan_result.status == PairScanStatus.REJECTED:
            pipeline_result = pair_scan_result.pipeline_result
            if pipeline_result is not None and pipeline_result.status == PipelineStatus.REJECTED:
                try:
                    await self._analytics_repository.save_rejection(pipeline_result)
                except StorageError:
                    self._logger.warning(
                        "Failed to save rejection analytics for %s", pair_scan_result.symbol
                    )
            return None

        # DUPLICATE, ERROR, and SKIPPED results are never rebuilt or stored.
        return None

    async def _process_valid_result(self, pair_scan_result: PairScanResult) -> SignalStorageResult:
        try:
            signal = self._signal_builder.build(pair_scan_result)
        except SignalBuildError as exc:
            return SignalStorageResult(signal=None, stored=False, duplicate=False, reason=str(exc))

        _debug_logger.debug("Signal created: trade_id=%s setup_key=%s", signal.trade_id, signal.setup_key)

        # Skip generating a new signal for a coin that already has an
        # ACTIVE signal on the dashboard, regardless of whether that
        # active trade is currently winning or losing. Read-only check;
        # does not alter risk management, TP/SL, or duplicate matching.
        already_active = await self._signal_repository.list_recent_with_status(
            limit=1, symbol=signal.coin, dashboard_status=DASHBOARD_STATUS_ACTIVE
        )
        if already_active:
            reason = (
                f"An ACTIVE signal already exists for {signal.coin}; "
                "new signal skipped until it is closed."
            )
            self._logger.info(
                "Signal skipped for %s: an ACTIVE signal already exists for this coin.",
                signal.coin,
            )
            return SignalStorageResult(signal=signal, stored=False, duplicate=False, reason=reason)

        try:
            stored_signal = await self._signal_repository.save(signal)
        except DuplicateSignalStorageError as exc:
            return SignalStorageResult(
                signal=signal, stored=False, duplicate=True, reason=str(exc)
            )
        except DatabaseOperationError:
            raise

        _debug_logger.debug("Signal saved: trade_id=%s setup_key=%s", stored_signal.trade_id, stored_signal.setup_key)

        # Only mark the setup as seen in DuplicateGuard now that it has
        # been successfully persisted -- never before. This is purely a
        # cache-insertion timing fix: it does not alter duplicate
        # matching, setup-key generation, or any trading/risk/repository
        # behaviour.
        if self._duplicate_guard is not None:
            duplicate_key = pair_scan_result.duplicate_key
            if duplicate_key:
                await self._duplicate_guard.register(duplicate_key, pair_scan_result.pipeline_result.detection_time_utc)
                _debug_logger.debug("DuplicateGuard marked: setup_key=%s", duplicate_key)

        return SignalStorageResult(signal=stored_signal, stored=True, duplicate=False)

    async def process_cycle(self, cycle_result: ScanCycleResult) -> list[SignalStorageResult]:
        """Process every pair result in a scan cycle, preserving configured order."""
        results: list[SignalStorageResult] = []
        for pair_result in cycle_result.pair_results:
            result = await self.process_pair_result(pair_result)
            if result is not None:
                results.append(result)
        return results
