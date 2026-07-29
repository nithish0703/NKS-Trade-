"""
Single-pair scan execution wrapping the institutional SMC strategy engine.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Mapping, Optional, Sequence

from app.models.candle import Candle
from app.scanner.duplicate_guard import DuplicateGuardError, DuplicateSignalGuard
from app.scanner.pipeline_exceptions import StrategyPipelineError
from app.scanner.pipeline_results import PipelineStatus, StrategyPipelineResult
from app.scanner.preview_analyzer import PreviewAnalysisResult, PreviewAnalyzer
from app.scanner.scan_results import PairScanResult, PairScanStatus
from app.scanner.strategy_engine import InstitutionalSMCStrategyEngine
from app.scoring.results import ConfidenceClassification

_PUBLISHABLE_CLASSIFICATIONS = (ConfidenceClassification.PREMIUM, ConfidenceClassification.STRONG)


def _now_ms() -> float:
    return asyncio.get_event_loop().time() * 1000


def _describe_pipeline_error(exc: StrategyPipelineError) -> str:
    """
    Build a reason string that includes the underlying cause when one is
    attached. StrategyPipelineError.reason is always a safe, generic
    summary; original_exception (when present) is itself a MarketDataError
    or IndicatorCalculationError whose message was already built to
    exclude credentials, query secrets, and full response bodies, so it is
    safe to include verbatim here.
    """
    if exc.original_exception is not None:
        return f"{exc.reason} ({exc.original_exception})"
    return exc.reason


class PairScanner:
    """
    Scans a single trading pair through the strategy engine, applying
    concurrency control and duplicate-setup suppression, and always
    produces a PairScanResult without raising to the caller.
    """

    def __init__(
        self,
        *,
        strategy_engine: InstitutionalSMCStrategyEngine,
        duplicate_guard: DuplicateSignalGuard,
        semaphore: asyncio.Semaphore,
        preview_analyzer: Optional[PreviewAnalyzer] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._strategy_engine = strategy_engine
        self._duplicate_guard = duplicate_guard
        self._semaphore = semaphore
        self._preview_analyzer = preview_analyzer
        self._logger = logger or logging.getLogger(__name__)

    async def scan_pair(
        self,
        *,
        symbol: str,
        account_balance: float,
        active_trade_count: int,
        active_positions: Sequence[object],
        active_position_candles: Mapping[str, Sequence[Candle]],
        detection_time_utc: datetime,
    ) -> PairScanResult:
        started_at_utc = datetime.now(timezone.utc)
        start_ms = _now_ms()

        async with self._semaphore:
            try:
                pipeline_result = await self._strategy_engine.analyze_symbol(
                    symbol=symbol,
                    account_balance=account_balance,
                    active_trade_count=active_trade_count,
                    active_positions=active_positions,
                    active_position_candles=active_position_candles,
                    detection_time_utc=detection_time_utc,
                )
            except StrategyPipelineError as exc:
                return self._build_error_result(
                    symbol=symbol,
                    started_at_utc=started_at_utc,
                    start_ms=start_ms,
                    reason=_describe_pipeline_error(exc),
                    error_type=type(exc).__name__,
                )
            except Exception as exc:  # noqa: BLE001 - one pair failure must not stop others
                self._logger.warning("Unexpected error scanning %s: %s", symbol, type(exc).__name__)
                return self._build_error_result(
                    symbol=symbol,
                    started_at_utc=started_at_utc,
                    start_ms=start_ms,
                    reason="Unexpected technical error during pair scan.",
                    error_type=type(exc).__name__,
                )

        return await self._interpret_pipeline_result(
            symbol=symbol,
            pipeline_result=pipeline_result,
            started_at_utc=started_at_utc,
            start_ms=start_ms,
            detection_time_utc=detection_time_utc,
        )

    async def _interpret_pipeline_result(
        self,
        *,
        symbol: str,
        pipeline_result: StrategyPipelineResult,
        started_at_utc: datetime,
        start_ms: float,
        detection_time_utc: datetime,
    ) -> PairScanResult:
        if pipeline_result.status == PipelineStatus.REJECTED:
            return self._finalize(
                symbol=symbol,
                status=PairScanStatus.REJECTED,
                pipeline_result=pipeline_result,
                started_at_utc=started_at_utc,
                start_ms=start_ms,
                reason=pipeline_result.rejection_reason,
                preview_result=self._safe_preview(symbol, pipeline_result),
            )

        if pipeline_result.status == PipelineStatus.ERROR:
            return self._finalize(
                symbol=symbol,
                status=PairScanStatus.ERROR,
                pipeline_result=pipeline_result,
                started_at_utc=started_at_utc,
                start_ms=start_ms,
                reason=pipeline_result.rejection_reason or "Pipeline reported an ERROR status.",
                error_type="PipelineError",
            )

        confidence_result = pipeline_result.confidence_result
        is_publishable = (
            confidence_result is not None
            and confidence_result.publishable
            and confidence_result.classification in _PUBLISHABLE_CLASSIFICATIONS
        )
        if not is_publishable:
            return self._finalize(
                symbol=symbol,
                status=PairScanStatus.REJECTED,
                pipeline_result=pipeline_result,
                started_at_utc=started_at_utc,
                start_ms=start_ms,
                reason="Pipeline result is not a publishable PREMIUM/STRONG setup.",
            )

        try:
            duplicate, setup_key = await self._duplicate_guard.check_and_register(
                pipeline_result, detection_time_utc
            )
        except DuplicateGuardError as exc:
            return self._build_error_result(
                symbol=symbol,
                started_at_utc=started_at_utc,
                start_ms=start_ms,
                reason=str(exc),
                error_type="DuplicateGuardError",
            )

        if duplicate:
            return self._finalize(
                symbol=symbol,
                status=PairScanStatus.DUPLICATE,
                pipeline_result=pipeline_result,
                started_at_utc=started_at_utc,
                start_ms=start_ms,
                duplicate_key=setup_key,
                duplicate=True,
                reason="Duplicate institutional setup suppressed.",
            )

        return self._finalize(
            symbol=symbol,
            status=PairScanStatus.VALID,
            pipeline_result=pipeline_result,
            started_at_utc=started_at_utc,
            start_ms=start_ms,
            duplicate_key=setup_key,
            duplicate=False,
        )

    def _build_error_result(
        self,
        *,
        symbol: str,
        started_at_utc: datetime,
        start_ms: float,
        reason: str,
        error_type: str,
    ) -> PairScanResult:
        return self._finalize(
            symbol=symbol,
            status=PairScanStatus.ERROR,
            pipeline_result=None,
            started_at_utc=started_at_utc,
            start_ms=start_ms,
            reason=reason,
            error_type=error_type,
        )

    def _safe_preview(
        self, symbol: str, pipeline_result: StrategyPipelineResult
    ) -> Optional[PreviewAnalysisResult]:
        """
        Independently compute a dashboard-only preview analysis for a
        REJECTED pipeline result, reusing the MarketContext the real
        (fail-fast) pipeline already built -- no extra network or
        database calls. Never raises: any preview failure is logged and
        treated as "no preview available" rather than affecting the
        real REJECTED PairScanResult in any way.
        """
        if self._preview_analyzer is None or pipeline_result.market_context is None:
            return None
        try:
            return self._preview_analyzer.analyze(pipeline_result.market_context)
        except Exception as exc:  # noqa: BLE001 - a preview failure must never affect the real scan
            self._logger.warning("Preview analysis failed for %s: %s", symbol, exc)
            return None

    @staticmethod
    def _finalize(
        *,
        symbol: str,
        status: PairScanStatus,
        pipeline_result: Optional[StrategyPipelineResult],
        started_at_utc: datetime,
        start_ms: float,
        duplicate_key: Optional[str] = None,
        duplicate: bool = False,
        reason: Optional[str] = None,
        error_type: Optional[str] = None,
        preview_result: Optional[PreviewAnalysisResult] = None,
    ) -> PairScanResult:
        completed_at_utc = datetime.now(timezone.utc)
        duration_ms = max(0.0, _now_ms() - start_ms)
        return PairScanResult(
            symbol=symbol,
            status=status,
            pipeline_result=pipeline_result,
            duplicate_key=duplicate_key,
            duplicate=duplicate,
            started_at_utc=started_at_utc,
            completed_at_utc=completed_at_utc,
            duration_ms=duration_ms,
            reason=reason,
            error_type=error_type,
            preview_result=preview_result,
        )
