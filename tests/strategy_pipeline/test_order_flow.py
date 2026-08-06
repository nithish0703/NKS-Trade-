"""
Unit tests for app.strategy_pipeline.order_flow (Stage 5: OI + CVD).
"""

from datetime import datetime, timedelta, timezone

from app.data.open_interest_point import OpenInterestPoint
from app.models.candle import Candle
from app.strategy_pipeline.order_flow import evaluate_order_flow

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


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


def _bullish(index: int, volume: float = 10.0) -> Candle:
    return _candle(index, 100.0, 101.0, volume)


def _bearish(index: int, volume: float = 10.0) -> Candle:
    return _candle(index, 101.0, 100.0, volume)


def _oi_point(index: int, value: float) -> OpenInterestPoint:
    return OpenInterestPoint(
        symbol="BTC-USDT", timestamp=UTC_NOW + timedelta(minutes=index), open_interest=value
    )


def _cvd_higher_low_buy_candles() -> list[Candle]:
    """Same deterministic higher-low CVD fixture proven in test_cvd.py."""
    deltas = [5, 5, -8, -8, -8, 5, 5, 5, 5, -4, -4, -4, 5, 5]
    return [_bullish(i, volume=d) if d > 0 else _bearish(i, volume=-d) for i, d in enumerate(deltas)]


def _cvd_higher_low_buy_candles_with_net_price_rise() -> list[Candle]:
    """
    The same higher-low CVD delta pattern (volumes only drive CVD, not
    price), but built on a rising absolute price ladder so
    candles[-1].close is genuinely above candles[0].close -- the CVD
    sub-check only reads volume/direction, so the OI sub-check's price
    comparison can be satisfied independently without disturbing the
    already-proven higher-low CVD swing pattern.
    """
    deltas = [5, 5, -8, -8, -8, 5, 5, 5, 5, -4, -4, -4, 5, 5]
    candles = []
    price = 100.0
    for i, d in enumerate(deltas):
        if d > 0:
            candle = _candle(i, price, price + 1.0, volume=d)
        else:
            candle = _candle(i, price + 1.0, price, volume=-d)
        candles.append(candle)
        price += 0.1  # each successive candle sits at a slightly higher price level
    return candles


def _cvd_no_swings_candles_with_net_price_rise() -> list[Candle]:
    """
    Monotonically rising CVD (every candle bullish, so no swing lows
    ever form -> CVD confirmation fails for BUY), built on a rising
    absolute price ladder so candles[-1].close is genuinely above
    candles[0].close (satisfying the OI sub-check's price_change
    requirement independently).
    """
    candles = []
    price = 100.0
    for i in range(14):
        candles.append(_candle(i, price, price + 1.0, volume=10.0))
        price += 0.1
    return candles


class TestEvaluateOrderFlowBothAgree:
    def test_both_oi_and_cvd_confirm_buy(self):
        candles = _cvd_higher_low_buy_candles_with_net_price_rise()
        assert candles[-1].close > candles[0].close, "fixture must have a genuine net price rise"
        oi = [_oi_point(0, 1000.0), _oi_point(13, 1100.0)]

        # left_strength/right_strength=2 matches the swing-detection
        # window this exact 14-candle CVD delta pattern was proven
        # against in test_cvd.py; the module's own default (3) needs a
        # longer series than this fixture provides.
        result = evaluate_order_flow(
            candles, oi, expected_direction="BUY", cvd_left_strength=2, cvd_right_strength=2
        )

        assert result.passed is True
        assert result.open_interest.passed is True
        assert result.cvd.passed is True


class TestEvaluateOrderFlowOneDisagrees:
    def test_oi_disagrees_short_covering_fails_even_with_cvd_agreeing(self):
        candles = _cvd_higher_low_buy_candles_with_net_price_rise()
        # OI falls: short covering, not new buying.
        oi = [_oi_point(0, 1000.0), _oi_point(13, 900.0)]

        result = evaluate_order_flow(
            candles, oi, expected_direction="BUY", cvd_left_strength=2, cvd_right_strength=2
        )

        assert result.passed is False
        assert result.open_interest.passed is False
        assert result.cvd.passed is True
        assert "Open Interest" in result.reason
        assert "CVD" not in result.reason.replace("Open Interest and CVD", "")

    def test_cvd_disagrees_fails_even_with_oi_agreeing(self):
        candles = _cvd_no_swings_candles_with_net_price_rise()
        oi = [_oi_point(0, 1000.0), _oi_point(13, 1100.0)]

        result = evaluate_order_flow(candles, oi, expected_direction="BUY")

        assert result.passed is False
        assert result.open_interest.passed is True
        assert result.cvd.passed is False
        assert "CVD" in result.reason

    def test_both_disagree(self):
        candles = _cvd_no_swings_candles_with_net_price_rise()
        oi = [_oi_point(0, 1000.0), _oi_point(13, 900.0)]

        result = evaluate_order_flow(candles, oi, expected_direction="BUY")

        assert result.passed is False
        assert result.open_interest.passed is False
        assert result.cvd.passed is False
        assert "Open Interest" in result.reason
        assert "CVD" in result.reason


class TestEvaluateOrderFlowBothSubChecksAlwaysRun:
    def test_cvd_result_is_populated_even_when_oi_fails_first(self):
        """Neither sub-check short-circuits the other."""
        candles = _cvd_higher_low_buy_candles()
        oi = [_oi_point(0, 1000.0), _oi_point(13, 900.0)]  # OI fails

        result = evaluate_order_flow(candles, oi, expected_direction="BUY")

        assert result.cvd is not None
        assert result.cvd.cvd_series  # CVD sub-check genuinely ran and produced a series

    def test_open_interest_result_is_populated_even_when_cvd_fails_first(self):
        candles = _cvd_no_swings_candles_with_net_price_rise()
        oi = [_oi_point(0, 1000.0), _oi_point(13, 1100.0)]  # OI passes

        result = evaluate_order_flow(candles, oi, expected_direction="BUY")

        assert result.open_interest is not None
        assert result.open_interest.price_change is not None
