"""
Orchestrates order-block, breaker-block, and fair-value-gap detection,
mitigation/invalidation evaluation, and entry-zone selection.
"""

from datetime import datetime
from typing import Optional, Sequence

from app.market_structure.shift_results import StructureBreakResult
from app.models.candle import Candle
from app.models.trade_zone import TradeZone, ZoneStatus

from app.zones.breaker_block import BreakerBlockDetector
from app.zones.fair_value_gap import FairValueGapDetector
from app.zones.invalidation_checker import ZoneInvalidationChecker
from app.zones.mitigation_checker import ZoneMitigationChecker
from app.zones.order_block import OrderBlockDetector, ZoneCalculationError
from app.zones.results import ZoneDetectionResult
from app.zones.zone_selector import EntryZoneSelector


class ZoneCalculator:
    """
    Coordinates order-block, breaker-block, and fair-value-gap detection
    together with mitigation/invalidation evaluation and entry-zone
    selection for a single symbol and timeframe.
    """

    def __init__(
        self,
        order_block_detector: OrderBlockDetector,
        fair_value_gap_detector: FairValueGapDetector,
        breaker_block_detector: BreakerBlockDetector,
        mitigation_checker: ZoneMitigationChecker,
        invalidation_checker: ZoneInvalidationChecker,
        entry_zone_selector: EntryZoneSelector,
    ) -> None:
        self._order_block_detector = order_block_detector
        self._fair_value_gap_detector = fair_value_gap_detector
        self._breaker_block_detector = breaker_block_detector
        self._mitigation_checker = mitigation_checker
        self._invalidation_checker = invalidation_checker
        self._entry_zone_selector = entry_zone_selector

    def _validate_consistency(
        self, candles: Sequence[Candle], structure_breaks: Sequence[StructureBreakResult]
    ) -> None:
        if not candles:
            raise ZoneCalculationError("candles cannot be empty.")

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe

        for structure_break in structure_breaks:
            if structure_break.symbol != symbol or structure_break.timeframe != timeframe:
                raise ZoneCalculationError(
                    "All structure breaks must match the candles' symbol and timeframe."
                )

    def calculate(
        self,
        candles: Sequence[Candle],
        structure_breaks: Sequence[StructureBreakResult],
        expected_direction: str,
        current_price: float,
        current_time_utc: datetime,
    ) -> ZoneDetectionResult:
        """
        Detect, evaluate, and select entry zones for a single symbol and
        timeframe.

        Raises:
            ZoneCalculationError: If candles are empty or structure
                breaks do not match the candles' symbol/timeframe.
        """
        self._validate_consistency(candles, structure_breaks)

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe

        order_blocks = self._order_block_detector.detect(candles, structure_breaks)
        fair_value_gaps = self._fair_value_gap_detector.detect(candles, structure_breaks)
        breaker_blocks = self._breaker_block_detector.detect(candles, order_blocks, structure_breaks)

        combined = list(order_blocks) + list(breaker_blocks) + list(fair_value_gaps)

        deduped: dict[str, TradeZone] = {}
        for zone in combined:
            deduped[zone.zone_id] = zone
        all_zones_pre_lifecycle = list(deduped.values())

        mitigated_evaluated = self._mitigation_checker.evaluate_multiple(
            all_zones_pre_lifecycle, candles, current_time_utc
        )
        fully_evaluated = self._invalidation_checker.evaluate_multiple(
            mitigated_evaluated, candles, current_time_utc
        )

        fully_evaluated.sort(key=lambda z: (z.created_at, z.zone_id))

        fresh_zones = [z for z in fully_evaluated if z.status == ZoneStatus.FRESH]
        mitigated_zones = [z for z in fully_evaluated if z.status == ZoneStatus.MITIGATED]
        invalidated_zones = [z for z in fully_evaluated if z.status == ZoneStatus.INVALIDATED]

        selected_zone = self._entry_zone_selector.select(
            fresh_zones, expected_direction, current_price, current_time_utc
        )

        evaluated_by_id = {z.zone_id: z for z in fully_evaluated}
        evaluated_order_blocks = [evaluated_by_id[z.zone_id] for z in order_blocks]
        evaluated_breaker_blocks = [evaluated_by_id[z.zone_id] for z in breaker_blocks]
        evaluated_fvgs = [evaluated_by_id[z.zone_id] for z in fair_value_gaps]

        return ZoneDetectionResult(
            symbol=symbol,
            timeframe=timeframe,
            order_blocks=evaluated_order_blocks,
            breaker_blocks=evaluated_breaker_blocks,
            fair_value_gaps=evaluated_fvgs,
            all_zones=fully_evaluated,
            fresh_zones=fresh_zones,
            mitigated_zones=mitigated_zones,
            invalidated_zones=invalidated_zones,
            selected_zone=selected_zone,
        )

    def select_latest_valid_zone(
        self,
        candles: Sequence[Candle],
        structure_breaks: Sequence[StructureBreakResult],
        expected_direction: str,
        current_price: float,
        current_time_utc: datetime,
    ) -> Optional[TradeZone]:
        """Calculate the full result and return only the selected zone."""
        result = self.calculate(
            candles, structure_breaks, expected_direction, current_price, current_time_utc
        )
        return result.selected_zone
