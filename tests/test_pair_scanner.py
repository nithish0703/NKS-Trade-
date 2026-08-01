"""
Tests for app.scanner.pair_scanner.PairScanner.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.market_context import MarketContext
from app.risk.results import RiskPlan, RiskPlanStatus
from app.scanner.duplicate_guard import DuplicateSignalGuard
from app.scanner.pair_scanner import PairScanner
from app.scanner.pipeline_exceptions import PipelineDataUnavailableError
from app.scanner.pipeline_results import PipelineStatus, StrategyPipelineResult
from app.scanner.preview_analyzer import PreviewAnalysisResult, PreviewLayerStatus
from app.scanner.scan_results import PairScanStatus
from app.scoring.results import ConfidenceClassification, ConfidenceScoreResult

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _confidence(classification=ConfidenceClassification.PREMIUM, publishable=True):
    return ConfidenceScoreResult(
        raw_score=115.0,
        maximum_raw_score=115,
        normalized_score=95.8,
        classification=classification,
        publishable=publishable,
        mandatory_layers_passed=True,
        layer_scores=[],
        failed_mandatory_layers=[],
        reason="CONFIDENCE",
    )


def _risk_plan():
    risk_plan = MagicMock(spec=RiskPlan)
    risk_plan.status = RiskPlanStatus.VALID
    return risk_plan


def _sweep(sweep_id="sweep-1"):
    sweep = MagicMock()
    sweep.sweep_id = sweep_id
    return sweep


def _zone(zone_id="zone-1"):
    zone = MagicMock()
    zone.zone_id = zone_id
    return zone


def _break(break_id="break-1"):
    structure_break = MagicMock()
    structure_break.break_id = break_id
    return structure_break


def _retest(retest_id="retest-1"):
    retest = MagicMock()
    retest.retest_id = retest_id
    return retest


def _valid_pipeline_result(
    symbol="BTC-USDT", classification=ConfidenceClassification.PREMIUM, publishable=True
):
    return StrategyPipelineResult(
        symbol=symbol,
        expected_direction="BUY",
        detection_time_utc=UTC_NOW,
        status=PipelineStatus.VALID,
        passed=True,
        stages=[],
        liquidity_sweep=_sweep(),
        selected_entry_zone=_zone(),
        selected_structure_break=_break(),
        retest_result=_retest(),
        risk_plan=_risk_plan(),
        confidence_result=_confidence(classification=classification, publishable=publishable),
    )


def _market_context(symbol="BTC-USDT") -> MarketContext:
    return MarketContext(
        symbol=symbol,
        detection_time_utc=UTC_NOW,
        candles_by_timeframe={},
        btc_candles_by_timeframe={},
    )


def _rejected_pipeline_result(symbol="BTC-USDT", market_context=None):
    return StrategyPipelineResult(
        symbol=symbol,
        expected_direction=None,
        detection_time_utc=UTC_NOW,
        status=PipelineStatus.REJECTED,
        passed=False,
        failed_layer="MARKET_REGIME",
        rejection_reason="ADX below trending threshold.",
        stages=[],
        market_context=market_context,
    )


def _preview_result(symbol="BTC-USDT") -> PreviewAnalysisResult:
    return PreviewAnalysisResult(
        symbol=symbol,
        preview_direction="BUY",
        preview_progress_raw_score=40.0,
        preview_progress_max_score=120.0,
        preview_progress_percentage=33,
        preview_completed_layers=["MARKET_REGIME", "HTF_BIAS"],
        preview_failed_layers=["LIQUIDITY_SWEEP"],
        preview_data_availability={"LIQUIDITY_SWEEP": PreviewLayerStatus.FAILED},
    )


def _build_scanner(
    strategy_engine=None,
    duplicate_guard=None,
    semaphore=None,
    preview_analyzer=None,
    on_pair_result=None,
) -> PairScanner:
    return PairScanner(
        strategy_engine=strategy_engine or MagicMock(),
        duplicate_guard=duplicate_guard or DuplicateSignalGuard(retention_seconds=3600, maximum_entries=1000),
        semaphore=semaphore or asyncio.Semaphore(5),
        preview_analyzer=preview_analyzer,
        on_pair_result=on_pair_result,
    )


async def _scan(scanner: PairScanner, symbol="BTC-USDT"):
    return await scanner.scan_pair(
        symbol=symbol,
        account_balance=10000.0,
        active_trade_count=0,
        active_positions=[],
        active_position_candles={},
        detection_time_utc=UTC_NOW,
    )


class TestPairScannerOutcomes:
    async def test_valid_premium_result(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(
            return_value=_valid_pipeline_result(classification=ConfidenceClassification.PREMIUM)
        )
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        assert result.status == PairScanStatus.VALID

    async def test_valid_strong_result(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(
            return_value=_valid_pipeline_result(classification=ConfidenceClassification.STRONG)
        )
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        assert result.status == PairScanStatus.VALID

    async def test_medium_result_does_not_become_valid_candidate(self):
        # StrategyPipelineResult itself guarantees status=VALID implies a
        # publishable PREMIUM/STRONG confidence result, so a MEDIUM/IGNORE
        # classification always carries status=REJECTED in practice. Confirm
        # the pair scanner never reports such a result as VALID.
        pipeline_result = StrategyPipelineResult(
            symbol="BTC-USDT",
            expected_direction="BUY",
            detection_time_utc=UTC_NOW,
            status=PipelineStatus.REJECTED,
            passed=False,
            failed_layer="CONFIDENCE_SCORING",
            rejection_reason="Confidence classification is MEDIUM.",
            stages=[],
            confidence_result=_confidence(
                classification=ConfidenceClassification.MEDIUM, publishable=False
            ),
        )
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=pipeline_result)
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        assert result.status != PairScanStatus.VALID
        assert result.status == PairScanStatus.REJECTED

    async def test_rejected_pipeline_result(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_rejected_pipeline_result())
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        assert result.status == PairScanStatus.REJECTED
        assert result.reason == "ADX below trending threshold."

    async def test_error_pipeline_result(self):
        error_result = StrategyPipelineResult(
            symbol="BTC-USDT",
            expected_direction=None,
            detection_time_utc=UTC_NOW,
            status=PipelineStatus.ERROR,
            passed=False,
            stages=[],
        )
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=error_result)
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        assert result.status == PairScanStatus.ERROR

    async def test_duplicate_valid_result(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_pipeline_result())
        guard = DuplicateSignalGuard(retention_seconds=3600, maximum_entries=1000)
        scanner = _build_scanner(strategy_engine=engine, duplicate_guard=guard)
        first = await _scan(scanner)
        second = await _scan(scanner)
        assert first.status == PairScanStatus.VALID
        assert second.status == PairScanStatus.DUPLICATE
        assert second.duplicate is True

    async def test_new_valid_result(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_pipeline_result())
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        assert result.status == PairScanStatus.VALID
        assert result.duplicate is False

    async def test_unexpected_engine_exception_converted_to_error(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(side_effect=RuntimeError("boom"))
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        assert result.status == PairScanStatus.ERROR
        assert result.error_type == "RuntimeError"

    async def test_pipeline_data_unavailable_error_converted_to_error(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(
            side_effect=PipelineDataUnavailableError(
                layer_name="MARKET_DATA_PREPARATION", symbol="BTC-USDT", reason="no data"
            )
        )
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        assert result.status == PairScanStatus.ERROR
        assert result.error_type == "PipelineDataUnavailableError"


class TestPairScannerConcurrency:
    async def test_semaphore_is_acquired(self):
        semaphore = asyncio.Semaphore(1)
        engine = MagicMock()

        acquired_during_call = False

        async def _analyze(**kwargs):
            nonlocal acquired_during_call
            acquired_during_call = semaphore.locked()
            return _valid_pipeline_result()

        engine.analyze_symbol = AsyncMock(side_effect=_analyze)
        scanner = _build_scanner(strategy_engine=engine, semaphore=semaphore)
        await _scan(scanner)
        assert acquired_during_call is True
        assert not semaphore.locked()

    async def test_semaphore_released_after_exception(self):
        semaphore = asyncio.Semaphore(1)
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(side_effect=RuntimeError("boom"))
        scanner = _build_scanner(strategy_engine=engine, semaphore=semaphore)
        await _scan(scanner)
        assert not semaphore.locked()


class TestPairScannerMetadata:
    async def test_duration_recorded(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_pipeline_result())
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        assert result.duration_ms >= 0.0
        assert result.completed_at_utc >= result.started_at_utc

    async def test_no_persistence_or_publishing_side_effect(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_pipeline_result())
        scanner = _build_scanner(strategy_engine=engine)
        result = await _scan(scanner)
        result_fields = set(type(result).model_fields.keys())
        forbidden = {"telegram_status", "dashboard_status", "exchange_order_id", "persisted"}
        assert result_fields.isdisjoint(forbidden)


class TestPreviewAnalyzerWiring:
    async def test_preview_computed_only_for_rejected_result(self):
        pipeline_result = _rejected_pipeline_result(market_context=_market_context())
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=pipeline_result)

        preview_analyzer = MagicMock()
        preview_analyzer.analyze = MagicMock(return_value=_preview_result())

        scanner = _build_scanner(strategy_engine=engine, preview_analyzer=preview_analyzer)
        result = await _scan(scanner)

        preview_analyzer.analyze.assert_called_once_with(pipeline_result.market_context)
        assert result.preview_result is not None
        assert result.preview_result.preview_direction == "BUY"

    async def test_preview_not_computed_for_valid_result(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_pipeline_result())

        preview_analyzer = MagicMock()
        preview_analyzer.analyze = MagicMock(return_value=_preview_result())

        scanner = _build_scanner(strategy_engine=engine, preview_analyzer=preview_analyzer)
        result = await _scan(scanner)

        preview_analyzer.analyze.assert_not_called()
        assert result.status == PairScanStatus.VALID
        assert result.preview_result is None

    async def test_preview_not_computed_for_error_result_without_market_context(self):
        # An unhandled exception (never reaching a StrategyPipelineResult
        # at all) has no market_context to reuse, so no preview can be
        # computed.
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(side_effect=RuntimeError("boom"))

        preview_analyzer = MagicMock()
        preview_analyzer.analyze = MagicMock(return_value=_preview_result())

        scanner = _build_scanner(strategy_engine=engine, preview_analyzer=preview_analyzer)
        result = await _scan(scanner)

        preview_analyzer.analyze.assert_not_called()
        assert result.status == PairScanStatus.ERROR
        assert result.preview_result is None

    async def test_preview_computed_for_error_result_with_market_context(self):
        # A PipelineStatus.ERROR result that DID reach market-data
        # preparation (market_context is populated) should still get a
        # preview score, the same as a REJECTED result -- an ERROR
        # status must not silently fall back to no score when the
        # necessary inputs are actually available.
        error_result = StrategyPipelineResult(
            symbol="BTC-USDT",
            expected_direction=None,
            detection_time_utc=UTC_NOW,
            status=PipelineStatus.ERROR,
            passed=False,
            rejection_reason="Downstream calculation error.",
            stages=[],
            market_context=_market_context(),
        )
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=error_result)

        preview_analyzer = MagicMock()
        preview_analyzer.analyze = MagicMock(return_value=_preview_result())

        scanner = _build_scanner(strategy_engine=engine, preview_analyzer=preview_analyzer)
        result = await _scan(scanner)

        preview_analyzer.analyze.assert_called_once_with(error_result.market_context)
        assert result.status == PairScanStatus.ERROR
        assert result.preview_result is not None
        assert result.preview_result.preview_direction == "BUY"

    async def test_no_preview_analyzer_configured_leaves_preview_result_none(self):
        pipeline_result = _rejected_pipeline_result(market_context=_market_context())
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=pipeline_result)

        scanner = _build_scanner(strategy_engine=engine, preview_analyzer=None)
        result = await _scan(scanner)

        assert result.status == PairScanStatus.REJECTED
        assert result.preview_result is None

    async def test_rejected_result_has_no_market_context_skips_preview(self):
        pipeline_result = _rejected_pipeline_result(market_context=None)
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=pipeline_result)

        preview_analyzer = MagicMock()
        preview_analyzer.analyze = MagicMock(return_value=_preview_result())

        scanner = _build_scanner(strategy_engine=engine, preview_analyzer=preview_analyzer)
        result = await _scan(scanner)

        preview_analyzer.analyze.assert_not_called()
        assert result.preview_result is None

    async def test_preview_analyzer_exception_never_affects_real_result(self):
        pipeline_result = _rejected_pipeline_result(market_context=_market_context())
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=pipeline_result)

        preview_analyzer = MagicMock()
        preview_analyzer.analyze = MagicMock(side_effect=RuntimeError("preview boom"))

        scanner = _build_scanner(strategy_engine=engine, preview_analyzer=preview_analyzer)
        result = await _scan(scanner)  # must not raise

        assert result.status == PairScanStatus.REJECTED
        assert result.reason == "ADX below trending threshold."
        assert result.preview_result is None

    async def test_preview_result_never_changes_pair_scan_status(self):
        pipeline_result = _rejected_pipeline_result(market_context=_market_context())
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=pipeline_result)

        preview_analyzer = MagicMock()
        preview_analyzer.analyze = MagicMock(return_value=_preview_result())

        scanner = _build_scanner(strategy_engine=engine, preview_analyzer=preview_analyzer)
        result = await _scan(scanner)

        # Even though the preview independently resolved BUY with 33%
        # progress, the real PairScanResult remains REJECTED -- a preview
        # can never mark a rejected pipeline as valid.
        assert result.status == PairScanStatus.REJECTED
        assert result.status != PairScanStatus.VALID
        assert result.duplicate is False
        assert result.duplicate_key is None


class TestOnPairResultCallback:
    async def test_callback_invoked_with_the_final_result(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_pipeline_result())

        received = []

        async def _on_pair_result(result):
            received.append(result)

        scanner = _build_scanner(strategy_engine=engine, on_pair_result=_on_pair_result)
        result = await _scan(scanner)

        assert received == [result]

    async def test_callback_invoked_for_every_status_including_error(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(side_effect=RuntimeError("boom"))

        received = []

        async def _on_pair_result(result):
            received.append(result)

        scanner = _build_scanner(strategy_engine=engine, on_pair_result=_on_pair_result)
        result = await _scan(scanner)

        assert len(received) == 1
        assert received[0].status == PairScanStatus.ERROR

    async def test_callback_exception_never_affects_the_returned_result(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_pipeline_result())

        async def _on_pair_result(result):
            raise RuntimeError("observer boom")

        scanner = _build_scanner(strategy_engine=engine, on_pair_result=_on_pair_result)
        result = await _scan(scanner)  # must not raise

        assert result.status == PairScanStatus.VALID

    async def test_no_callback_configured_is_a_no_op(self):
        engine = MagicMock()
        engine.analyze_symbol = AsyncMock(return_value=_valid_pipeline_result())

        scanner = _build_scanner(strategy_engine=engine, on_pair_result=None)
        result = await _scan(scanner)  # must not raise

        assert result.status == PairScanStatus.VALID
