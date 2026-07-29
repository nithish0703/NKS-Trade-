"""
Orchestrates displacement, BOS, CHoCH, and MSS detection into a single
structure-shift detection result.
"""

from typing import Optional, Sequence

from app.liquidity.results import LiquiditySweepResult
from app.models.candle import Candle

from app.market_structure.bos_detector import BOSDetector
from app.market_structure.choch_detector import CHOCHDetector
from app.market_structure.displacement import DisplacementDetector, StructureShiftCalculationError
from app.market_structure.mss_detector import MSSDetector
from app.market_structure.results import MarketStructureResult, SwingPoint
from app.market_structure.shift_results import (
    StructureBreakResult,
    StructureBreakType,
    StructureShiftDetectionResult,
)

_BREAK_TYPE_PRIORITY = {
    StructureBreakType.MSS: 0,
    StructureBreakType.CHOCH: 1,
    StructureBreakType.BOS: 2,
}


class StructureShiftCalculator:
    """
    Coordinates displacement detection and BOS/CHoCH/MSS structure-break
    detection for a single symbol and timeframe.
    """

    def __init__(
        self,
        displacement_detector: DisplacementDetector,
        bos_detector: BOSDetector,
        choch_detector: CHOCHDetector,
        mss_detector: MSSDetector,
    ) -> None:
        self._displacement_detector = displacement_detector
        self._bos_detector = bos_detector
        self._choch_detector = choch_detector
        self._mss_detector = mss_detector

    def _validate_consistency(
        self,
        candles: Sequence[Candle],
        swings: Sequence[SwingPoint],
        structure: MarketStructureResult,
        liquidity_sweeps: Sequence[LiquiditySweepResult],
    ) -> None:
        if not candles:
            raise StructureShiftCalculationError("candles cannot be empty.")

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe

        if structure.symbol != symbol:
            raise StructureShiftCalculationError(
                f"structure symbol '{structure.symbol}' does not match candles symbol '{symbol}'."
            )
        if structure.timeframe != timeframe:
            raise StructureShiftCalculationError(
                f"structure timeframe '{structure.timeframe}' does not match candles "
                f"timeframe '{timeframe}'."
            )

        for swing in swings:
            if swing.symbol != symbol or swing.timeframe != timeframe:
                raise StructureShiftCalculationError(
                    "All swings must match the candles' symbol and timeframe."
                )

        for sweep in liquidity_sweeps:
            if sweep.symbol != symbol or sweep.timeframe != timeframe:
                raise StructureShiftCalculationError(
                    "All liquidity sweeps must match the candles' symbol and timeframe."
                )

    def calculate(
        self,
        candles: Sequence[Candle],
        swings: Sequence[SwingPoint],
        structure: MarketStructureResult,
        liquidity_sweeps: Sequence[LiquiditySweepResult],
        volume_ema_values: Optional[Sequence[Optional[float]]],
    ) -> StructureShiftDetectionResult:
        """
        Calculate the complete displacement/BOS/CHoCH/MSS structure-shift
        detection result for a single symbol and timeframe.

        A structural break is only ever confirmed by the underlying
        detectors when its supporting liquidity sweep timestamp is
        strictly before the break candle's timestamp; a break occurring
        before its supporting sweep is never produced as confirmed.

        Raises:
            StructureShiftCalculationError: If candles, swings, structure,
                or liquidity_sweeps are inconsistent in symbol/timeframe.
        """
        self._validate_consistency(candles, swings, structure, liquidity_sweeps)

        displacement_results = self._displacement_detector.detect(candles, volume_ema_values)

        bos_results = self._bos_detector.detect(
            candles, swings, displacement_results, liquidity_sweeps
        )
        choch_results = self._choch_detector.detect(
            candles, structure, swings, displacement_results, liquidity_sweeps
        )
        mss_results = self._mss_detector.detect(bos_results, choch_results)

        all_breaks = list(bos_results) + list(choch_results) + list(mss_results)
        all_breaks.sort(
            key=lambda b: (
                b.break_candle_timestamp,
                _BREAK_TYPE_PRIORITY[b.break_type],
                b.break_id,
            )
        )

        latest_confirmed_break = self._select_latest_confirmed(all_breaks)

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe

        return StructureShiftDetectionResult(
            symbol=symbol,
            timeframe=timeframe,
            displacement_candles=displacement_results,
            bos_results=bos_results,
            choch_results=choch_results,
            mss_results=mss_results,
            all_breaks=all_breaks,
            latest_confirmed_break=latest_confirmed_break,
        )

    def calculate_latest_confirmed_shift(
        self,
        candles: Sequence[Candle],
        swings: Sequence[SwingPoint],
        structure: MarketStructureResult,
        liquidity_sweeps: Sequence[LiquiditySweepResult],
        volume_ema_values: Optional[Sequence[Optional[float]]],
    ) -> Optional[StructureBreakResult]:
        """Calculate the full result and return only the latest confirmed break."""
        result = self.calculate(candles, swings, structure, liquidity_sweeps, volume_ema_values)
        return result.latest_confirmed_break

    @staticmethod
    def _select_latest_confirmed(
        all_breaks: Sequence[StructureBreakResult],
    ) -> Optional[StructureBreakResult]:
        confirmed = [b for b in all_breaks if b.confirmation.value == "CONFIRMED"]
        if not confirmed:
            return None

        def sort_key(b: StructureBreakResult):
            return (b.break_candle_timestamp, -_BREAK_TYPE_PRIORITY[b.break_type])

        # Latest timestamp first; among equal timestamps, prefer MSS over
        # CHOCH over BOS (lower priority number wins).
        best = max(
            confirmed,
            key=lambda b: (b.break_candle_timestamp, -_BREAK_TYPE_PRIORITY[b.break_type]),
        )
        return best
