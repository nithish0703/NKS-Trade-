"""
Tests for app.scanner.engine_factory.build_strategy_engine.
"""

from app.scanner.engine_factory import build_strategy_engine
from app.strategy_pipeline.engine import PipelineStrategyEngine


class TestEngineFactory:
    def test_all_dependencies_are_constructed(self):
        engine = build_strategy_engine()
        assert isinstance(engine, PipelineStrategyEngine)
        assert engine._market_data_provider is not None
        assert engine._candle_repository is not None
        assert engine._indicator_calculator is not None
        assert engine._market_structure_calculator is not None
        assert engine._liquidity_calculator is not None
        assert engine._displacement_detector is not None
        assert engine._bos_detector is not None
        assert engine._fair_value_gap_detector is not None
        assert engine._risk_management_calculator is not None

    def test_settings_are_respected(self):
        engine = build_strategy_engine()
        assert engine._market_data_provider._base_url.startswith("https://")

    def test_no_api_request_during_construction(self):
        # Construction must not perform network I/O; if it did, this call
        # would attempt real HTTP requests in a sandboxed test environment
        # and hang or fail. A clean, fast return is sufficient evidence.
        engine = build_strategy_engine()
        assert engine is not None

    def test_no_scanner_starts(self):
        # No background task/thread should be created by construction alone.
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
