"""
Orchestrates swing detection, trend structure classification, and
higher-timeframe bias analysis across one or more timeframes.
"""

from typing import Mapping, Sequence

from app.indicators.results import IndicatorSnapshot
from app.models.candle import Candle

from app.market_structure.htf_bias import (
    PRIMARY_TIMEFRAME,
    SECONDARY_TIMEFRAME,
    HigherTimeframeBiasAnalyzer,
)
from app.market_structure.results import HigherTimeframeBiasResult, MarketStructureResult
from app.market_structure.swing_detector import (
    MarketStructureCalculationError,
    SwingDetector,
)
from app.market_structure.trend_structure import TrendStructureAnalyzer


class MarketStructureCalculator:
    """
    Coordinates swing detection, trend structure classification, and
    higher-timeframe bias analysis for one or more timeframes.
    """

    def __init__(
        self,
        swing_detector: SwingDetector,
        trend_structure_analyzer: TrendStructureAnalyzer,
        higher_timeframe_bias_analyzer: HigherTimeframeBiasAnalyzer,
    ) -> None:
        self._swing_detector = swing_detector
        self._trend_structure_analyzer = trend_structure_analyzer
        self._higher_timeframe_bias_analyzer = higher_timeframe_bias_analyzer

    def calculate_timeframe(self, candles: Sequence[Candle]) -> MarketStructureResult:
        """
        Detect confirmed swings and analyze trend structure for a single
        chronologically ordered candle sequence.

        Raises:
            MarketStructureCalculationError: If candles are invalid.
        """
        if not candles:
            raise MarketStructureCalculationError("candles cannot be empty.")

        swings = self._swing_detector.detect_swings(candles)
        return self._trend_structure_analyzer.analyze(candles, swings)

    def calculate_multiple_timeframes(
        self, candles_by_timeframe: Mapping[str, Sequence[Candle]]
    ) -> dict[str, MarketStructureResult]:
        """
        Calculate market structure independently for each timeframe.

        Raises:
            MarketStructureCalculationError: An aggregated error if any
                timeframe's calculation fails. No partial result is
                returned.
        """
        errors: list[str] = []
        results: dict[str, MarketStructureResult] = {}

        for timeframe, candles in candles_by_timeframe.items():
            try:
                results[timeframe] = self.calculate_timeframe(candles)
            except MarketStructureCalculationError as exc:
                errors.append(f"{timeframe}: {exc}")

        if errors:
            raise MarketStructureCalculationError(
                f"Failed to calculate market structure for {len(errors)} "
                f"timeframe(s): {'; '.join(errors)}"
            )

        return results

    def calculate_higher_timeframe_bias(
        self,
        candles_by_timeframe: Mapping[str, Sequence[Candle]],
        indicators_by_timeframe: Mapping[str, IndicatorSnapshot],
    ) -> HigherTimeframeBiasResult:
        """
        Calculate 4H and 1H market structure and combine it with 4H/1H
        indicator snapshots to produce a HigherTimeframeBiasResult.

        Only the primary (4h) and secondary (1h) timeframes are required;
        15m entry-timeframe data is not used here.
        """
        required_candles = {
            timeframe: candles_by_timeframe[timeframe]
            for timeframe in (PRIMARY_TIMEFRAME, SECONDARY_TIMEFRAME)
            if timeframe in candles_by_timeframe
        }

        structures = self.calculate_multiple_timeframes(required_candles)

        return self._higher_timeframe_bias_analyzer.analyze(
            structures, indicators_by_timeframe
        )
