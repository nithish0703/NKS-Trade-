"""
Identifies breaker block zones.
"""

from typing import Sequence

from app.market_structure.shift_results import (
    BreakConfirmation,
    BreakDirection,
    StructureBreakResult,
)
from app.models.candle import Candle
from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType

from app.zones._ids import make_zone_id


class BreakerBlockDetector:
    """
    Detects fresh breaker-block zones formed when a confirmed opposite-
    direction structure break decisively invalidates a prior order block
    with a closing price beyond its boundary (not merely a wick).
    """

    def detect(
        self,
        candles: Sequence[Candle],
        order_blocks: Sequence[TradeZone],
        structure_breaks: Sequence[StructureBreakResult],
    ) -> list[TradeZone]:
        """
        Detect fresh breaker-block zones.

        A bullish breaker block requires a prior bearish order block
        whose upper boundary is decisively closed above (not just
        wicked) by a confirmed bullish structure break occurring after
        the order block's creation. The invalidated block's boundaries
        become the breaker zone. Bearish breaker blocks are the
        symmetric rule against bullish order blocks and bearish breaks.
        """
        candles_by_timestamp = {c.timestamp: c for c in candles}

        zones: list[TradeZone] = []
        seen_zone_ids: set[str] = set()

        for order_block in order_blocks:
            if order_block.zone_type != ZoneType.ORDER_BLOCK:
                continue

            if order_block.direction == "SELL":
                confirming_break = self._find_invalidating_break(
                    structure_breaks,
                    order_block,
                    required_direction=BreakDirection.BULLISH,
                    boundary_price=order_block.upper_price,
                    close_beyond=lambda close, boundary: close > boundary,
                )
                if confirming_break is None:
                    continue

                new_direction = "BUY"
            elif order_block.direction == "BUY":
                confirming_break = self._find_invalidating_break(
                    structure_breaks,
                    order_block,
                    required_direction=BreakDirection.BEARISH,
                    boundary_price=order_block.lower_price,
                    close_beyond=lambda close, boundary: close < boundary,
                )
                if confirming_break is None:
                    continue

                new_direction = "SELL"
            else:
                continue

            source_key = f"{order_block.zone_id}|{confirming_break.break_id}"
            zone_id = make_zone_id(
                order_block.symbol, order_block.timeframe, ZoneType.BREAKER_BLOCK, source_key
            )
            if zone_id in seen_zone_ids:
                continue
            seen_zone_ids.add(zone_id)

            zones.append(
                TradeZone(
                    zone_id=zone_id,
                    symbol=order_block.symbol,
                    timeframe=order_block.timeframe,
                    zone_type=ZoneType.BREAKER_BLOCK,
                    direction=new_direction,
                    lower_price=order_block.lower_price,
                    upper_price=order_block.upper_price,
                    created_at=confirming_break.break_candle_timestamp,
                    source_candle_timestamp=confirming_break.break_candle_timestamp,
                    source_candle_index=confirming_break.break_candle_index,
                    status=ZoneStatus.FRESH,
                    originating_break_id=confirming_break.break_id,
                    originating_sweep_id=(
                        confirming_break.preceding_liquidity_sweep.sweep_id
                        if confirming_break.preceding_liquidity_sweep
                        else None
                    ),
                    touch_count=0,
                    metadata={"original_order_block_id": order_block.zone_id},
                )
            )

        zones.sort(key=lambda z: (z.created_at, z.zone_id))
        return zones

    @staticmethod
    def _find_invalidating_break(
        structure_breaks: Sequence[StructureBreakResult],
        order_block: TradeZone,
        required_direction: BreakDirection,
        boundary_price: float,
        close_beyond,
    ):
        candidates = [
            b
            for b in structure_breaks
            if b.confirmation == BreakConfirmation.CONFIRMED
            and b.direction == required_direction
            and not b.wick_only_break
            and b.displacement.confirmed
            and b.break_candle_timestamp > order_block.created_at
            and close_beyond(b.close_price, boundary_price)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda b: b.break_candle_timestamp)
