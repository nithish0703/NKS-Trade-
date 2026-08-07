"""
Orchestrates swing detection and trend structure classification across
one or more timeframes.
"""

from typing import Mapping, Sequence

from app.models.candle import Candle

from app.market_structure.results import MarketStructureResult
from app.market_structure.swing_detector import (
    MarketStructureCalculationError,
    SwingDetector,
)
from app.market_structure.trend_structure import TrendStructureAnalyzer


class MarketStructureCalculator:
    """
    Coordinates swing detection and trend structure classification for
    one or more timeframes.
    """

    def __init__(
        self,
        swing_detector: SwingDetector,
        trend_structure_analyzer: TrendStructureAnalyzer,
    ) -> None:
        self._swing_detector = swing_detector
        self._trend_structure_analyzer = trend_structure_analyzer

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
