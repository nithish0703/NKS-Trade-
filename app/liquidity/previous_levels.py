"""
Tracks previous day/week high and low levels.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Sequence

from app.models.candle import Candle

from app.liquidity._ids import make_liquidity_id
from app.liquidity.equal_high_low import LiquidityCalculationError
from app.liquidity.results import LiquidityLevel, LiquiditySide, LiquidityStrength, LiquidityType


def _validate_candles(candles: Sequence[Candle]) -> None:
    if not candles:
        return
    symbol = candles[0].symbol
    timeframe = candles[0].timeframe
    previous_timestamp = None
    for candle in candles:
        if candle.symbol != symbol:
            raise LiquidityCalculationError("All candles must share the same symbol.")
        if candle.timeframe != timeframe:
            raise LiquidityCalculationError("All candles must share the same timeframe.")
        if previous_timestamp is not None and candle.timestamp <= previous_timestamp:
            raise LiquidityCalculationError(
                "candles must be in strictly ascending chronological order."
            )
        previous_timestamp = candle.timestamp


def _validate_reference_time(reference_time_utc: datetime) -> None:
    if reference_time_utc.tzinfo is None or reference_time_utc.tzinfo.utcoffset(
        reference_time_utc
    ) is None:
        raise LiquidityCalculationError("reference_time_utc must be timezone-aware UTC.")
    if reference_time_utc.utcoffset() != timezone.utc.utcoffset(reference_time_utc):
        raise LiquidityCalculationError("reference_time_utc must be in UTC.")


class PreviousPeriodLevelDetector:
    """
    Detects previous-completed-day and previous-completed-ISO-week high
    and low liquidity levels.
    """

    def detect_previous_day_levels(
        self, candles: Sequence[Candle], reference_time_utc: datetime
    ) -> list[LiquidityLevel]:
        """
        Detect the previous fully completed UTC calendar day's high and
        low liquidity levels.

        Returns an empty list when no candles exist for the previous
        completed day; this condition must be captured by the caller
        rather than fabricated here.

        Raises:
            LiquidityCalculationError: If candles are invalid or
                reference_time_utc is not timezone-aware UTC.
        """
        _validate_candles(candles)
        _validate_reference_time(reference_time_utc)

        if not candles:
            return []

        current_day = reference_time_utc.date()
        previous_day = current_day - timedelta(days=1)

        day_candles = [c for c in candles if c.timestamp.date() == previous_day]
        if not day_candles:
            return []

        return self._build_high_low_levels(
            day_candles,
            LiquidityType.PREVIOUS_DAY_HIGH,
            LiquidityType.PREVIOUS_DAY_LOW,
        )

    def detect_previous_week_levels(
        self, candles: Sequence[Candle], reference_time_utc: datetime
    ) -> list[LiquidityLevel]:
        """
        Detect the previous fully completed ISO week's high and low
        liquidity levels (UTC).

        Returns an empty list when no candles exist for the previous
        completed ISO week.

        Raises:
            LiquidityCalculationError: If candles are invalid or
                reference_time_utc is not timezone-aware UTC.
        """
        _validate_candles(candles)
        _validate_reference_time(reference_time_utc)

        if not candles:
            return []

        current_iso_year, current_iso_week, _ = reference_time_utc.isocalendar()

        week_candles = [
            c
            for c in candles
            if (
                (iso := c.timestamp.isocalendar())[0],
                iso[1],
            )
            == self._previous_iso_week(current_iso_year, current_iso_week)
        ]
        if not week_candles:
            return []

        return self._build_high_low_levels(
            week_candles,
            LiquidityType.PREVIOUS_WEEK_HIGH,
            LiquidityType.PREVIOUS_WEEK_LOW,
        )

    @staticmethod
    def _previous_iso_week(iso_year: int, iso_week: int) -> tuple[int, int]:
        first_day_of_current_week = date.fromisocalendar(iso_year, iso_week, 1)
        last_day_of_previous_week = first_day_of_current_week - timedelta(days=1)
        prev_iso_year, prev_iso_week, _ = last_day_of_previous_week.isocalendar()
        return prev_iso_year, prev_iso_week

    @staticmethod
    def _build_high_low_levels(
        period_candles: list[Candle],
        high_type: LiquidityType,
        low_type: LiquidityType,
    ) -> list[LiquidityLevel]:
        high_candle = max(period_candles, key=lambda c: c.high)
        low_candle = min(period_candles, key=lambda c: c.low)

        start_ts = min(c.timestamp for c in period_candles)
        end_ts = max(c.timestamp for c in period_candles)

        high_level = LiquidityLevel(
            liquidity_id=make_liquidity_id(
                high_candle.symbol,
                high_candle.timeframe,
                high_type,
                high_candle.timestamp.isoformat(),
            ),
            symbol=high_candle.symbol,
            timeframe=high_candle.timeframe,
            liquidity_type=high_type,
            liquidity_side=LiquiditySide.BUY_SIDE,
            price=high_candle.high,
            start_timestamp=start_ts,
            end_timestamp=end_ts,
            source_timestamps=[high_candle.timestamp],
            touch_count=1,
            strength=LiquidityStrength.INSTITUTIONAL,
            active=True,
        )

        low_level = LiquidityLevel(
            liquidity_id=make_liquidity_id(
                low_candle.symbol,
                low_candle.timeframe,
                low_type,
                low_candle.timestamp.isoformat(),
            ),
            symbol=low_candle.symbol,
            timeframe=low_candle.timeframe,
            liquidity_type=low_type,
            liquidity_side=LiquiditySide.SELL_SIDE,
            price=low_candle.low,
            start_timestamp=start_ts,
            end_timestamp=end_ts,
            source_timestamps=[low_candle.timestamp],
            touch_count=1,
            strength=LiquidityStrength.INSTITUTIONAL,
            active=True,
        )

        return [high_level, low_level]
