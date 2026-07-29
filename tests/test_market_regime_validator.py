"""
Unit tests for app.validators.market_regime.MarketRegimeValidator.
"""

from app.indicators.results import IndicatorSnapshot
from app.validators.market_regime import MarketRegimeValidator
from app.validators.results import MarketRegimeStatus


def _snapshot(**overrides) -> IndicatorSnapshot:
    fields = dict(
        adx=30.0,
        ema200_slope=0.01,
        ema200_slope_direction="BULLISH",
        atr=10.0,
        atr_expansion_ratio=1.5,
    )
    fields.update(overrides)
    return IndicatorSnapshot(**fields)


def _validator(
    adx_trending_min=25.0, adx_rejection_max=20.0, ema_flat=0.0005, min_atr_expansion=1.0
) -> MarketRegimeValidator:
    return MarketRegimeValidator(
        adx_trending_minimum=adx_trending_min,
        adx_rejection_maximum=adx_rejection_max,
        ema_flat_threshold=ema_flat,
        minimum_atr_expansion_ratio=min_atr_expansion,
    )


class TestMarketRegimeValidator:
    def test_valid_trending_passes(self):
        snapshot = _snapshot(adx=30.0, ema200_slope_direction="BULLISH", atr=10.0, atr_expansion_ratio=1.5)
        result = _validator().validate(snapshot)
        assert result.passed is True
        assert result.score == 0.0

    def test_adx_exactly_25_does_not_pass(self):
        snapshot = _snapshot(adx=25.0)
        result = _validator().validate(snapshot)
        assert result.passed is False
        assert result.rejection_code == "ADX_BELOW_TRENDING_THRESHOLD"

    def test_adx_below_20_fails(self):
        snapshot = _snapshot(adx=15.0)
        result = _validator().validate(snapshot)
        assert result.passed is False

    def test_adx_between_20_and_25_fails(self):
        snapshot = _snapshot(adx=22.0)
        result = _validator().validate(snapshot)
        assert result.passed is False
        evaluation = _validator().evaluate(snapshot)
        assert evaluation.status != MarketRegimeStatus.TRENDING

    def test_flat_ema200_fails(self):
        snapshot = _snapshot(ema200_slope_direction="FLAT")
        result = _validator().validate(snapshot)
        assert result.passed is False
        assert result.rejection_code == "EMA200_FLAT"

    def test_missing_adx_fails(self):
        snapshot = _snapshot(adx=None)
        result = _validator().validate(snapshot)
        assert result.passed is False
        assert result.rejection_code == "MARKET_REGIME_DATA_MISSING"

    def test_missing_atr_fails(self):
        snapshot = _snapshot(atr=None)
        result = _validator().validate(snapshot)
        assert result.passed is False
        assert result.rejection_code == "ATR_INVALID"

    def test_zero_atr_fails(self):
        snapshot = _snapshot(atr=0.0)
        result = _validator().validate(snapshot)
        assert result.passed is False
        assert result.rejection_code == "ATR_INVALID"

    def test_insufficient_atr_expansion_fails(self):
        snapshot = _snapshot(atr_expansion_ratio=0.5)
        result = _validator().validate(snapshot)
        assert result.passed is False
        assert result.rejection_code == "ATR_EXPANSION_INSUFFICIENT"

    def test_bullish_ema_slope_may_pass(self):
        snapshot = _snapshot(ema200_slope_direction="BULLISH")
        result = _validator().validate(snapshot)
        assert result.passed is True

    def test_bearish_ema_slope_may_pass(self):
        snapshot = _snapshot(ema200_slope_direction="BEARISH")
        result = _validator().validate(snapshot)
        assert result.passed is True

    def test_score_remains_zero(self):
        passing = _validator().validate(_snapshot())
        assert passing.score == 0.0
        failing = _validator().validate(_snapshot(adx=None))
        assert failing.score == 0.0

    def test_no_risk_or_signal_fields(self):
        result = _validator().evaluate(_snapshot())
        result_fields = set(type(result).model_fields.keys())
        forbidden = {"stop_loss", "take_profit", "risk_reward_ratio", "confidence_score", "signal_type"}
        assert result_fields.isdisjoint(forbidden)
