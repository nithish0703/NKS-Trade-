"""
Unit tests for app.liquidity.previous_levels.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.liquidity.equal_high_low import LiquidityCalculationError
from app.liquidity.previous_levels import PreviousPeriodLevelDetector
from app.liquidity.results import LiquidityType
from app.models.candle import Candle


def _candle(timestamp: datetime, high: float, low: float, symbol="BTC-USDT", timeframe="1h") -> Candle:
    close = (high + low) / 2
    return Candle(
        timestamp=timestamp,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        symbol=symbol,
        timeframe=timeframe,
    )


def _hourly_candles_for_day(day: datetime, base_price: float) -> list[Candle]:
    candles = []
    for hour in range(24):
        ts = day.replace(hour=hour, minute=0, second=0, microsecond=0)
        price = base_price + hour
        candles.append(_candle(ts, price + 1, price - 1))
    return candles


class TestPreviousDayLevels:
    def test_previous_day_high_and_low(self):
        reference = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
        previous_day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        candles = _hourly_candles_for_day(previous_day, 100.0)

        levels = PreviousPeriodLevelDetector().detect_previous_day_levels(candles, reference)

        assert len(levels) == 2
        types = {level.liquidity_type for level in levels}
        assert types == {LiquidityType.PREVIOUS_DAY_HIGH, LiquidityType.PREVIOUS_DAY_LOW}

    def test_current_incomplete_day_excluded(self):
        reference = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
        current_day_candles = _hourly_candles_for_day(
            datetime(2026, 1, 2, tzinfo=timezone.utc), 200.0
        )
        levels = PreviousPeriodLevelDetector().detect_previous_day_levels(
            current_day_candles, reference
        )
        assert levels == []

    def test_missing_previous_day_data(self):
        reference = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
        candles = _hourly_candles_for_day(datetime(2026, 1, 1, tzinfo=timezone.utc), 100.0)
        levels = PreviousPeriodLevelDetector().detect_previous_day_levels(candles, reference)
        assert levels == []

    def test_utc_boundary_handling(self):
        reference = datetime(2026, 1, 2, 0, 0, 1, tzinfo=timezone.utc)
        previous_day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        candles = _hourly_candles_for_day(previous_day, 100.0)
        levels = PreviousPeriodLevelDetector().detect_previous_day_levels(candles, reference)
        assert len(levels) == 2


class TestPreviousWeekLevels:
    def test_previous_week_high_and_low(self):
        # 2026-01-05 is a Monday (ISO week 2). Previous ISO week is week 1.
        reference = datetime(2026, 1, 12, 10, 0, tzinfo=timezone.utc)
        candles = []
        for day_offset in range(7):
            day = datetime(2026, 1, 5, tzinfo=timezone.utc) + timedelta(days=day_offset)
            candles.extend(_hourly_candles_for_day(day, 100.0 + day_offset))

        levels = PreviousPeriodLevelDetector().detect_previous_week_levels(candles, reference)
        assert len(levels) == 2
        types = {level.liquidity_type for level in levels}
        assert types == {LiquidityType.PREVIOUS_WEEK_HIGH, LiquidityType.PREVIOUS_WEEK_LOW}

    def test_current_incomplete_week_excluded(self):
        reference = datetime(2026, 1, 12, 10, 0, tzinfo=timezone.utc)
        current_week_candles = _hourly_candles_for_day(
            datetime(2026, 1, 12, tzinfo=timezone.utc), 300.0
        )
        levels = PreviousPeriodLevelDetector().detect_previous_week_levels(
            current_week_candles, reference
        )
        assert levels == []

    def test_missing_previous_week_data(self):
        reference = datetime(2026, 2, 1, tzinfo=timezone.utc)
        candles = _hourly_candles_for_day(datetime(2026, 1, 5, tzinfo=timezone.utc), 100.0)
        levels = PreviousPeriodLevelDetector().detect_previous_week_levels(candles, reference)
        assert levels == []


class TestValidation:
    def test_non_utc_reference_time_rejection(self):
        naive_reference = datetime(2026, 1, 2, 10, 0)
        candles = _hourly_candles_for_day(datetime(2026, 1, 1, tzinfo=timezone.utc), 100.0)
        with pytest.raises(LiquidityCalculationError):
            PreviousPeriodLevelDetector().detect_previous_day_levels(candles, naive_reference)

    def test_mixed_symbol_rejection(self):
        reference = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
        candles = [
            _candle(datetime(2026, 1, 1, 0, tzinfo=timezone.utc), 101, 99, symbol="BTC-USDT"),
            _candle(datetime(2026, 1, 1, 1, tzinfo=timezone.utc), 102, 98, symbol="ETH-USDT"),
        ]
        with pytest.raises(LiquidityCalculationError):
            PreviousPeriodLevelDetector().detect_previous_day_levels(candles, reference)

    def test_non_ascending_candles_rejection(self):
        reference = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
        c1 = _candle(datetime(2026, 1, 1, 0, tzinfo=timezone.utc), 101, 99)
        c2 = _candle(datetime(2026, 1, 1, 1, tzinfo=timezone.utc), 102, 98)
        with pytest.raises(LiquidityCalculationError):
            PreviousPeriodLevelDetector().detect_previous_day_levels([c2, c1], reference)
