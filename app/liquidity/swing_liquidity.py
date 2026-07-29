"""
Identifies liquidity resting at swing points.
"""

from typing import Sequence

from app.market_structure.results import SwingPoint, SwingType

from app.liquidity._ids import make_liquidity_id
from app.liquidity.equal_high_low import LiquidityCalculationError
from app.liquidity.results import LiquidityLevel, LiquiditySide, LiquidityStrength, LiquidityType

_INSTITUTIONAL_STRENGTH_MARGIN = 2


class SwingLiquidityDetector:
    """
    Identifies major-swing liquidity levels from confirmed swings whose
    left/right strength meets a configured minimum.
    """

    def __init__(self, minimum_swing_strength: int) -> None:
        if minimum_swing_strength <= 0:
            raise LiquidityCalculationError(
                f"minimum_swing_strength must be positive, got {minimum_swing_strength}."
            )
        self._minimum_swing_strength = minimum_swing_strength

    def detect_major_swing_levels(
        self, swings: Sequence[SwingPoint]
    ) -> list[LiquidityLevel]:
        """
        Detect major-swing liquidity levels from confirmed swings meeting
        the minimum left/right strength requirement.

        Each qualifying swing produces exactly one liquidity level.
        Weak swings (below the configured minimum strength) never
        produce an institutional liquidity level.
        """
        ordered = sorted(swings, key=lambda s: s.timestamp)
        levels: list[LiquidityLevel] = []
        seen_ids: set[str] = set()

        for swing in ordered:
            if not swing.confirmed:
                continue
            if (
                swing.left_strength < self._minimum_swing_strength
                or swing.right_strength < self._minimum_swing_strength
            ):
                continue

            if swing.swing_type == SwingType.HIGH:
                liquidity_type = LiquidityType.MAJOR_SWING_HIGH
                side = LiquiditySide.BUY_SIDE
            else:
                liquidity_type = LiquidityType.MAJOR_SWING_LOW
                side = LiquiditySide.SELL_SIDE

            is_institutional = (
                swing.left_strength >= self._minimum_swing_strength + _INSTITUTIONAL_STRENGTH_MARGIN
                and swing.right_strength >= self._minimum_swing_strength + _INSTITUTIONAL_STRENGTH_MARGIN
            )
            strength = (
                LiquidityStrength.INSTITUTIONAL if is_institutional else LiquidityStrength.STRONG
            )

            liquidity_id = make_liquidity_id(
                swing.symbol, swing.timeframe, liquidity_type, swing.swing_id
            )
            if liquidity_id in seen_ids:
                continue
            seen_ids.add(liquidity_id)

            levels.append(
                LiquidityLevel(
                    liquidity_id=liquidity_id,
                    symbol=swing.symbol,
                    timeframe=swing.timeframe,
                    liquidity_type=liquidity_type,
                    liquidity_side=side,
                    price=swing.price,
                    start_timestamp=swing.timestamp,
                    end_timestamp=swing.timestamp,
                    source_timestamps=[swing.timestamp],
                    touch_count=1,
                    strength=strength,
                    active=True,
                )
            )

        return levels
