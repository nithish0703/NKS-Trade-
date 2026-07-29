"""
Tracks trading session-based high and low levels.
"""

from datetime import datetime, time, timedelta, timezone
from typing import Optional, Sequence

from app.models.candle import Candle

from app.liquidity._ids import make_liquidity_id
from app.liquidity.equal_high_low import LiquidityCalculationError
from app.liquidity.results import (
    LiquidityLevel,
    LiquiditySide,
    LiquidityStrength,
    LiquidityType,
    SessionName,
)

# UTC session windows. Windows may overlap and are defined as [start, end).
SESSION_WINDOWS: dict[SessionName, tuple[time, time]] = {
    SessionName.ASIA: (time(0, 0), time(8, 0)),
    SessionName.LONDON: (time(8, 0), time(16, 0)),
    SessionName.NEW_YORK: (time(13, 0), time(21, 0)),
}


def _session_bounds_for_day(
    session_name: SessionName, day: datetime
) -> tuple[datetime, datetime]:
    """Compute the UTC start/end datetimes of a session window on a given day."""
    start_time, end_time = SESSION_WINDOWS[session_name]
    start_dt = datetime.combine(day.date(), start_time, tzinfo=timezone.utc)

    if end_time <= start_time:
        # Session crosses a calendar-day boundary.
        end_dt = datetime.combine(day.date() + timedelta(days=1), end_time, tzinfo=timezone.utc)
    else:
        end_dt = datetime.combine(day.date(), end_time, tzinfo=timezone.utc)

    return start_dt, end_dt


class SessionLiquidityDetector:
    """
    Detects high/low liquidity levels for the most recently completed
    occurrence of each named UTC trading session.
    """

    def detect_completed_session_levels(
        self, candles: Sequence[Candle], reference_time_utc: datetime
    ) -> list[LiquidityLevel]:
        """
        Detect session-high and session-low liquidity levels for the most
        recently completed occurrence of each session (Asia, London,
        New York), relative to reference_time_utc.

        Sessions still in progress at reference_time_utc are skipped for
        that occurrence and never used to calculate a level.

        Raises:
            LiquidityCalculationError: If reference_time_utc is not
                timezone-aware UTC.
        """
        if reference_time_utc.tzinfo is None or reference_time_utc.tzinfo.utcoffset(
            reference_time_utc
        ) is None:
            raise LiquidityCalculationError("reference_time_utc must be timezone-aware UTC.")
        if reference_time_utc.utcoffset() != timezone.utc.utcoffset(reference_time_utc):
            raise LiquidityCalculationError("reference_time_utc must be in UTC.")

        levels: list[LiquidityLevel] = []

        for session_name in SessionName:
            window = self._find_most_recent_completed_window(session_name, reference_time_utc)
            if window is None:
                continue
            start_dt, end_dt = window

            session_candles = [
                c for c in candles if start_dt <= c.timestamp < end_dt
            ]
            if not session_candles:
                continue

            levels.extend(
                self._build_session_levels(session_candles, session_name, start_dt, end_dt)
            )

        return levels

    @staticmethod
    def _find_most_recent_completed_window(
        session_name: SessionName, reference_time_utc: datetime
    ) -> Optional[tuple[datetime, datetime]]:
        # Check today's and the prior day's occurrence, and return the
        # most recent one that has already fully ended.
        for day_offset in (0, 1, 2):
            candidate_day = reference_time_utc - timedelta(days=day_offset)
            start_dt, end_dt = _session_bounds_for_day(session_name, candidate_day)
            if end_dt <= reference_time_utc:
                return start_dt, end_dt
        return None

    @staticmethod
    def _build_session_levels(
        session_candles: list[Candle],
        session_name: SessionName,
        start_dt: datetime,
        end_dt: datetime,
    ) -> list[LiquidityLevel]:
        high_candle = max(session_candles, key=lambda c: c.high)
        low_candle = min(session_candles, key=lambda c: c.low)

        high_level = LiquidityLevel(
            liquidity_id=make_liquidity_id(
                high_candle.symbol,
                high_candle.timeframe,
                LiquidityType.SESSION_HIGH,
                f"{session_name.value}|{start_dt.isoformat()}",
            ),
            symbol=high_candle.symbol,
            timeframe=high_candle.timeframe,
            liquidity_type=LiquidityType.SESSION_HIGH,
            liquidity_side=LiquiditySide.BUY_SIDE,
            price=high_candle.high,
            start_timestamp=start_dt,
            end_timestamp=end_dt,
            source_timestamps=[high_candle.timestamp],
            touch_count=1,
            strength=LiquidityStrength.STRONG,
            active=True,
            metadata={"session": session_name.value},
        )

        low_level = LiquidityLevel(
            liquidity_id=make_liquidity_id(
                low_candle.symbol,
                low_candle.timeframe,
                LiquidityType.SESSION_LOW,
                f"{session_name.value}|{start_dt.isoformat()}",
            ),
            symbol=low_candle.symbol,
            timeframe=low_candle.timeframe,
            liquidity_type=LiquidityType.SESSION_LOW,
            liquidity_side=LiquiditySide.SELL_SIDE,
            price=low_candle.low,
            start_timestamp=start_dt,
            end_timestamp=end_dt,
            source_timestamps=[low_candle.timestamp],
            touch_count=1,
            strength=LiquidityStrength.STRONG,
            active=True,
            metadata={"session": session_name.value},
        )

        return [high_level, low_level]
