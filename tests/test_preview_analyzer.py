"""
Tests for app.scanner.preview_analyzer.PreviewAnalyzer.

Uses fully mocked validators/calculators (matching the convention in
test_strategy_engine_rejections.py) so these tests isolate the preview
analyzer's own orchestration (continue-past-failure, graceful
unavailability, scoring) rather than re-testing individual validator
logic, which is already covered by their own dedicated test files.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.market_structure.results import HigherTimeframeBias
from app.models.candle import Candle
from app.models.market_context import MarketContext
from app.models.validation_result import ValidationResult
from app.scanner.preview_analyzer import (
    PREVIEW_LAYER_ORDER,
    PreviewAnalyzer,
    PreviewLayerStatus,
)

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

_LAYER_WEIGHTS = {
    "MARKET_REGIME": 15,
    "HTF_BIAS": 25,
    "LIQUIDITY_SWEEP": 15,
    "STRUCTURE_SHIFT": 15,
    "VOLUME_CONFIRMATION": 10,
    "ENTRY_ZONE": 10,
    "PREMIUM_DISCOUNT": 5,
    "RETEST_CONFIRMATION": 5,
    "ATR": 5,
    "SESSION_FILTER": 5,
    "BTC_ALIGNMENT": 5,
    "FAKE_BREAKOUT_FILTER": 5,
}
_MAX_SCORE = 120.0


def _candle(index: int, symbol="BTC-USDT", timeframe="15m") -> Candle:
    price = 100.0 + index * 0.01
    return Candle(
        timestamp=UTC_NOW - timedelta(minutes=(50 - index)),
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=100.0,
        symbol=symbol,
        timeframe=timeframe,
    )


def _candles(count=50, symbol="BTC-USDT", timeframe="15m") -> list[Candle]:
    return [_candle(i, symbol, timeframe) for i in range(count)]


def _snapshot():
    snapshot = MagicMock()
    snapshot.atr = 5.0
    snapshot.atr_expansion_ratio = 1.5
    snapshot.volume_ema20 = 100.0
    return snapshot


def _structure():
    structure = MagicMock()
    structure.swings = []
    structure.trend_direction.value = "BULLISH"
    return structure


def _htf_bias(final_bias=HigherTimeframeBias.BULLISH):
    bias = MagicMock()
    bias.final_bias = final_bias
    return bias


def _passing_result(layer_name: str) -> ValidationResult:
    return ValidationResult.success(layer_name=layer_name, reason="ok")


def _failing_result(layer_name: str) -> ValidationResult:
    return ValidationResult.failure(layer_name=layer_name, reason="not ok")


def _build_context(
    *,
    htf_bias_result=None,
    indicators_by_timeframe=None,
    structures_by_timeframe=None,
    pipeline_metadata=None,
    candles_by_timeframe=None,
) -> MarketContext:
    return MarketContext(
        symbol="BTC-USDT",
        detection_time_utc=UTC_NOW,
        candles_by_timeframe=candles_by_timeframe or {"15m": _candles()},
        btc_candles_by_timeframe={"15m": _candles(symbol="BTC-USDT")},
        entry_timeframe="15m",
        indicators_by_timeframe=indicators_by_timeframe,
        structures_by_timeframe=structures_by_timeframe,
        htf_bias_result=htf_bias_result,
        pipeline_metadata=pipeline_metadata,
    )


def _build_analyzer(**overrides) -> PreviewAnalyzer:
    """Build a PreviewAnalyzer with fully mocked, default-passing collaborators."""
    liquidity_calculator = overrides.get("liquidity_calculator")
    if liquidity_calculator is None:
        liquidity_calculator = MagicMock()
        liquidity_calculator.detect_levels = MagicMock(return_value=MagicMock())
        liquidity_calculator.detect_sweeps = MagicMock(return_value=[])

    structure_shift_calculator = overrides.get("structure_shift_calculator")
    if structure_shift_calculator is None:
        structure_shift_calculator = MagicMock()
        shift_result = MagicMock()
        selected_break = MagicMock()
        selected_break.displacement = MagicMock()
        shift_result.latest_confirmed_break = selected_break
        shift_result.all_breaks = []
        structure_shift_calculator.calculate = MagicMock(return_value=shift_result)

    zone_calculator = overrides.get("zone_calculator")
    if zone_calculator is None:
        zone_calculator = MagicMock()
        zone_result = MagicMock()
        zone_result.selected_zone = MagicMock()
        zone_calculator.calculate = MagicMock(return_value=zone_result)

    dealing_range_calculator = overrides.get("dealing_range_calculator")
    if dealing_range_calculator is None:
        dealing_range_calculator = MagicMock()
        dealing_range_calculator.calculate = MagicMock(return_value=MagicMock())

    retest_confirmation_detector = overrides.get("retest_confirmation_detector")
    if retest_confirmation_detector is None:
        retest_confirmation_detector = MagicMock()
        retest_confirmation_detector.detect_latest_confirmed = MagicMock(return_value=MagicMock())

    def _validator(name: str):
        validator = MagicMock()
        validator.validate = MagicMock(return_value=_passing_result(name))
        return validator

    return PreviewAnalyzer(
        liquidity_calculator=liquidity_calculator,
        structure_shift_calculator=structure_shift_calculator,
        zone_calculator=zone_calculator,
        dealing_range_calculator=dealing_range_calculator,
        retest_confirmation_detector=retest_confirmation_detector,
        market_regime_validator=overrides.get("market_regime_validator") or _validator("MARKET_REGIME"),
        htf_bias_validator=overrides.get("htf_bias_validator") or _validator("HTF_BIAS"),
        liquidity_sweep_validator=overrides.get("liquidity_sweep_validator")
        or _validator("LIQUIDITY_SWEEP"),
        structure_shift_validator=overrides.get("structure_shift_validator")
        or _validator("structure_shift"),
        volume_confirmation_validator=overrides.get("volume_confirmation_validator")
        or _validator("VOLUME_CONFIRMATION"),
        entry_zone_validator=overrides.get("entry_zone_validator") or _validator("entry_zone"),
        premium_discount_validator=overrides.get("premium_discount_validator")
        or _validator("premium_discount"),
        retest_confirmation_validator=overrides.get("retest_confirmation_validator")
        or _validator("retest_confirmation"),
        atr_validator=overrides.get("atr_validator") or _validator("ATR"),
        session_filter=overrides.get("session_filter") or _validator("SESSION_FILTER"),
        btc_alignment_validator=overrides.get("btc_alignment_validator") or _validator("BTC_ALIGNMENT"),
        fake_breakout_filter=overrides.get("fake_breakout_filter") or _validator("FAKE_BREAKOUT_FILTER"),
        layer_weights=_LAYER_WEIGHTS,
        max_score=_MAX_SCORE,
    )


def _full_context(final_bias=HigherTimeframeBias.BULLISH) -> MarketContext:
    return _build_context(
        htf_bias_result=_htf_bias(final_bias),
        indicators_by_timeframe={"15m": _snapshot()},
        structures_by_timeframe={"15m": _structure()},
        pipeline_metadata={
            "btc_structures_by_timeframe": {"4h": _structure()},
            "btc_htf_bias_result": _htf_bias(final_bias),
        },
    )


class TestPreviewDirectionResolution:
    def test_bullish_htf_bias_maps_to_buy(self):
        analyzer = _build_analyzer()
        result = analyzer.analyze(_full_context(HigherTimeframeBias.BULLISH))
        assert result.preview_direction == "BUY"

    def test_bearish_htf_bias_maps_to_sell(self):
        analyzer = _build_analyzer()
        result = analyzer.analyze(_full_context(HigherTimeframeBias.BEARISH))
        assert result.preview_direction == "SELL"

    def test_mixed_htf_bias_maps_to_null(self):
        analyzer = _build_analyzer()
        result = analyzer.analyze(_full_context(HigherTimeframeBias.MIXED))
        assert result.preview_direction is None

    def test_unknown_htf_bias_maps_to_null(self):
        analyzer = _build_analyzer()
        result = analyzer.analyze(_full_context(HigherTimeframeBias.UNKNOWN))
        assert result.preview_direction is None

    def test_missing_htf_bias_result_maps_to_null(self):
        analyzer = _build_analyzer()
        context = _build_context()  # htf_bias_result=None
        result = analyzer.analyze(context)
        assert result.preview_direction is None

    def test_direction_never_inferred_from_candles_alone(self):
        # Even with a fully populated candle history but no htf_bias_result,
        # direction must remain null -- never inferred from raw candles.
        analyzer = _build_analyzer()
        context = _build_context(candles_by_timeframe={"15m": _candles(count=200)})
        result = analyzer.analyze(context)
        assert result.preview_direction is None


class TestPreviewProgressWhenPipelineFailsFast:
    def test_all_layers_pass_gives_full_score(self):
        analyzer = _build_analyzer()
        result = analyzer.analyze(_full_context())
        assert result.preview_progress_raw_score == _MAX_SCORE
        assert result.preview_progress_percentage == 100
        assert set(result.preview_completed_layers) == set(PREVIEW_LAYER_ORDER)
        assert result.preview_failed_layers == []

    def test_continues_past_a_failed_layer_instead_of_stopping(self):
        # MARKET_REGIME fails (mirroring the real fail-fast rejection
        # point), but the preview must still independently evaluate the
        # remaining layers rather than stopping.
        failing_market_regime = MagicMock()
        failing_market_regime.validate = MagicMock(return_value=_failing_result("MARKET_REGIME"))
        analyzer = _build_analyzer(market_regime_validator=failing_market_regime)

        result = analyzer.analyze(_full_context())

        assert "MARKET_REGIME" in result.preview_failed_layers
        # HTF_BIAS, ATR, and other independently-evaluable layers still ran.
        assert "HTF_BIAS" in result.preview_completed_layers
        assert result.preview_progress_raw_score == _MAX_SCORE - _LAYER_WEIGHTS["MARKET_REGIME"]

    def test_failed_layer_gets_zero_points_not_partial_credit(self):
        failing_atr = MagicMock()
        failing_atr.validate = MagicMock(return_value=_failing_result("ATR"))
        analyzer = _build_analyzer(atr_validator=failing_atr)

        result = analyzer.analyze(_full_context())

        assert "ATR" in result.preview_failed_layers
        assert "ATR" not in result.preview_completed_layers
        assert result.preview_progress_raw_score == _MAX_SCORE - _LAYER_WEIGHTS["ATR"]


class TestPreviewDataAvailability:
    def test_layers_marked_unavailable_when_direction_cannot_be_resolved(self):
        analyzer = _build_analyzer()
        context = _full_context(HigherTimeframeBias.MIXED)
        result = analyzer.analyze(context)

        # Direction-dependent layers are unavailable, not fabricated as
        # failed or passed.
        for layer in (
            "LIQUIDITY_SWEEP",
            "STRUCTURE_SHIFT",
            "ENTRY_ZONE",
            "PREMIUM_DISCOUNT",
            "RETEST_CONFIRMATION",
            "BTC_ALIGNMENT",
            "FAKE_BREAKOUT_FILTER",
        ):
            assert result.preview_data_availability[layer] == PreviewLayerStatus.UNAVAILABLE
            assert layer not in result.preview_completed_layers
            assert layer not in result.preview_failed_layers

    def test_all_layers_unavailable_with_no_market_data(self):
        analyzer = _build_analyzer()
        context = _build_context()  # everything None
        result = analyzer.analyze(context)

        assert result.preview_progress_raw_score == 0.0
        assert result.preview_progress_percentage == 0
        assert result.preview_completed_layers == []
        for layer in PREVIEW_LAYER_ORDER:
            assert result.preview_data_availability[layer] == PreviewLayerStatus.UNAVAILABLE

    def test_never_fabricates_a_passed_result_for_unavailable_data(self):
        analyzer = _build_analyzer()
        context = _build_context()
        result = analyzer.analyze(context)
        assert all(
            status != PreviewLayerStatus.PASSED
            for status in result.preview_data_availability.values()
        )


class TestPreviewNeverRaises:
    def test_calculator_exception_degrades_layer_to_unavailable(self):
        broken_liquidity_calculator = MagicMock()
        broken_liquidity_calculator.detect_levels = MagicMock(side_effect=RuntimeError("boom"))
        analyzer = _build_analyzer(liquidity_calculator=broken_liquidity_calculator)

        result = analyzer.analyze(_full_context())  # must not raise

        assert result.preview_data_availability["LIQUIDITY_SWEEP"] == PreviewLayerStatus.UNAVAILABLE
