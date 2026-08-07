"""
Detects equal highs and equal lows (liquidity pools).
"""

from typing import Sequence

from app.market_structure.results import SwingPoint, SwingType

from app.liquidity._ids import make_liquidity_id
from app.liquidity.results import LiquidityLevel, LiquiditySide, LiquidityStrength, LiquidityType


class LiquidityCalculationError(Exception):
    """Raised when liquidity-level or liquidity-sweep calculations cannot be performed."""


def _strength_for_touch_count(touch_count: int) -> LiquidityStrength:
    if touch_count >= 3:
        return LiquidityStrength.INSTITUTIONAL
    return LiquidityStrength.STRONG


class EqualHighLowDetector:
    """
    Groups confirmed swing highs/lows that fall within a relative price
    tolerance into equal-high/equal-low liquidity levels.
    """

    def __init__(
        self, equality_tolerance: float, minimum_touches: int, maximum_group_span: int
    ) -> None:
        if equality_tolerance < 0:
            raise LiquidityCalculationError(
                f"equality_tolerance cannot be negative, got {equality_tolerance}."
            )
        if minimum_touches < 2:
            raise LiquidityCalculationError(
                f"minimum_touches must be at least 2, got {minimum_touches}."
            )
        if maximum_group_span <= 0:
            raise LiquidityCalculationError(
                f"maximum_group_span must be positive, got {maximum_group_span}."
            )

        self._equality_tolerance = equality_tolerance
        self._minimum_touches = minimum_touches
        self._maximum_group_span = maximum_group_span

    def _validate_swings(self, swings: Sequence[SwingPoint]) -> None:
        if not swings:
            return
        symbol = swings[0].symbol
        timeframe = swings[0].timeframe
        for swing in swings:
            if swing.symbol != symbol:
                raise LiquidityCalculationError(
                    "All swings must share the same symbol."
                )
            if swing.timeframe != timeframe:
                raise LiquidityCalculationError(
                    "All swings must share the same timeframe."
                )

    def _group_swings(self, swings: list[SwingPoint]) -> list[list[SwingPoint]]:
        """Group swings whose prices fall within tolerance, in candle-index order."""
        ordered = sorted(swings, key=lambda s: s.candle_index)
        groups: list[list[SwingPoint]] = []
        used: set[str] = set()

        for i, swing in enumerate(ordered):
            if swing.swing_id in used:
                continue

            group = [swing]
            used.add(swing.swing_id)

            for other in ordered[i + 1 :]:
                if other.swing_id in used:
                    continue
                if other.candle_index - swing.candle_index > self._maximum_group_span:
                    continue
                relative_diff = abs(other.price - swing.price) / abs(swing.price)
                if relative_diff <= self._equality_tolerance:
                    group.append(other)
                    used.add(other.swing_id)

            if len(group) >= self._minimum_touches:
                groups.append(sorted(group, key=lambda s: s.timestamp))

        return groups

    def _build_level(
        self, group: list[SwingPoint], liquidity_type: LiquidityType, side: LiquiditySide
    ) -> LiquidityLevel:
        mean_price = sum(s.price for s in group) / len(group)
        source_timestamps = [s.timestamp for s in group]
        # Keyed on each swing's timestamp (not its candle_index, which is
        # only that swing's position within whatever window this scan
        # happened to fetch) so the same real group of swings always
        # produces the same liquidity_id across scan cycles.
        source_key = "-".join(s.timestamp.isoformat() for s in group)

        return LiquidityLevel(
            liquidity_id=make_liquidity_id(
                group[0].symbol, group[0].timeframe, liquidity_type, source_key
            ),
            symbol=group[0].symbol,
            timeframe=group[0].timeframe,
            liquidity_type=liquidity_type,
            liquidity_side=side,
            price=mean_price,
            start_timestamp=min(source_timestamps),
            end_timestamp=max(source_timestamps),
            source_timestamps=source_timestamps,
            touch_count=len(group),
            strength=_strength_for_touch_count(len(group)),
            active=True,
        )

    def detect_equal_highs(self, swings: Sequence[SwingPoint]) -> list[LiquidityLevel]:
        """
        Detect equal-high liquidity levels from confirmed swing highs.

        Raises:
            LiquidityCalculationError: If swings have mixed symbol/timeframe.
        """
        self._validate_swings(swings)
        confirmed_highs = [
            s for s in swings if s.swing_type == SwingType.HIGH and s.confirmed
        ]
        groups = self._group_swings(confirmed_highs)
        levels = [
            self._build_level(group, LiquidityType.EQUAL_HIGH, LiquiditySide.BUY_SIDE)
            for group in groups
        ]
        levels.sort(key=lambda level: level.start_timestamp)
        return levels

    def detect_equal_lows(self, swings: Sequence[SwingPoint]) -> list[LiquidityLevel]:
        """
        Detect equal-low liquidity levels from confirmed swing lows.

        Raises:
            LiquidityCalculationError: If swings have mixed symbol/timeframe.
        """
        self._validate_swings(swings)
        confirmed_lows = [
            s for s in swings if s.swing_type == SwingType.LOW and s.confirmed
        ]
        groups = self._group_swings(confirmed_lows)
        levels = [
            self._build_level(group, LiquidityType.EQUAL_LOW, LiquiditySide.SELL_SIDE)
            for group in groups
        ]
        levels.sort(key=lambda level: level.start_timestamp)
        return levels
