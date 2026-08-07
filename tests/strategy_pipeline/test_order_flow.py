"""
Unit tests for app.strategy_pipeline.order_flow (Stage 5: Volume
Profile + CVD confidence -- a soft confluence signal, never a gate).
"""

from datetime import datetime, timedelta, timezone

from app.models.candle import Candle
from app.strategy_pipeline.order_flow import OrderFlowConfidence, evaluate_order_flow

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

# Volume Profile parameters used throughout: a single price bin makes
# the whole window's HVN sit exactly at the profile's POC, so whether
# the sub-check agrees/disagrees is controlled purely by how large a
# `volume_profile_proximity_ratio` is supplied -- a wide one always
# reaches the POC (agrees), a vanishingly small one never does
# (disagrees).
_VP_REACHES_POC = dict(volume_profile_bins=1, volume_profile_proximity_ratio=0.02)
_VP_NEVER_REACHES = dict(volume_profile_bins=1, volume_profile_proximity_ratio=0.0001)


def _candle(index: int, open_: float, close: float, volume: float = 10.0) -> Candle:
    high = max(open_, close) + 0.5
    low = min(open_, close) - 0.5
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        symbol="BTC-USDT",
        timeframe="15m",
    )


def _build_from_deltas(deltas: list[float], start_price: float = 100.0, step: float = 0.1) -> list[Candle]:
    candles = []
    price = start_price
    for i, d in enumerate(deltas):
        if d > 0:
            candle = _candle(i, price, price + 1.0, volume=d)
        else:
            candle = _candle(i, price + 1.0, price, volume=-d)
        candles.append(candle)
        price += step
    return candles


def _cvd_higher_low_buy_candles() -> list[Candle]:
    """Deterministic higher-low CVD fixture, proven in test_cvd.py."""
    return _build_from_deltas([5, 5, -8, -8, -8, 5, 5, 5, 5, -4, -4, -4, 5, 5])


def _cvd_no_swings_candles() -> list[Candle]:
    """Monotonically rising CVD: every candle bullish, so no swing lows ever form."""
    return _build_from_deltas([10.0] * 14)


def _cvd_lower_low_candles() -> list[Candle]:
    """Deterministic lower-low CVD fixture (genuine disagreement for BUY)."""
    return _build_from_deltas([5, 5, -6, -6, -6, 5, 5, 5, 5, -8, -8, -8, 5, 5])


class TestEvaluateOrderFlowHighConfidence:
    def test_both_volume_profile_and_cvd_agree_gives_high_confidence(self):
        candles = _cvd_higher_low_buy_candles()

        result = evaluate_order_flow(
            candles, expected_direction="BUY", cvd_left_strength=2, cvd_right_strength=2, **_VP_REACHES_POC
        )

        assert result.confidence == OrderFlowConfidence.HIGH
        assert result.passed is True
        assert result.volume_profile.passed is True
        assert result.cvd.passed is True
        assert "HIGH_CONFIDENCE" in result.reason


class TestEvaluateOrderFlowMediumConfidence:
    def test_only_cvd_agrees_gives_medium_confidence(self):
        candles = _cvd_higher_low_buy_candles()

        result = evaluate_order_flow(
            candles, expected_direction="BUY", cvd_left_strength=2, cvd_right_strength=2, **_VP_NEVER_REACHES
        )

        assert result.confidence == OrderFlowConfidence.MEDIUM
        assert result.passed is False
        assert result.volume_profile.passed is False
        assert result.cvd.passed is True
        assert "MEDIUM_CONFIDENCE" in result.reason
        assert "CVD" in result.reason

    def test_only_volume_profile_agrees_gives_medium_confidence(self):
        candles = _cvd_lower_low_candles()

        result = evaluate_order_flow(
            candles, expected_direction="BUY", cvd_left_strength=2, cvd_right_strength=2, **_VP_REACHES_POC
        )

        assert result.confidence == OrderFlowConfidence.MEDIUM
        assert result.passed is False
        assert result.volume_profile.passed is True
        assert result.cvd.passed is False
        assert "MEDIUM_CONFIDENCE" in result.reason
        assert "Volume Profile" in result.reason


class TestEvaluateOrderFlowLowConfidence:
    def test_neither_agrees_gives_low_confidence(self):
        candles = _cvd_lower_low_candles()

        result = evaluate_order_flow(
            candles, expected_direction="BUY", cvd_left_strength=2, cvd_right_strength=2, **_VP_NEVER_REACHES
        )

        assert result.confidence == OrderFlowConfidence.LOW
        assert result.passed is False
        assert result.volume_profile.passed is False
        assert result.cvd.passed is False
        assert "LOW_CONFIDENCE" in result.reason

    def test_cvd_unavailable_and_volume_profile_disagreeing_gives_low_confidence(self):
        # CVD UNAVAILABLE (no swing structure) and Volume Profile not
        # reaching any node both count as "did not agree" -- neither
        # sub-check confirming is LOW regardless of *why* CVD didn't.
        candles = _cvd_no_swings_candles()

        result = evaluate_order_flow(candles, expected_direction="BUY", **_VP_NEVER_REACHES)

        assert result.confidence == OrderFlowConfidence.LOW
        assert result.passed is False
        assert result.cvd.passed is False


class TestEvaluateOrderFlowNeverBlocks:
    def test_low_confidence_still_returns_a_result_not_an_exception(self):
        """Confidence is informational only -- evaluate_order_flow never raises or signals rejection."""
        candles = _cvd_lower_low_candles()

        result = evaluate_order_flow(
            candles, expected_direction="BUY", cvd_left_strength=2, cvd_right_strength=2, **_VP_NEVER_REACHES
        )

        assert result.confidence == OrderFlowConfidence.LOW
        assert result is not None

    def test_cvd_result_is_populated_even_when_volume_profile_disagrees(self):
        """Neither sub-check short-circuits the other."""
        candles = _cvd_higher_low_buy_candles()

        result = evaluate_order_flow(
            candles, expected_direction="BUY", cvd_left_strength=2, cvd_right_strength=2, **_VP_NEVER_REACHES
        )

        assert result.cvd is not None
        assert result.cvd.cvd_series  # CVD sub-check genuinely ran and produced a series

    def test_volume_profile_result_is_populated_even_when_cvd_disagrees(self):
        candles = _cvd_no_swings_candles()

        result = evaluate_order_flow(candles, expected_direction="BUY", **_VP_REACHES_POC)

        assert result.volume_profile is not None
        assert result.volume_profile.distance_to_poc is not None
