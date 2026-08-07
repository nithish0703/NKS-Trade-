"""
Unit tests for app.strategy_pipeline.volume_profile.
"""

from datetime import datetime, timedelta, timezone

from app.models.candle import Candle
from app.strategy_pipeline.confirmation_status import ConfirmationStatus
from app.strategy_pipeline.volume_profile import (
    VolumeProfileDecision,
    build_volume_profile,
    evaluate_volume_profile_confirmation,
)

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(index: int, low: float, high: float, close: float, volume: float, open_: float = None) -> Candle:
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_ if open_ is not None else close,
        high=high,
        low=low,
        close=close,
        volume=volume,
        symbol="BTC-USDT",
        timeframe="15m",
    )


def _base_profile_candles() -> list[Candle]:
    """
    Three candles, each kept strictly inside one of 3 equal-width price
    bins spanning [100, 103): a dominant middle bin (POC/HVN, ~101.45)
    flanked by two thin bins (LVN, ~100.48 and ~102.42) on either side.
    Ranges are kept off the exact bin boundaries (100/101/102/103) so a
    candle is never split across bins by a boundary landing exactly on
    a bin edge.
    """
    return [
        _candle(0, low=100.0, high=100.9, close=100.5, volume=10.0),
        _candle(1, low=101.0, high=101.9, close=101.5, volume=100.0),
        _candle(2, low=102.0, high=102.9, close=102.5, volume=10.0),
    ]


def _with_current(close: float, low: float, high: float, volume: float = 1.0) -> list[Candle]:
    """Base profile candles plus a small trailing "current price" candle."""
    return _base_profile_candles() + [_candle(3, low=low, high=high, close=close, volume=volume)]


class TestBuildVolumeProfile:
    def test_poc_value_area_and_nodes(self):
        profile = build_volume_profile(_base_profile_candles(), bins=3, value_area_percent=70.0)

        assert profile is not None
        assert profile.poc == 101.45
        assert [node.price for node in profile.hvn_nodes] == [101.45]
        assert sorted(round(node.price, 5) for node in profile.lvn_nodes) == [100.48333, 102.41667]

    def test_insufficient_candles_returns_none(self):
        assert build_volume_profile([_candle(0, 100.0, 101.0, 100.5, 10.0)], bins=3) is None

    def test_flat_price_range_returns_none(self):
        candles = [_candle(i, 100.0, 100.0, 100.0, 10.0) for i in range(3)]
        assert build_volume_profile(candles, bins=3) is None

    def test_zero_volume_returns_none(self):
        candles = [_candle(i, 100.0 + i, 100.9 + i, 100.5 + i, 0.0) for i in range(3)]
        assert build_volume_profile(candles, bins=3) is None


class TestEvaluateVolumeProfileConfirmationDisabledOrUnavailable:
    def test_disabled_is_unavailable(self):
        result = evaluate_volume_profile_confirmation(
            _base_profile_candles(), expected_direction="BUY", enabled=False
        )
        assert result.passed is False
        assert result.status == ConfirmationStatus.UNAVAILABLE
        assert result.decision == VolumeProfileDecision.UNAVAILABLE

    def test_insufficient_candles_is_unavailable(self):
        result = evaluate_volume_profile_confirmation(
            [_candle(0, 100.0, 101.0, 100.5, 10.0)], expected_direction="BUY", bins=3
        )
        assert result.passed is False
        assert result.status == ConfirmationStatus.UNAVAILABLE
        assert result.decision == VolumeProfileDecision.UNAVAILABLE

    def test_flat_price_range_is_unavailable(self):
        candles = [_candle(i, 100.0, 100.0, 100.0, 10.0) for i in range(3)]
        result = evaluate_volume_profile_confirmation(candles, expected_direction="BUY", bins=3)
        assert result.passed is False
        assert result.status == ConfirmationStatus.UNAVAILABLE
        assert result.decision == VolumeProfileDecision.UNAVAILABLE


