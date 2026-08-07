"""
Unit tests for app.strategy_pipeline.order_flow (Stage 5: Volume Profile + CVD).
"""

from datetime import datetime, timedelta, timezone

from app.models.candle import Candle
from app.strategy_pipeline.confirmation_status import ConfirmationStatus
from app.strategy_pipeline.order_flow import evaluate_order_flow

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

# Volume Profile parameters used throughout: a single price bin makes
# the whole window's HVN sit exactly at the profile's POC, so whether
# the sub-check passes/fails is controlled purely by how large a
# `volume_profile_proximity_ratio` is supplied -- a wide one always
# reaches the POC (CONFIRMED), a vanishingly small one never does
# (DISAGREED, since nothing else is close enough to count as an LVN
# rejection either).
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
    """Deterministic lower-low CVD fixture (genuine DISAGREED for BUY)."""
    return _build_from_deltas([5, 5, -6, -6, -6, 5, 5, 5, 5, -8, -8, -8, 5, 5])


class TestEvaluateOrderFlowBothAgree:
    def test_both_volume_profile_and_cvd_confirm_buy(self):
        candles = _cvd_higher_low_buy_candles()

        result = evaluate_order_flow(
            candles, expected_direction="BUY", cvd_left_strength=2, cvd_right_strength=2, **_VP_REACHES_POC
        )

        assert result.passed is True
        assert result.status == ConfirmationStatus.CONFIRMED
        assert result.volume_profile.passed is True
        assert result.cvd.passed is True


class TestEvaluateOrderFlowOneDisagrees:
    def test_volume_profile_disagrees_fails_even_with_cvd_agreeing(self):
        candles = _cvd_higher_low_buy_candles()

        result = evaluate_order_flow(
            candles, expected_direction="BUY", cvd_left_strength=2, cvd_right_strength=2, **_VP_NEVER_REACHES
        )

        assert result.passed is False
        assert result.status == ConfirmationStatus.DISAGREED
        assert result.volume_profile.passed is False
        assert result.cvd.passed is True
        assert "Volume Profile" in result.reason
        assert "CVD" not in result.reason.replace("Volume Profile and CVD", "")
        assert "ORDER_FLOW_DISAGREED" in result.reason

    def test_cvd_disagrees_fails_even_with_volume_profile_agreeing(self):
        candles = _cvd_lower_low_candles()

        result = evaluate_order_flow(
            candles, expected_direction="BUY", cvd_left_strength=2, cvd_right_strength=2, **_VP_REACHES_POC
        )

        assert result.passed is False
        assert result.status == ConfirmationStatus.DISAGREED
        assert result.volume_profile.passed is True
        assert result.cvd.passed is False
        assert result.cvd.status == ConfirmationStatus.DISAGREED
        assert "CVD" in result.reason
        assert "ORDER_FLOW_DISAGREED" in result.reason

    def test_both_disagree(self):
        candles = _cvd_lower_low_candles()

        result = evaluate_order_flow(
            candles, expected_direction="BUY", cvd_left_strength=2, cvd_right_strength=2, **_VP_NEVER_REACHES
        )

        assert result.passed is False
        assert result.status == ConfirmationStatus.DISAGREED
        assert result.volume_profile.passed is False
        assert result.cvd.passed is False
        assert "Volume Profile" in result.reason
        assert "CVD" in result.reason
        assert "ORDER_FLOW_DISAGREED" in result.reason


class TestEvaluateOrderFlowDataUnavailable:
    """
    Neither sub-check disagreeing but at least one lacking enough data
    to reach a conclusion must reject with a status/reason distinct
    from a genuine market disagreement -- these must never collapse
    into the same outcome.
    """

    def test_cvd_unavailable_no_swings_fails_distinctly_from_disagreement(self):
        candles = _cvd_no_swings_candles()

        result = evaluate_order_flow(candles, expected_direction="BUY", **_VP_REACHES_POC)

        assert result.passed is False
        assert result.status == ConfirmationStatus.UNAVAILABLE
        assert result.cvd.status == ConfirmationStatus.UNAVAILABLE
        assert "ORDER_FLOW_DATA_UNAVAILABLE" in result.reason
        assert "ORDER_FLOW_DISAGREED" not in result.reason
        assert "CVD" in result.reason

    def test_volume_profile_disabled_is_unavailable(self):
        candles = _cvd_higher_low_buy_candles()

        result = evaluate_order_flow(
            candles,
            expected_direction="BUY",
            cvd_left_strength=2,
            cvd_right_strength=2,
            volume_profile_enabled=False,
        )

        assert result.passed is False
        assert result.status == ConfirmationStatus.UNAVAILABLE
        assert result.volume_profile.status == ConfirmationStatus.UNAVAILABLE
        assert "ORDER_FLOW_DATA_UNAVAILABLE" in result.reason
        assert "Volume Profile" in result.reason

    def test_insufficient_candle_history_is_unavailable(self):
        candles = _cvd_higher_low_buy_candles()[:1]

        result = evaluate_order_flow(candles, expected_direction="BUY", **_VP_REACHES_POC)

        assert result.passed is False
        assert result.status == ConfirmationStatus.UNAVAILABLE
        assert result.volume_profile.status == ConfirmationStatus.UNAVAILABLE
        assert result.cvd.status == ConfirmationStatus.UNAVAILABLE


class TestEvaluateOrderFlowBothSubChecksAlwaysRun:
    def test_cvd_result_is_populated_even_when_volume_profile_fails_first(self):
        """Neither sub-check short-circuits the other."""
        candles = _cvd_higher_low_buy_candles()

        result = evaluate_order_flow(
            candles, expected_direction="BUY", cvd_left_strength=2, cvd_right_strength=2, **_VP_NEVER_REACHES
        )

        assert result.cvd is not None
        assert result.cvd.cvd_series  # CVD sub-check genuinely ran and produced a series

    def test_volume_profile_result_is_populated_even_when_cvd_fails_first(self):
        candles = _cvd_no_swings_candles()

        result = evaluate_order_flow(candles, expected_direction="BUY", **_VP_REACHES_POC)

        assert result.volume_profile is not None
        assert result.volume_profile.distance_to_poc is not None
