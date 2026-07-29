"""
Unit tests for app.liquidity.sweep_detector.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.liquidity.equal_high_low import LiquidityCalculationError
from app.liquidity.results import (
    LiquidityLevel,
    LiquiditySide,
    LiquidityStrength,
    LiquidityType,
    SweepDirection,
)
from app.liquidity.sweep_detector import LiquiditySweepDetector
from app.models.candle import Candle

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(index: int, high: float, low: float, close: float) -> Candle:
    open_price = close
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        symbol="BTC-USDT",
        timeframe="15m",
    )


def _buy_side_level(
    price: float,
    liquidity_type=LiquidityType.EQUAL_HIGH,
    strength=LiquidityStrength.STRONG,
    end_offset: int = 0,
    active: bool = True,
) -> LiquidityLevel:
    ts = UTC_NOW + timedelta(minutes=end_offset)
    return LiquidityLevel(
        liquidity_id=f"buy-{price}-{end_offset}",
        symbol="BTC-USDT",
        timeframe="15m",
        liquidity_type=liquidity_type,
        liquidity_side=LiquiditySide.BUY_SIDE,
        price=price,
        start_timestamp=ts,
        end_timestamp=ts,
        source_timestamps=[ts],
        touch_count=2,
        strength=strength,
        active=active,
    )


def _sell_side_level(
    price: float,
    liquidity_type=LiquidityType.EQUAL_LOW,
    strength=LiquidityStrength.STRONG,
    end_offset: int = 0,
    active: bool = True,
) -> LiquidityLevel:
    ts = UTC_NOW + timedelta(minutes=end_offset)
    return LiquidityLevel(
        liquidity_id=f"sell-{price}-{end_offset}",
        symbol="BTC-USDT",
        timeframe="15m",
        liquidity_type=liquidity_type,
        liquidity_side=LiquiditySide.SELL_SIDE,
        price=price,
        start_timestamp=ts,
        end_timestamp=ts,
        source_timestamps=[ts],
        touch_count=2,
        strength=strength,
        active=active,
    )


def _detector(min_ratio=0.0001, max_reclaim=2) -> LiquiditySweepDetector:
    return LiquiditySweepDetector(
        minimum_penetration_ratio=min_ratio, maximum_reclaim_candles=max_reclaim
    )


class TestBearishSweep:
    def test_bearish_sweep_of_equal_highs_same_candle_reclaim(self):
        level = _buy_side_level(110.0, end_offset=-1)
        # Sweep candle wicks above 110 then closes back below.
        candles = [_candle(0, high=112.0, low=108.0, close=109.0)]
        sweeps = _detector().detect_sweeps(candles, [level])
        assert len(sweeps) == 1
        assert sweeps[0].direction == SweepDirection.BEARISH
        assert sweeps[0].confirmed is True
        assert sweeps[0].reclaimed_level is True

    def test_bearish_sweep_previous_day_high(self):
        level = _buy_side_level(
            110.0, liquidity_type=LiquidityType.PREVIOUS_DAY_HIGH, end_offset=-1
        )
        candles = [_candle(0, high=111.0, low=108.0, close=109.0)]
        sweeps = _detector().detect_sweeps(candles, [level])
        assert len(sweeps) == 1
        assert sweeps[0].direction == SweepDirection.BEARISH

    def test_reclaim_on_next_candle(self):
        level = _buy_side_level(110.0, end_offset=-1)
        candles = [
            _candle(0, high=112.0, low=109.5, close=111.0),  # penetrates, no reclaim yet
            _candle(1, high=111.0, low=105.0, close=106.0),  # reclaims below 110
        ]
        sweeps = _detector(max_reclaim=2).detect_sweeps(candles, [level])
        assert len(sweeps) == 1
        assert sweeps[0].confirmed is True
        assert sweeps[0].sweep_price == pytest.approx(112.0)

    def test_reclaim_within_configured_maximum(self):
        level = _buy_side_level(110.0, end_offset=-1)
        candles = [
            _candle(0, high=112.0, low=109.5, close=111.0),
            _candle(1, high=111.5, low=109.0, close=110.5),
            _candle(2, high=110.5, low=105.0, close=106.0),  # reclaim at +2
        ]
        sweeps = _detector(max_reclaim=2).detect_sweeps(candles, [level])
        assert len(sweeps) == 1
        assert sweeps[0].confirmed is True

    def test_no_reclaim_means_no_confirmed_sweep(self):
        level = _buy_side_level(110.0, end_offset=-1)
        candles = [
            _candle(0, high=112.0, low=109.5, close=111.0),
            _candle(1, high=113.0, low=110.5, close=112.0),
            _candle(2, high=114.0, low=111.0, close=113.0),
        ]
        sweeps = _detector(max_reclaim=2).detect_sweeps(candles, [level])
        assert len(sweeps) == 1
        assert sweeps[0].confirmed is False
        assert sweeps[0].reclaimed_level is False

    def test_simple_touch_is_not_a_sweep(self):
        level = _buy_side_level(110.0, end_offset=-1)
        candles = [_candle(0, high=110.0, low=105.0, close=107.0)]
        sweeps = _detector().detect_sweeps(candles, [level])
        assert sweeps == []

    def test_penetration_below_minimum_is_not_a_sweep(self):
        level = _buy_side_level(110.0, end_offset=-1)
        candles = [_candle(0, high=110.0001, low=105.0, close=107.0)]
        sweeps = _detector(min_ratio=0.01).detect_sweeps(candles, [level])
        assert sweeps == []


class TestBullishSweep:
    def test_bullish_sweep_of_equal_lows(self):
        level = _sell_side_level(90.0, end_offset=-1)
        candles = [_candle(0, high=95.0, low=88.0, close=91.0)]
        sweeps = _detector().detect_sweeps(candles, [level])
        assert len(sweeps) == 1
        assert sweeps[0].direction == SweepDirection.BULLISH
        assert sweeps[0].confirmed is True

    def test_bullish_sweep_previous_day_low(self):
        level = _sell_side_level(
            90.0, liquidity_type=LiquidityType.PREVIOUS_DAY_LOW, end_offset=-1
        )
        candles = [_candle(0, high=95.0, low=89.0, close=91.0)]
        sweeps = _detector().detect_sweeps(candles, [level])
        assert len(sweeps) == 1
        assert sweeps[0].direction == SweepDirection.BULLISH


class TestGuardConditions:
    def test_candles_before_level_creation_are_ignored(self):
        level = _buy_side_level(110.0, end_offset=5)  # level created "in the future"
        candles = [_candle(0, high=112.0, low=108.0, close=109.0)]
        sweeps = _detector().detect_sweeps(candles, [level])
        assert sweeps == []

    def test_weak_levels_ignored(self):
        level = _buy_side_level(110.0, strength=LiquidityStrength.WEAK, end_offset=-1)
        candles = [_candle(0, high=112.0, low=108.0, close=109.0)]
        sweeps = _detector().detect_sweeps(candles, [level])
        assert sweeps == []

    def test_inactive_levels_ignored(self):
        level = _buy_side_level(110.0, active=False, end_offset=-1)
        candles = [_candle(0, high=112.0, low=108.0, close=109.0)]
        sweeps = _detector().detect_sweeps(candles, [level])
        assert sweeps == []

    def test_duplicate_sweep_prevention(self):
        level = _buy_side_level(110.0, end_offset=-1)
        candles = [_candle(0, high=112.0, low=108.0, close=109.0)]
        sweeps = _detector().detect_sweeps(candles, [level, level])
        assert len(sweeps) == 1

    def test_deterministic_sweep_id(self):
        level = _buy_side_level(110.0, end_offset=-1)
        candles = [_candle(0, high=112.0, low=108.0, close=109.0)]
        sweeps_one = _detector().detect_sweeps(candles, [level])
        sweeps_two = _detector().detect_sweeps(candles, [level])
        assert sweeps_one[0].sweep_id == sweeps_two[0].sweep_id

    def test_chronological_result_order(self):
        buy_level = _buy_side_level(110.0, end_offset=-1)
        sell_level = _sell_side_level(90.0, end_offset=-1)
        candles = [
            _candle(0, high=112.0, low=108.0, close=109.0),
            _candle(1, high=100.0, low=88.0, close=95.0),
        ]
        sweeps = _detector().detect_sweeps(candles, [buy_level, sell_level])
        timestamps = [s.sweep_candle_timestamp for s in sweeps]
        assert timestamps == sorted(timestamps)

    def test_latest_confirmed_sweep(self):
        buy_level = _buy_side_level(110.0, end_offset=-1)
        candles = [
            _candle(0, high=112.0, low=108.0, close=109.0),
            _candle(1, high=100.0, low=95.0, close=97.0),
        ]
        latest = _detector().detect_latest_confirmed_sweep(candles, [buy_level])
        assert latest is not None
        assert latest.confirmed is True

    def test_no_confirmed_sweep_returns_none(self):
        buy_level = _buy_side_level(110.0, end_offset=-1)
        candles = [_candle(0, high=110.0, low=105.0, close=107.0)]  # simple touch
        latest = _detector().detect_latest_confirmed_sweep(candles, [buy_level])
        assert latest is None

    def test_input_candles_and_levels_not_mutated(self):
        level = _buy_side_level(110.0, end_offset=-1)
        candles = [_candle(0, high=112.0, low=108.0, close=109.0)]
        candles_snapshot = [c.model_copy() for c in candles]
        level_snapshot = level.model_copy()
        _detector().detect_sweeps(candles, [level])
        assert candles == candles_snapshot
        assert level == level_snapshot

    def test_mixed_symbol_rejection(self):
        level = _buy_side_level(110.0, end_offset=-1)
        candles = [
            _candle(0, high=112.0, low=108.0, close=109.0),
            Candle(
                timestamp=UTC_NOW + timedelta(minutes=1),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=100.0,
                symbol="ETH-USDT",
                timeframe="15m",
            ),
        ]
        with pytest.raises(LiquidityCalculationError):
            _detector().detect_sweeps(candles, [level])

    def test_no_bos_mss_choch_entry_score_or_signal_fields(self):
        level = _buy_side_level(110.0, end_offset=-1)
        candles = [_candle(0, high=112.0, low=108.0, close=109.0)]
        sweeps = _detector().detect_sweeps(candles, [level])
        result_fields = set(type(sweeps[0]).model_fields.keys())
        forbidden = {
            "bos",
            "mss",
            "choch",
            "entry_price",
            "confidence_score",
            "signal_type",
            "stop_loss",
            "take_profit",
        }
        assert result_fields.isdisjoint(forbidden)

    def test_constructor_validation(self):
        with pytest.raises(LiquidityCalculationError):
            LiquiditySweepDetector(minimum_penetration_ratio=-0.01, maximum_reclaim_candles=2)
        with pytest.raises(LiquidityCalculationError):
            LiquiditySweepDetector(minimum_penetration_ratio=0.0001, maximum_reclaim_candles=0)
