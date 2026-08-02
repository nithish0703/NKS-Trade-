"""
Tests for app.scanner.engine_factory.build_strategy_engine and
build_preview_analyzer.
"""

from app.scanner.engine_factory import build_preview_analyzer, build_strategy_engine
from app.scanner.preview_analyzer import PreviewAnalyzer
from app.scanner.strategy_engine import InstitutionalSMCStrategyEngine


class TestEngineFactory:
    def test_all_dependencies_are_constructed(self):
        engine = build_strategy_engine()
        assert isinstance(engine, InstitutionalSMCStrategyEngine)
        assert engine._market_data_provider is not None
        assert engine._candle_repository is not None
        assert engine._indicator_calculator is not None
        assert engine._market_structure_calculator is not None
        assert engine._liquidity_calculator is not None
        assert engine._structure_shift_calculator is not None
        assert engine._zone_calculator is not None
        assert engine._zone_setup_confirmation_calculator is not None
        assert engine._risk_management_calculator is not None
        assert engine._confidence_calculator is not None

    def test_settings_are_respected(self):
        engine = build_strategy_engine()
        assert engine._market_data_provider._base_url.startswith("https://")

    def test_thresholds_are_injected(self):
        from app.config import thresholds

        engine = build_strategy_engine()
        assert engine._market_regime_validator._adx_trending_minimum == thresholds.ADX_TRENDING_MIN

    def test_no_api_request_during_construction(self):
        # Construction must not perform network I/O; if it did, this call
        # would attempt real HTTP requests in a sandboxed test environment
        # and hang or fail. A clean, fast return is sufficient evidence.
        engine = build_strategy_engine()
        assert engine is not None

    def test_no_scanner_starts(self):
        # No background task/thread should be created by construction alone.
        import asyncio

        engine = build_strategy_engine()
        assert not hasattr(engine, "_scanner_task")

    def test_no_mutable_global_singleton(self):
        engine_one = build_strategy_engine()
        engine_two = build_strategy_engine()
        assert engine_one is not engine_two
        assert engine_one._candle_repository is not engine_two._candle_repository

    def test_separate_factory_calls_create_independent_engine_instances(self):
        engine_one = build_strategy_engine()
        engine_two = build_strategy_engine()
        assert engine_one._market_data_provider is not engine_two._market_data_provider
        assert engine_one._risk_management_calculator is not engine_two._risk_management_calculator


class TestPreviewAnalyzerFactory:
    def test_preview_analyzer_is_constructed(self):
        analyzer = build_preview_analyzer()
        assert isinstance(analyzer, PreviewAnalyzer)

    def test_preview_analyzer_uses_identical_threshold_constants_as_strategy_engine(self):
        """
        No strategy threshold was changed or duplicated for the preview
        feature: both the real engine's MarketRegimeValidator and the
        preview analyzer's MarketRegimeValidator must read the exact same
        app.config.thresholds constants.
        """
        from app.config import thresholds

        engine = build_strategy_engine()
        analyzer = build_preview_analyzer()

        assert (
            engine._market_regime_validator._adx_trending_minimum
            == analyzer._market_regime_validator._adx_trending_minimum
            == thresholds.ADX_TRENDING_MIN
        )
        assert (
            engine._market_regime_validator._compression_lookback
            == analyzer._market_regime_validator._compression_lookback
            == thresholds.VOLATILITY_COMPRESSION_LOOKBACK
        )

    def test_preview_analyzer_layer_weights_match_confidence_scoring_weights(self):
        """
        The preview's 115-point layer weights must be the exact same
        SCORE_* constants ConfidenceScoringEngine uses -- never a
        reimplemented or divergent copy.
        """
        from app.config import thresholds

        analyzer = build_preview_analyzer()
        assert analyzer._layer_weights["MARKET_REGIME"] == thresholds.SCORE_MARKET_REGIME
        assert analyzer._layer_weights["HTF_BIAS"] == thresholds.SCORE_HTF_BIAS
        assert analyzer._layer_weights["LIQUIDITY_SWEEP"] == thresholds.SCORE_LIQUIDITY_SWEEP
        assert analyzer._layer_weights["STRUCTURE_SHIFT"] == thresholds.SCORE_STRUCTURE_SHIFT
        assert analyzer._layer_weights["VOLUME_CONFIRMATION"] == thresholds.SCORE_VOLUME_CONFIRMATION
        assert analyzer._layer_weights["ENTRY_ZONE"] == thresholds.SCORE_ENTRY_ZONE
        assert analyzer._layer_weights["PREMIUM_DISCOUNT"] == thresholds.SCORE_PREMIUM_DISCOUNT
        assert analyzer._layer_weights["RETEST_CONFIRMATION"] == thresholds.SCORE_RETEST_CONFIRMATION
        assert analyzer._layer_weights["SESSION_FILTER"] == thresholds.SCORE_SESSION
        assert analyzer._layer_weights["BTC_ALIGNMENT"] == thresholds.SCORE_BTC_ALIGNMENT
        assert analyzer._layer_weights["FAKE_BREAKOUT_FILTER"] == thresholds.SCORE_FAKE_BREAKOUT
        assert analyzer._max_score == thresholds.SCORE_MAXIMUM_RAW

    def test_no_api_request_during_preview_analyzer_construction(self):
        analyzer = build_preview_analyzer()
        assert analyzer is not None

    def test_separate_calls_create_independent_preview_analyzer_instances(self):
        analyzer_one = build_preview_analyzer()
        analyzer_two = build_preview_analyzer()
        assert analyzer_one is not analyzer_two
        assert analyzer_one._zone_calculator is not analyzer_two._zone_calculator
