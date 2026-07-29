"""
Selects active institutional liquidity levels relevant to the current price.
"""

from datetime import datetime, timezone
from typing import Sequence

from app.liquidity.equal_high_low import LiquidityCalculationError
from app.liquidity.results import LiquidityLevel, LiquiditySide, LiquidityStrength, LiquidityType

_PRIORITY_ORDER: dict[LiquidityType, int] = {
    LiquidityType.PREVIOUS_WEEK_HIGH: 1,
    LiquidityType.PREVIOUS_WEEK_LOW: 1,
    LiquidityType.PREVIOUS_DAY_HIGH: 2,
    LiquidityType.PREVIOUS_DAY_LOW: 2,
    LiquidityType.EQUAL_HIGH: 3,  # only when touch_count >= 3; see _priority()
    LiquidityType.EQUAL_LOW: 3,
    LiquidityType.MAJOR_SWING_HIGH: 4,
    LiquidityType.MAJOR_SWING_LOW: 4,
    LiquidityType.SESSION_HIGH: 5,
    LiquidityType.SESSION_LOW: 5,
}
_TWO_TOUCH_EQUAL_PRIORITY = 6


def get_institutional_priority(level: LiquidityLevel) -> int:
    """
    Return the institutional priority rank for a liquidity level (lower
    is higher priority): previous-week, previous-day, 3+-touch equal
    highs/lows, major swings, session levels, then 2-touch equal highs/lows.
    """
    if level.liquidity_type in (LiquidityType.EQUAL_HIGH, LiquidityType.EQUAL_LOW):
        if level.touch_count >= 3:
            return 3
        return _TWO_TOUCH_EQUAL_PRIORITY
    return _PRIORITY_ORDER[level.liquidity_type]


def _priority(level: LiquidityLevel) -> int:
    return get_institutional_priority(level)


class InstitutionalLiquiditySelector:
    """
    Selects active, sufficiently strong liquidity levels and orders them
    by institutional priority. Does not select a take-profit target.
    """

    def select_active_institutional_levels(
        self,
        levels: Sequence[LiquidityLevel],
        current_price: float,
        current_time_utc: datetime,
    ) -> list[LiquidityLevel]:
        """
        Select active, non-WEAK liquidity levels, deduplicated and sorted
        by institutional priority, then distance from current price, then
        timestamp, then liquidity ID.

        Raises:
            LiquidityCalculationError: If current_price is not positive
                or current_time_utc is not timezone-aware UTC.
        """
        if current_price <= 0:
            raise LiquidityCalculationError("current_price must be positive.")
        if current_time_utc.tzinfo is None or current_time_utc.tzinfo.utcoffset(
            current_time_utc
        ) is None:
            raise LiquidityCalculationError("current_time_utc must be timezone-aware UTC.")
        if current_time_utc.utcoffset() != timezone.utc.utcoffset(current_time_utc):
            raise LiquidityCalculationError("current_time_utc must be in UTC.")

        deduped: dict[str, LiquidityLevel] = {}
        for level in levels:
            if not level.active:
                continue
            if level.strength == LiquidityStrength.WEAK:
                continue
            if level.metadata and level.metadata.get("invalidated") is True:
                continue
            deduped[level.liquidity_id] = level

        selected = list(deduped.values())

        selected.sort(
            key=lambda level: (
                _priority(level),
                abs(level.price - current_price),
                level.start_timestamp,
                level.liquidity_id,
            )
        )

        return selected
