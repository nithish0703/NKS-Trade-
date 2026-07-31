"""
Unit tests for app.strategy_v2.liquidity_sweep (Check 2: Valid Liquidity Sweep).
"""

from datetime import datetime, timedelta, timezone

from app.liquidity.results import (
    LiquidityLevel,
    LiquiditySide,
    LiquidityStrength,
    LiquiditySweepResult,
    LiquidityType,
    SweepDirection,
)
from app.models.candle import Candle
from app.strategy_v2.liquidity_sweep import validate_liquidity_sweep

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        timestamp=UTC_NOW + timedelta(minutes=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        symbol="BTC-USDT",
        timeframe="15m",
    )


def _level(price: float, side: LiquiditySide) -> LiquidityLevel:
    ts = UTC_NOW
    return LiquidityLevel(
        liquidity_id="level-1",
        symbol="BTC-USDT",
        timeframe="15m",
        liquidity_type=LiquidityType.MAJOR_SWING_HIGH if side == LiquiditySide.BUY_SIDE else LiquidityType.MAJOR_SWING_LOW,
        liquidity_side=side,
        price=price,
        start_timestamp=ts,
        end_timestamp=ts,
        source_timestamps=[ts],
        touch_count=2,
        strength=LiquidityStrength.STRONG,
        active=True,
    )


def _sweep(
    *,
    direction: SweepDirection,
    sweep_candle_index: int,
    sweep_candle_timestamp: datetime,
    sweep_price: float,
    close_price: float,
    confirmed: bool = True,
    level_price: float = 100.0,
) -> LiquiditySweepResult:
    side = LiquiditySide.BUY_SIDE if direction == SweepDirection.BEARISH else LiquiditySide.SELL_SIDE
    return LiquiditySweepResult(
        sweep_id="sweep-1",
        symbol="BTC-USDT",
        timeframe="15m",
        direction=direction,
        liquidity_level=_level(level_price, side),
        sweep_candle_timestamp=sweep_candle_timestamp,
        sweep_candle_index=sweep_candle_index,
        sweep_price=sweep_price,
        close_price=close_price,
        penetration_distance=1.0,
        penetration_ratio=0.01,
        reclaimed_level=confirmed,
        confirmed=confirmed,
        reason="test sweep",
    )


class TestValidateLiquiditySweep:
    def test_no_sweep_fails(self):
        result = validate_liquidity_sweep(None, [], expected_direction="BUY")
        assert result.passed is False

    def test_unconfirmed_sweep_fails(self):
        sweep = _sweep(
            direction=SweepDirection.BULLISH,
            sweep_candle_index=10,
            sweep_candle_timestamp=UTC_NOW + timedelta(minutes=10),
            sweep_price=95.0,
            close_price=101.0,
            confirmed=False,
        )
        result = validate_liquidity_sweep(sweep, [], expected_direction="BUY")
        assert result.passed is False
        assert "not confirmed" in result.reason.lower()

    def test_direction_mismatch_fails(self):
        sweep = _sweep(
            direction=SweepDirection.BEARISH,
            sweep_candle_index=10,
            sweep_candle_timestamp=UTC_NOW + timedelta(minutes=10),
            sweep_price=105.0,
            close_price=99.0,
        )
        result = validate_liquidity_sweep(sweep, [], expected_direction="BUY")
        assert result.passed is False
        assert "direction" in result.reason.lower()

    def test_valid_bullish_sweep_wick_larger_than_body_closes_inside_range(self):
        # 10 prior candles ranging 98-102, then a sweep candle that
        # wicks down to 90 (large lower wick) and closes back at 100
        # (small body, well inside the prior range).
        candles = [_candle(i, 100, 102, 98, 100) for i in range(10)]
        sweep_candle = _candle(10, 99.5, 100.5, 90.0, 100.0)  # lower_wick=9.5, body=0.5
        candles.append(sweep_candle)

        sweep = _sweep(
            direction=SweepDirection.BULLISH,
            sweep_candle_index=10,
            sweep_candle_timestamp=sweep_candle.timestamp,
            sweep_price=90.0,
            close_price=100.0,
        )
        result = validate_liquidity_sweep(sweep, candles, expected_direction="BUY")
        assert result.passed is True
        assert result.wick_size > result.body_size

    def test_valid_bearish_sweep_wick_larger_than_body_closes_inside_range(self):
        candles = [_candle(i, 100, 102, 98, 100) for i in range(10)]
        sweep_candle = _candle(10, 100.5, 110.0, 99.5, 100.0)  # upper_wick=9.5, body=0.5
        candles.append(sweep_candle)

        sweep = _sweep(
            direction=SweepDirection.BEARISH,
            sweep_candle_index=10,
            sweep_candle_timestamp=sweep_candle.timestamp,
            sweep_price=110.0,
            close_price=100.0,
        )
        result = validate_liquidity_sweep(sweep, candles, expected_direction="SELL")
        assert result.passed is True

    def test_body_larger_than_wick_fails(self):
        # Full-bodied breakout candle, not a rejection wick.
        candles = [_candle(i, 100, 102, 98, 100) for i in range(10)]
        sweep_candle = _candle(10, 90.0, 101.0, 89.0, 100.5)  # lower_wick=1.0, body=10.5
        candles.append(sweep_candle)

        sweep = _sweep(
            direction=SweepDirection.BULLISH,
            sweep_candle_index=10,
            sweep_candle_timestamp=sweep_candle.timestamp,
            sweep_price=89.0,
            close_price=100.5,
        )
        result = validate_liquidity_sweep(sweep, candles, expected_direction="BUY")
        assert result.passed is False
        assert "wick" in result.reason.lower()

    def test_close_outside_prior_range_fails(self):
        # Large lower wick (passes wick>body: lower_wick=9.9, body=0.1)
        # but closes at 103, above the prior range [98, 102], instead
        # of back inside it.
        candles = [_candle(i, 100, 102, 98, 100) for i in range(10)]
        sweep_candle = _candle(10, 102.9, 103.0, 90.0, 103.0)
        candles.append(sweep_candle)

        sweep = _sweep(
            direction=SweepDirection.BULLISH,
            sweep_candle_index=10,
            sweep_candle_timestamp=sweep_candle.timestamp,
            sweep_price=90.0,
            close_price=103.0,
        )
        result = validate_liquidity_sweep(sweep, candles, expected_direction="BUY")
        assert result.passed is False
        assert "range" in result.reason.lower()

    def test_insufficient_prior_history_fails(self):
        sweep_candle = _candle(0, 99.5, 100.5, 90.0, 100.0)
        sweep = _sweep(
            direction=SweepDirection.BULLISH,
            sweep_candle_index=0,
            sweep_candle_timestamp=sweep_candle.timestamp,
            sweep_price=90.0,
            close_price=100.0,
        )
        result = validate_liquidity_sweep(sweep, [sweep_candle], expected_direction="BUY")
        assert result.passed is False
        assert "insufficient" in result.reason.lower()

    def test_sweep_candle_not_found_fails(self):
        sweep = _sweep(
            direction=SweepDirection.BULLISH,
            sweep_candle_index=10,
            sweep_candle_timestamp=UTC_NOW + timedelta(minutes=999),
            sweep_price=90.0,
            close_price=100.0,
        )
        candles = [_candle(i, 100, 102, 98, 100) for i in range(10)]
        result = validate_liquidity_sweep(sweep, candles, expected_direction="BUY")
        assert result.passed is False
        assert "located" in result.reason.lower()
