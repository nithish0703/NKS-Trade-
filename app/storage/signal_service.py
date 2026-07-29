"""
Top-level service coordinating signal building and local persistence
for scanner results. Never publishes, broadcasts, or executes trades.
"""

import logging
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from app.models.signal import Signal
from app.scanner.pipeline_results import PipelineStatus
from app.scanner.scan_results import PairScanResult, PairScanStatus, ScanCycleResult
from app.scanner.signal_builder import InstitutionalSignalBuilder, SignalBuildError
from app.storage.analytics_repository import AnalyticsRepository
from app.storage.database import DatabaseOperationError, StorageError
from app.storage.signal_repository import DuplicateSignalStorageError, SignalRepository


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
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._signal_builder = signal_builder
        self._signal_repository = signal_repository
        self._database_manager = signal_repository.database_manager
        self._analytics_repository = analytics_repository
        self._settings = settings
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

        try:
            stored_signal = await self._signal_repository.save(signal)
        except DuplicateSignalStorageError as exc:
            return SignalStorageResult(
                signal=signal, stored=False, duplicate=True, reason=str(exc)
            )
        except DatabaseOperationError:
            raise

        return SignalStorageResult(signal=stored_signal, stored=True, duplicate=False)

    async def process_cycle(self, cycle_result: ScanCycleResult) -> list[SignalStorageResult]:
        """Process every pair result in a scan cycle, preserving configured order."""
        results: list[SignalStorageResult] = []
        for pair_result in cycle_result.pair_results:
            result = await self.process_pair_result(pair_result)
            if result is not None:
                results.append(result)
        return results
