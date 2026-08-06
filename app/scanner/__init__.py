"""
Scanner package: strategy engine, pair scanning, scheduling, and signal publishing.
"""

from app.scanner.active_state import (
    ActiveTradingState,
    ActiveTradingStateProvider,
    EmptyActiveTradingStateProvider,
)
from app.scanner.candidate_buffer import ValidSignalCandidateBuffer
from app.scanner.duplicate_guard import DuplicateGuardError, DuplicateSignalGuard
from app.scanner.engine_factory import build_scanner_service, build_strategy_engine
from app.scanner.pair_scanner import PairScanner
from app.scanner.pipeline_exceptions import (
    PipelineDataUnavailableError,
    PipelineInputError,
    PipelineStageError,
    StrategyPipelineError,
)
from app.scanner.pipeline_results import PipelineStageResult, PipelineStatus, StrategyPipelineResult
from app.scanner.scan_results import (
    PairScanResult,
    PairScanStatus,
    ScanCycleResult,
    ScannerRuntimeStatus,
)
from app.scanner.scan_scheduler import MultiPairScanScheduler
from app.scanner.scanner_events import ScannerEvent, ScannerEventType
from app.scanner.scanner_service import ScannerService
from app.scanner.signal_builder import InstitutionalSignalBuilder, SignalBuildError

__all__ = [
    "StrategyPipelineResult",
    "PipelineStageResult",
    "PipelineStatus",
    "StrategyPipelineError",
    "PipelineInputError",
    "PipelineStageError",
    "PipelineDataUnavailableError",
    "build_strategy_engine",
    "PairScanner",
    "MultiPairScanScheduler",
    "ScannerService",
    "DuplicateSignalGuard",
    "DuplicateGuardError",
    "ValidSignalCandidateBuffer",
    "ActiveTradingState",
    "ActiveTradingStateProvider",
    "EmptyActiveTradingStateProvider",
    "PairScanResult",
    "ScanCycleResult",
    "ScannerRuntimeStatus",
    "PairScanStatus",
    "build_scanner_service",
    "InstitutionalSignalBuilder",
    "SignalBuildError",
    "ScannerEvent",
    "ScannerEventType",
]