class TestEvaluateVolumeProfileConfirmationHvn:
    def test_buy_passes_at_hvn_support(self):
        candles = _with_current(close=101.5, low=101.4, high=101.6)

        result = evaluate_volume_profile_confirmation(candles, expected_direction="BUY", bins=3)

        assert result.passed is True
        assert result.status == ConfirmationStatus.CONFIRMED
        assert result.decision == VolumeProfileDecision.PASS_HVN_SUPPORT
        assert result.nearest_hvn == 101.45

    def test_sell_passes_at_hvn_resistance(self):
        candles = _with_current(close=101.4, low=101.3, high=101.45)

        result = evaluate_volume_profile_confirmation(candles, expected_direction="SELL", bins=3)

        assert result.passed is True
        assert result.status == ConfirmationStatus.CONFIRMED
        assert result.decision == VolumeProfileDecision.PASS_HVN_RESISTANCE
        assert result.nearest_hvn == 101.45

    def test_buy_fails_below_hvn_resistance(self):
        # Current price sits just under the HVN, with the node above it.
        candles = _with_current(close=101.4, low=101.3, high=101.45)

        result = evaluate_volume_profile_confirmation(
            candles, expected_direction="BUY", bins=3, proximity_ratio=0.01
        )

        assert result.passed is False
        assert result.status == ConfirmationStatus.DISAGREED
        assert result.decision == VolumeProfileDecision.FAIL_HVN_RESISTANCE

    def test_sell_fails_on_hvn_support(self):
        # Current price sits just above the HVN, with the node below it,
        # and not close enough to the far-side LVN to count as a rejection.
        candles = _with_current(close=101.5, low=101.4, high=101.6)

        result = evaluate_volume_profile_confirmation(candles, expected_direction="SELL", bins=3)

        assert result.passed is False
        assert result.status == ConfirmationStatus.DISAGREED
        assert result.decision == VolumeProfileDecision.FAIL_HVN_SUPPORT


class TestEvaluateVolumeProfileConfirmationLvnRejection:
    def test_sell_passes_on_lvn_rejection_from_above(self):
        # A wider proximity brings the far-side LVN (~102.417) into
        # range of a current price that's inside the value area and
        # has moved down from the previous (base fixture) candle's
        # close of 102.5 -- a rejection back into value from above.
        candles = _with_current(close=101.5, low=101.4, high=101.6)

        result = evaluate_volume_profile_confirmation(
            candles, expected_direction="SELL", bins=3, proximity_ratio=0.01
        )

        assert result.passed is True
        assert result.status == ConfirmationStatus.CONFIRMED
        assert result.decision == VolumeProfileDecision.PASS_LVN_REJECTION


class TestEvaluateVolumeProfileConfirmationNoConfirmation:
    def test_buy_fails_with_no_confirmation_far_from_any_node(self):
        candles = _with_current(close=200.5, low=200.0, high=201.0)

        result = evaluate_volume_profile_confirmation(candles, expected_direction="BUY", bins=3)

        assert result.passed is False
        assert result.status == ConfirmationStatus.DISAGREED
        assert result.decision == VolumeProfileDecision.FAIL_NO_CONFIRMATION

    def test_sell_fails_with_no_confirmation_far_from_any_node(self):
        candles = _with_current(close=200.5, low=200.0, high=201.0)

        result = evaluate_volume_profile_confirmation(candles, expected_direction="SELL", bins=3)

        assert result.passed is False
        assert result.status == ConfirmationStatus.DISAGREED
        assert result.decision == VolumeProfileDecision.FAIL_NO_CONFIRMATION


class TestEvaluateVolumeProfileConfirmationDistances:
    def test_distance_fields_are_populated_when_available(self):
        candles = _base_profile_candles()
        result = evaluate_volume_profile_confirmation(candles, expected_direction="BUY", bins=3)

        assert result.distance_to_poc is not None
        assert result.distance_to_vah is not None
        assert result.distance_to_val is not None
