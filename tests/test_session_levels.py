"""
Unit tests for app.liquidity.session_levels.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.liquidity.equal_high_low import LiquidityCalculationError
from app.liquidity.results import LiquidityStrength, LiquidityType, SessionName
from app.liquidity.session_levels import SessionLiquidityDetector
from app.models.candle import Candle


def _candle(timestamp: datetime, high: float, low: float) -> Candle:
    close = (high + low) / 2
    return Candle(
        timestamp=timestamp,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        symbol="BTC-USDT",
        timeframe="15m",
    )


def _hourly_candles(start: datetime, hours: int, base_price: float) -> list[Candle]:
    candles = []
    for h in range(hours):
        ts = start + timedelta(hours=h)
        price = base_price + h
        candles.append(_candle(ts, price + 1, price - 1))
    return candles


class TestSessionLiquidityDetector:
    def test_completed_asia_session_high_and_low(self):
        day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        candles = _hourly_candles(day, 8, 100.0)  # 00:00-08:00 Asia session
        reference = day + timedelta(hours=9)

        levels = SessionLiquidityDetector().detect_completed_session_levels(candles, reference)
        asia_levels = [l for l in levels if l.metadata and l.metadata.get("session") == "ASIA"]
        assert len(asia_levels) == 2
        types = {l.liquidity_type for l in asia_levels}
        assert types == {LiquidityType.SESSION_HIGH, LiquidityType.SESSION_LOW}

    def test_completed_london_session_high_and_low(self):
        day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        candles = _hourly_candles(day.replace(hour=8), 8, 100.0)  # 08:00-16:00
        reference = day.replace(hour=17)

        levels = SessionLiquidityDetector().detect_completed_session_levels(candles, reference)
        london_levels = [
            l for l in levels if l.metadata and l.metadata.get("session") == "LONDON"
        ]
        assert len(london_levels) == 2

    def test_completed_new_york_session_high_and_low(self):
        day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        candles = _hourly_candles(day.replace(hour=13), 8, 100.0)  # 13:00-21:00
        reference = day.replace(hour=22)

        levels = SessionLiquidityDetector().detect_completed_session_levels(candles, reference)
        ny_levels = [
            l for l in levels if l.metadata and l.metadata.get("session") == "NEW_YORK"
        ]
        assert len(ny_levels) == 2

    def test_active_session_excluded(self):
        day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        candles = _hourly_candles(day, 4, 100.0)  # only partial Asia session so far
        reference = day + timedelta(hours=4)  # still inside the Asia window

        levels = SessionLiquidityDetector().detect_completed_session_levels(candles, reference)
        # The Asia session for "today" hasn't ended yet, and there's no
        # prior-day data, so no Asia level should be produced from it.
        asia_levels = [l for l in levels if l.metadata and l.metadata.get("session") == "ASIA"]
        assert asia_levels == []

    def test_overlapping_sessions_handled_independently(self):
        day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # Cover 00:00 through 21:00 so Asia, London, and New York (which
        # overlaps London 13:00-16:00) are all completed by 22:00.
        candles = _hourly_candles(day, 21, 100.0)
        reference = day.replace(hour=22)

        levels = SessionLiquidityDetector().detect_completed_session_levels(candles, reference)
        sessions_found = {l.metadata["session"] for l in levels}
        assert sessions_found == {"ASIA", "LONDON", "NEW_YORK"}

    def test_utc_timestamps_required(self):
        naive_reference = datetime(2026, 1, 1, 10, 0)
        candles = _hourly_candles(datetime(2026, 1, 1, tzinfo=timezone.utc), 8, 100.0)
        with pytest.raises(LiquidityCalculationError):
            SessionLiquidityDetector().detect_completed_session_levels(
                candles, naive_reference
            )

    def test_source_extreme_timestamps_retained(self):
        day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        candles = _hourly_candles(day, 8, 100.0)
        reference = day + timedelta(hours=9)

        levels = SessionLiquidityDetector().detect_completed_session_levels(candles, reference)
        asia_high = next(
            l
            for l in levels
            if l.metadata.get("session") == "ASIA" and l.liquidity_type == LiquidityType.SESSION_HIGH
        )
        assert len(asia_high.source_timestamps) == 1

    def test_no_trade_permission_logic_exists(self):
        day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        candles = _hourly_candles(day, 8, 100.0)
        reference = day + timedelta(hours=9)
        levels = SessionLiquidityDetector().detect_completed_session_levels(candles, reference)
        for level in levels:
            level_fields = set(type(level).model_fields.keys())
            assert "trading_allowed" not in level_fields
            assert "session_permission" not in level_fields
        assert all(level.strength == LiquidityStrength.STRONG for level in levels)
