"""
Determines overall trend structure from swing points.
"""

from typing import Optional, Sequence

from app.models.candle import Candle

from app.market_structure.results import (
    ClassifiedSwing,
    MarketStructureResult,
    StructureLabel,
    SwingPoint,
    SwingType,
    TrendDirection,
)
from app.market_structure.swing_detector import MarketStructureCalculationError

_BULLISH_HIGH_LABELS = {StructureLabel.HIGHER_HIGH}
_BULLISH_LOW_LABELS = {StructureLabel.HIGHER_LOW}
_BEARISH_HIGH_LABELS = {StructureLabel.LOWER_HIGH}
_BEARISH_LOW_LABELS = {StructureLabel.LOWER_LOW}


class TrendStructureAnalyzer:
    """
    Classifies swing points relative to the previous same-type swing and
    derives an overall structural trend direction for a timeframe.
    """

    def __init__(self, equality_tolerance: float, minimum_confirmed_swings: int) -> None:
        if equality_tolerance < 0:
            raise MarketStructureCalculationError(
                f"equality_tolerance cannot be negative, got {equality_tolerance}."
            )
        if minimum_confirmed_swings < 4:
            raise MarketStructureCalculationError(
                f"minimum_confirmed_swings must be at least 4, got "
                f"{minimum_confirmed_swings}."
            )

        self._equality_tolerance = equality_tolerance
        self._minimum_confirmed_swings = minimum_confirmed_swings

    def _validate_swings(self, swings: Sequence[SwingPoint]) -> None:
        if not swings:
            return

        symbol = swings[0].symbol
        timeframe = swings[0].timeframe
        seen_ids: set[str] = set()

        for swing in swings:
            if swing.symbol != symbol:
                raise MarketStructureCalculationError(
                    "All swings must share the same symbol."
                )
            if swing.timeframe != timeframe:
                raise MarketStructureCalculationError(
                    "All swings must share the same timeframe."
                )
            if swing.swing_id in seen_ids:
                raise MarketStructureCalculationError(
                    f"Duplicate swing_id detected: {swing.swing_id!r}."
                )
            seen_ids.add(swing.swing_id)

    def classify_swings(self, swings: Sequence[SwingPoint]) -> list[ClassifiedSwing]:
        """
        Classify each swing relative to the previous confirmed swing of
        the same type, preserving chronological order.

        The first swing of each type (HIGH, LOW) is UNCLASSIFIED. Swing
        highs are never compared against swing lows.

        Raises:
            MarketStructureCalculationError: If swings have mixed
                symbol/timeframe or contain duplicate swing IDs.
        """
        self._validate_swings(swings)

        ordered = sorted(swings, key=lambda s: (s.timestamp, s.swing_type.value))

        previous_high: Optional[SwingPoint] = None
        previous_low: Optional[SwingPoint] = None
        classified: list[ClassifiedSwing] = []

        for swing in ordered:
            if swing.swing_type == SwingType.HIGH:
                previous = previous_high
            else:
                previous = previous_low

            if previous is None:
                classified.append(
                    ClassifiedSwing(
                        swing=swing,
                        structure_label=StructureLabel.UNCLASSIFIED,
                        previous_same_type_swing_id=None,
                        price_difference=None,
                        percentage_difference=None,
                    )
                )
            else:
                price_difference = swing.price - previous.price
                percentage_difference = abs(price_difference) / abs(previous.price)

                if percentage_difference <= self._equality_tolerance:
                    label = (
                        StructureLabel.EQUAL_HIGH
                        if swing.swing_type == SwingType.HIGH
                        else StructureLabel.EQUAL_LOW
                    )
                elif price_difference > 0:
                    label = (
                        StructureLabel.HIGHER_HIGH
                        if swing.swing_type == SwingType.HIGH
                        else StructureLabel.HIGHER_LOW
                    )
                else:
                    label = (
                        StructureLabel.LOWER_HIGH
                        if swing.swing_type == SwingType.HIGH
                        else StructureLabel.LOWER_LOW
                    )

                classified.append(
                    ClassifiedSwing(
                        swing=swing,
                        structure_label=label,
                        previous_same_type_swing_id=previous.swing_id,
                        price_difference=price_difference,
                        percentage_difference=percentage_difference,
                    )
                )

            if swing.swing_type == SwingType.HIGH:
                previous_high = swing
            else:
                previous_low = swing

        return classified

    def analyze(
        self, candles: Sequence[Candle], swings: Sequence[SwingPoint]
    ) -> MarketStructureResult:
        """
        Classify swings and derive an overall trend direction.

        Trend rules:
            - BULLISH: the latest confirmed structural sequence contains
              at least one recent HIGHER_HIGH and at least one recent
              HIGHER_LOW, with no newer contradictory LOWER_HIGH/LOWER_LOW.
            - BEARISH: the latest confirmed structural sequence contains
              at least one recent LOWER_HIGH and at least one recent
              LOWER_LOW, with no newer contradictory HIGHER_HIGH/HIGHER_LOW.
            - RANGE: sufficient confirmed swings exist, but equal
              highs/lows dominate or labels alternate without a clear
              sequence.
            - UNKNOWN: insufficient confirmed swings, incomplete latest
              structure, or no defensible direction.

        This method does not use future candles beyond those already
        reflected in the provided swings, does not use indicators, and
        does not make any trade acceptance, rejection, or scoring
        decisions.
        """
        symbol = candles[0].symbol if candles else (swings[0].symbol if swings else "")
        timeframe = (
            candles[0].timeframe if candles else (swings[0].timeframe if swings else "")
        )

        classified_swings = self.classify_swings(swings)

        confirmed_swings = [cs for cs in classified_swings if cs.swing.confirmed]

        higher_high_count = sum(
            1 for cs in classified_swings if cs.structure_label == StructureLabel.HIGHER_HIGH
        )
        higher_low_count = sum(
            1 for cs in classified_swings if cs.structure_label == StructureLabel.HIGHER_LOW
        )
        lower_high_count = sum(
            1 for cs in classified_swings if cs.structure_label == StructureLabel.LOWER_HIGH
        )
        lower_low_count = sum(
            1 for cs in classified_swings if cs.structure_label == StructureLabel.LOWER_LOW
        )
        equal_high_count = sum(
            1 for cs in classified_swings if cs.structure_label == StructureLabel.EQUAL_HIGH
        )
        equal_low_count = sum(
            1 for cs in classified_swings if cs.structure_label == StructureLabel.EQUAL_LOW
        )

        highs = [s for s in swings if s.swing_type == SwingType.HIGH]
        lows = [s for s in swings if s.swing_type == SwingType.LOW]
        latest_swing_high = max(highs, key=lambda s: s.timestamp) if highs else None
        latest_swing_low = max(lows, key=lambda s: s.timestamp) if lows else None

        trend_direction, confidence_notes = self._classify_trend(
            confirmed_swings, len(confirmed_swings)
        )

        return MarketStructureResult(
            symbol=symbol,
            timeframe=timeframe,
            swings=list(swings),
            classified_swings=classified_swings,
            latest_swing_high=latest_swing_high,
            latest_swing_low=latest_swing_low,
            trend_direction=trend_direction,
            higher_high_count=higher_high_count,
            higher_low_count=higher_low_count,
            lower_high_count=lower_high_count,
            lower_low_count=lower_low_count,
            equal_high_count=equal_high_count,
            equal_low_count=equal_low_count,
            confidence_notes=confidence_notes,
        )

    def _classify_trend(
        self, confirmed_swings: list[ClassifiedSwing], confirmed_count: int
    ) -> tuple[TrendDirection, str]:
        if confirmed_count < self._minimum_confirmed_swings:
            return (
                TrendDirection.UNKNOWN,
                f"Insufficient confirmed swings: {confirmed_count} "
                f"(minimum {self._minimum_confirmed_swings}).",
            )

        # Walk backwards through the classified sequence, tracking the
        # most recent label seen for highs and lows independently, and
        # stop as soon as a contradictory label is found for either.
        latest_high_label: Optional[StructureLabel] = None
        latest_low_label: Optional[StructureLabel] = None
        contradicted_bullish = False
        contradicted_bearish = False

        for cs in reversed(confirmed_swings):
            label = cs.structure_label
            swing_type = cs.swing.swing_type

            if swing_type == SwingType.HIGH:
                if latest_high_label is None:
                    latest_high_label = label
                if label in _BEARISH_HIGH_LABELS:
                    contradicted_bullish = True
                if label in _BULLISH_HIGH_LABELS:
                    contradicted_bearish = True
            else:
                if latest_low_label is None:
                    latest_low_label = label
                if label in _BEARISH_LOW_LABELS:
                    contradicted_bullish = True
                if label in _BULLISH_LOW_LABELS:
                    contradicted_bearish = True

            if latest_high_label is not None and latest_low_label is not None:
                break

        is_bullish = (
            latest_high_label in _BULLISH_HIGH_LABELS
            and latest_low_label in _BULLISH_LOW_LABELS
        )
        is_bearish = (
            latest_high_label in _BEARISH_HIGH_LABELS
            and latest_low_label in _BEARISH_LOW_LABELS
        )

        if is_bullish and not contradicted_bullish:
            return (
                TrendDirection.BULLISH,
                "Latest structure shows a higher high and higher low with "
                "no contradicting recent structure.",
            )

        if is_bearish and not contradicted_bearish:
            return (
                TrendDirection.BEARISH,
                "Latest structure shows a lower high and lower low with "
                "no contradicting recent structure.",
            )

        equal_count = sum(
            1
            for cs in confirmed_swings
            if cs.structure_label in (StructureLabel.EQUAL_HIGH, StructureLabel.EQUAL_LOW)
        )
        directional_count = sum(
            1
            for cs in confirmed_swings
            if cs.structure_label
            in (
                StructureLabel.HIGHER_HIGH,
                StructureLabel.HIGHER_LOW,
                StructureLabel.LOWER_HIGH,
                StructureLabel.LOWER_LOW,
            )
        )

        if equal_count >= directional_count and equal_count > 0:
            return (
                TrendDirection.RANGE,
                "Equal highs/lows dominate the confirmed swing structure.",
            )

        if is_bullish and contradicted_bullish:
            return (
                TrendDirection.RANGE,
                "Bullish structure is contradicted by newer opposing structure.",
            )
        if is_bearish and contradicted_bearish:
            return (
                TrendDirection.RANGE,
                "Bearish structure is contradicted by newer opposing structure.",
            )

        return (
            TrendDirection.RANGE,
            "Structural labels alternate without a clear directional sequence.",
        )
