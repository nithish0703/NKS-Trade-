"""
Unit tests for app.validators.liquidity_sweep.LiquiditySweepValidator.
"""

from datetime import datetime, timedelta, timezone

from app.liquidity.results import (
    LiquidityLevel,
    LiquiditySide,
    LiquiditySweepResult,
    LiquidityStrength,
    LiquidityType,
    SweepDirection,
)
from app.validators.liquidity_sweep import LiquiditySweepValidator

UTC_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _sweep(
    direction=SweepDirection.BULLISH,
    confirmed=True,
    reclaimed=True,
    strength=LiquidityStrength.STRONG,
) -> LiquiditySweepResult:
    level = LiquidityLevel(
        liquidity_id="level-1",
        symbol="BTC-USDT",
        timeframe="15m",
        liquidity_type=(
            LiquidityType.EQUAL_LOW if direction == SweepDirection.BULLISH else LiquidityType.EQUAL_HIGH
        ),
        liquidity_side=(
            LiquiditySide.SELL_SIDE if direction == SweepDirection.BULLISH else LiquiditySide.BUY_SIDE
        ),
        price=90.0,
        start_timestamp=UTC_NOW - timedelta(minutes=20),
        end_timestamp=UTC_NOW - timedelta(minutes=20),
        source_timestamps=[UTC_NOW - timedelta(minutes=20)],
        touch_count=2,
        strength=strength,
        active=True,
    )
    reclaimed_flag = reclaimed and confirmed
    return LiquiditySweepResult(
        sweep_id="sweep-1",
        symbol="BTC-USDT",
        timeframe="15m",
        direction=direction,
        liquidity_level=level,
        sweep_candle_timestamp=UTC_NOW,
        sweep_candle_index=0,
        sweep_price=89.0,
        close_price=91.0,
        penetration_distance=1.0,
        penetration_ratio=0.01,
        reclaimed_level=reclaimed_flag,
        confirmed=confirmed,
        reason="test sweep",
    )


class TestLiquiditySweepValidator:
    def test_confirmed_bullish_sweep_passes_buy(self):
        result = LiquiditySweepValidator().validate(_sweep(SweepDirection.BULLISH), "BUY")
        assert result.passed is True

    def test_confirmed_bearish_sweep_passes_sell(self):
        result = LiquiditySweepValidator().validate(_sweep(SweepDirection.BEARISH), "SELL")
        assert result.passed is True

    def test_missing_sweep_fails(self):
        result = LiquiditySweepValidator().validate(None, "BUY")
        assert result.passed is False
        assert result.rejection_code == "LIQUIDITY_SWEEP_MISSING"

    def test_unconfirmed_sweep_fails(self):
        result = LiquiditySweepValidator().validate(
            _sweep(SweepDirection.BULLISH, confirmed=False, reclaimed=False), "BUY"
        )
        assert result.passed is False
        assert result.rejection_code == "LIQUIDITY_SWEEP_NOT_CONFIRMED"

    def test_unreclaimed_level_fails(self):
        # Confirmed sweeps require reclaimed_level=True by model invariant,
        # so this scenario is represented via a not-confirmed candidate
        # that also lacks reclaim (the only representable combination).
        result = LiquiditySweepValidator().validate(
            _sweep(SweepDirection.BULLISH, confirmed=False, reclaimed=False), "BUY"
        )
        assert result.passed is False

    def test_direction_mismatch_fails(self):
        result = LiquiditySweepValidator().validate(_sweep(SweepDirection.BEARISH), "BUY")
        assert result.passed is False
        assert result.rejection_code == "LIQUIDITY_SWEEP_DIRECTION_MISMATCH"

    def test_weak_liquidity_fails(self):
        result = LiquiditySweepValidator().validate(
            _sweep(SweepDirection.BULLISH, strength=LiquidityStrength.WEAK), "BUY"
        )
        assert result.passed is False
        assert result.rejection_code == "LIQUIDITY_LEVEL_TOO_WEAK"

    def test_score_remains_zero(self):
        passing = LiquiditySweepValidator().validate(_sweep(SweepDirection.BULLISH), "BUY")
        assert passing.score == 0.0
        failing = LiquiditySweepValidator().validate(None, "BUY")
        assert failing.score == 0.0
