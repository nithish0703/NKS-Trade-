"""
Unit tests for app.scoring.input_adapter.ConfidenceInputAdapter.
"""

import pytest

from app.models.validation_result import ValidationResult
from app.scoring.input_adapter import ConfidenceInputAdapter
from app.scoring.score_engine import ConfidenceScoringError, _ALL_LAYERS


def _results() -> dict:
    return {
        "market_regime": ValidationResult.success(layer_name="MARKET_REGIME"),
        "htf_bias": ValidationResult.success(layer_name="HTF_BIAS"),
        "liquidity_sweep": ValidationResult.success(layer_name="LIQUIDITY_SWEEP"),
        "structure_shift": ValidationResult.success(layer_name="STRUCTURE_SHIFT"),
        "volume_confirmation": ValidationResult.success(layer_name="VOLUME_CONFIRMATION"),
        "entry_zone": ValidationResult.success(layer_name="ENTRY_ZONE"),
        "premium_discount": ValidationResult.success(layer_name="PREMIUM_DISCOUNT"),
        "retest_confirmation": ValidationResult.success(layer_name="RETEST_CONFIRMATION"),
        "session_filter": ValidationResult.success(layer_name="SESSION_FILTER"),
        "btc_alignment": ValidationResult.success(layer_name="BTC_ALIGNMENT"),
        "fake_breakout_filter": ValidationResult.success(layer_name="FAKE_BREAKOUT_FILTER"),
    }


class TestConfidenceInputAdapter:
    def test_all_exact_layers_mapped_correctly(self):
        result = ConfidenceInputAdapter().build_validation_map(**_results())
        assert set(result.keys()) == set(_ALL_LAYERS)

    def test_deterministic_ordering(self):
        adapter = ConfidenceInputAdapter()
        result_one = adapter.build_validation_map(**_results())
        result_two = adapter.build_validation_map(**_results())
        assert list(result_one.keys()) == list(result_two.keys())

    def test_missing_required_result_rejected(self):
        kwargs = _results()
        del kwargs["session_filter"]
        with pytest.raises(TypeError):
            ConfidenceInputAdapter().build_validation_map(**kwargs)

    def test_layer_name_mismatch_rejected(self):
        kwargs = _results()
        kwargs["session_filter"] = ValidationResult.success(layer_name="WRONG_NAME")
        with pytest.raises(ConfidenceScoringError):
            ConfidenceInputAdapter().build_validation_map(**kwargs)

    def test_duplicate_logical_layer_rejected(self):
        # Supplying the same ValidationResult layer_name for two different
        # expected slots should be caught as a layer_name mismatch on one
        # of them (since each expected key requires its own exact name).
        kwargs = _results()
        kwargs["htf_bias"] = ValidationResult.success(layer_name="MARKET_REGIME")
        with pytest.raises(ConfidenceScoringError):
            ConfidenceInputAdapter().build_validation_map(**kwargs)

    def test_inputs_not_mutated(self):
        kwargs = _results()
        snapshot = {k: v.model_copy() for k, v in kwargs.items()}
        ConfidenceInputAdapter().build_validation_map(**kwargs)
        assert kwargs == snapshot

    def test_no_validation_logic_recalculated(self):
        kwargs = _results()
        result = ConfidenceInputAdapter().build_validation_map(**kwargs)
        for layer_name, validation_result in result.items():
            assert validation_result.passed is True
            assert validation_result.layer_name == layer_name
