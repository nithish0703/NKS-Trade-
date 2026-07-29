"""
Identifies order block zones.
"""

from typing import Optional, Sequence

from app.market_structure.shift_results import (
    BreakConfirmation,
    BreakDirection,
    StructureBreakResult,
)
from app.models.candle import Candle
from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType

from app.zones._ids import make_zone_id


class ZoneCalculationError(Exception):
    """Raised when zone detection or lifecycle calculations cannot be performed."""


class OrderBlockDetector:
    """
    Detects fresh order-block zones from confirmed structure breaks
    (BOS, CHoCH, or MSS) backed by a confirmed preceding liquidity sweep.
    """

    def detect(
        self, candles: Sequence[Candle], structure_breaks: Sequence[StructureBreakResult]
    ) -> list[TradeZone]:
        """
        Detect fresh order-block zones.

        For a confirmed bullish break, the order block is the last
        bearish candle before the break's displacement candle, occurring
        after the break's supporting liquidity sweep. For a confirmed
        bearish break, the order block is the last bullish candle under
        the same constraints. Wick-only breaks, unconfirmed breaks, and
        breaks without a confirmed preceding sweep never produce an
        order block. No candle after the break candle is ever used to
        select the source block.
        """
        if not candles:
            return []

        candles_by_timestamp = {c.timestamp: (i, c) for i, c in enumerate(candles)}

        zones: list[TradeZone] = []
        seen_zone_ids: set[str] = set()

        for structure_break in structure_breaks:
            if structure_break.confirmation != BreakConfirmation.CONFIRMED:
                continue
            if structure_break.wick_only_break:
                continue
            if not structure_break.displacement.confirmed:
                continue
            sweep = structure_break.preceding_liquidity_sweep
            if sweep is None or not sweep.confirmed:
                continue

            break_index = structure_break.break_candle_index
            if break_index >= len(candles) or candles[break_index].timestamp != structure_break.break_candle_timestamp:
                # Break candle isn't present in the provided candle window;
                # cannot safely locate the source candle.
                continue

            sweep_timestamp = sweep.sweep_candle_timestamp

            is_bullish = structure_break.direction == BreakDirection.BULLISH
            source_candle, source_index = self._find_source_candle(
                candles, break_index, sweep_timestamp, is_bullish
            )
            if source_candle is None:
                continue

            if is_bullish:
                lower_price = source_candle.low
                upper_price = source_candle.open
                direction = "BUY"
            else:
                lower_price = source_candle.open
                upper_price = source_candle.high
                direction = "SELL"

            if upper_price <= lower_price:
                continue

            source_key = f"{structure_break.break_id}|{source_index}"
            zone_id = make_zone_id(
                structure_break.symbol, structure_break.timeframe, ZoneType.ORDER_BLOCK, source_key
            )
            if zone_id in seen_zone_ids:
                continue
            seen_zone_ids.add(zone_id)

            zones.append(
                TradeZone(
                    zone_id=zone_id,
                    symbol=structure_break.symbol,
                    timeframe=structure_break.timeframe,
                    zone_type=ZoneType.ORDER_BLOCK,
                    direction=direction,
                    lower_price=lower_price,
                    upper_price=upper_price,
                    created_at=structure_break.break_candle_timestamp,
                    source_candle_timestamp=source_candle.timestamp,
                    source_candle_index=source_index,
                    status=ZoneStatus.FRESH,
                    originating_break_id=structure_break.break_id,
                    originating_sweep_id=sweep.sweep_id,
                    touch_count=0,
                )
            )

        zones.sort(key=lambda z: (z.created_at, z.zone_id))
        return zones

    @staticmethod
    def _find_source_candle(
        candles: Sequence[Candle],
        break_index: int,
        sweep_timestamp,
        is_bullish: bool,
    ) -> tuple[Optional[Candle], Optional[int]]:
        """
        Walk backward from the break candle (exclusive) to find the last
        opposite-direction candle occurring after the supporting sweep.
        """
        for index in range(break_index - 1, -1, -1):
            candle = candles[index]
            if candle.timestamp <= sweep_timestamp:
                break
            if is_bullish and candle.is_bearish:
                return candle, index
            if not is_bullish and candle.is_bullish:
                return candle, index
        return None, None
