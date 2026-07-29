"""
Tests for the exact strategy-pipeline stage execution order.
"""

from app.scanner.strategy_engine import _MANDATORY_STAGES, _STAGE_DEFINITIONS

EXPECTED_ORDER = [
    "MARKET_REGIME",
    "HTF_BIAS",
    "LIQUIDITY_SWEEP",
    "STRUCTURE_SHIFT",
    "VOLUME_CONFIRMATION",
    "ENTRY_ZONE",
    "PREMIUM_DISCOUNT",
    "RETEST_CONFIRMATION",
    "MOMENTUM_FILTER",
    "VOLATILITY_FILTER",
    "SESSION_FILTER",
    "BTC_ALIGNMENT",
    "FAKE_BREAKOUT_FILTER",
    "CANDLE_QUALITY",
    "ATR",
    "RISK_MANAGEMENT",
    "CONFIDENCE_SCORING",
]


class TestStrategyPipelineOrder:
    def test_deterministic_order(self):
        actual_order = [name for _, name in _STAGE_DEFINITIONS]
        assert actual_order == EXPECTED_ORDER

    def test_every_stage_has_unique_order(self):
        orders = [order for order, _ in _STAGE_DEFINITIONS]
        assert len(orders) == len(set(orders))
        assert orders == list(range(1, len(EXPECTED_ORDER) + 1))

    def test_no_stage_missing(self):
        actual_names = {name for _, name in _STAGE_DEFINITIONS}
        assert actual_names == set(EXPECTED_ORDER)

    def test_no_unexpected_stage_added(self):
        assert len(_STAGE_DEFINITIONS) == len(EXPECTED_ORDER)

    def test_momentum_is_the_only_non_mandatory_stage(self):
        non_mandatory = {name for _, name in _STAGE_DEFINITIONS} - _MANDATORY_STAGES
        assert non_mandatory == {"MOMENTUM_FILTER"}
