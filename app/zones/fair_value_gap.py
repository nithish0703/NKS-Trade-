"""
Identifies fair value gap (FVG) zones.
"""

from typing import Optional, Sequence

from app.market_structure.shift_results import BreakDirection, StructureBreakResult
from app.models.candle import Candle
from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType

from app.zones._ids import make_zone_id


class FairValueGapDetector:
    """
    Detects fresh fair-value-gap (FVG) zones from three-candle windows,
    where the middle candle is part of a confirmed bullish or bearish
    structure-break displacement impulse.
    """

    def detect(
        self,
        candles: Sequence[Candle],
        structure_breaks: Optional[Sequence[StructureBreakResult]] = None,
    ) -> list[TradeZone]:
        """
        Detect fresh FVG zones using chronological three-candle windows.

        A bullish FVG requires candle[0].high strictly below candle[2].low,
        with the middle candle (candle[1]) matching a confirmed bullish
        structure-break's displacement candle. A bearish FVG is the
        symmetric rule: candle[0].low strictly above candle[2].high, with
        the middle candle matching a confirmed bearish displacement. No
        candle beyond candle[2] (index+2) is ever used to create the gap.
        """
        if not candles or len(candles) < 3:
            return []

        structure_breaks = structure_breaks or []
        bullish_displacement_indices = {
            b.displacement.candle_index
            for b in structure_breaks
            if b.direction == BreakDirection.BULLISH and b.displacement.confirmed
        }
        bearish_displacement_indices = {
            b.displacement.candle_index
            for b in structure_breaks
            if b.direction == BreakDirection.BEARISH and b.displacement.confirmed
        }
        break_id_by_displacement_index = {
            b.displacement.candle_index: b.break_id for b in structure_breaks
        }

        zones: list[TradeZone] = []
        seen_zone_ids: set[str] = set()

        for index in range(len(candles) - 2):
            first_candle = candles[index]
            middle_candle = candles[index + 1]
            third_candle = candles[index + 2]
            middle_index = index + 1

            if (
                first_candle.high < third_candle.low
                and middle_index in bullish_displacement_indices
            ):
                lower_price = first_candle.high
                upper_price = third_candle.low
                if upper_price <= lower_price:
                    continue

                source_key = f"{index}"
                zone_id = make_zone_id(
                    first_candle.symbol, first_candle.timeframe, ZoneType.FAIR_VALUE_GAP, source_key
                )
                if zone_id in seen_zone_ids:
                    continue
                seen_zone_ids.add(zone_id)

                zones.append(
                    TradeZone(
                        zone_id=zone_id,
                        symbol=first_candle.symbol,
                        timeframe=first_candle.timeframe,
                        zone_type=ZoneType.FAIR_VALUE_GAP,
                        direction="BUY",
                        lower_price=lower_price,
                        upper_price=upper_price,
                        created_at=third_candle.timestamp,
                        source_candle_timestamp=middle_candle.timestamp,
                        source_candle_index=middle_index,
                        status=ZoneStatus.FRESH,
                        originating_break_id=break_id_by_displacement_index.get(middle_index),
                        touch_count=0,
                    )
                )

            elif (
                first_candle.low > third_candle.high
                and middle_index in bearish_displacement_indices
            ):
                lower_price = third_candle.high
                upper_price = first_candle.low
                if upper_price <= lower_price:
                    continue

                source_key = f"{index}"
                zone_id = make_zone_id(
                    first_candle.symbol, first_candle.timeframe, ZoneType.FAIR_VALUE_GAP, source_key
                )
                if zone_id in seen_zone_ids:
                    continue
                seen_zone_ids.add(zone_id)

                zones.append(
                    TradeZone(
                        zone_id=zone_id,
                        symbol=first_candle.symbol,
                        timeframe=first_candle.timeframe,
                        zone_type=ZoneType.FAIR_VALUE_GAP,
                        direction="SELL",
                        lower_price=lower_price,
                        upper_price=upper_price,
                        created_at=third_candle.timestamp,
                        source_candle_timestamp=middle_candle.timestamp,
                        source_candle_index=middle_index,
                        status=ZoneStatus.FRESH,
                        originating_break_id=break_id_by_displacement_index.get(middle_index),
                        touch_count=0,
                    )
                )

        zones.sort(key=lambda z: (z.created_at, z.zone_id))
        return zones
