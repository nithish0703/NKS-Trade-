"""
Detects swing highs and swing lows in price structure.
"""

import hashlib
from typing import Optional, Sequence

from app.models.candle import Candle

from app.market_structure.results import SwingPoint, SwingType


class MarketStructureCalculationError(Exception):
    """Raised when market-structure calculations cannot be performed."""


def _make_swing_id(
    symbol: str,
    timeframe: str,
    timestamp,
    swing_type: SwingType,
) -> str:
    """
    Generate a stable, deterministic swing ID.

    Deliberately keyed on the swing candle's timestamp (not its
    position/index within whatever candle window happened to be fetched
    for this scan) so the same real swing always produces the same ID
    across scan cycles, even as the fetched candle window rolls forward.
    Candle timestamps are already validated as strictly ascending and
    therefore unique per symbol/timeframe, so timestamp alone identifies
    the candle just as precisely as index did.
    """
    raw = f"{symbol}|{timeframe}|{timestamp.isoformat()}|{swing_type.value}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class SwingDetector:
    """
    Detects confirmed swing highs and swing lows from a chronologically
    ordered sequence of candles, using a fixed left/right strength window.
    """

    def __init__(
        self, left_strength: int, right_strength: int, equality_tolerance: float
    ) -> None:
        if left_strength <= 0:
            raise MarketStructureCalculationError(
                f"left_strength must be positive, got {left_strength}."
            )
        if right_strength <= 0:
            raise MarketStructureCalculationError(
                f"right_strength must be positive, got {right_strength}."
            )
        if equality_tolerance < 0:
            raise MarketStructureCalculationError(
                f"equality_tolerance cannot be negative, got {equality_tolerance}."
            )

        self._left_strength = left_strength
        self._right_strength = right_strength
        self._equality_tolerance = equality_tolerance

    def _validate_candles(self, candles: Sequence[Candle]) -> None:
        if not candles:
            raise MarketStructureCalculationError("candles cannot be empty.")

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        previous_timestamp = None

        for index, candle in enumerate(candles):
            if candle.symbol != symbol:
                raise MarketStructureCalculationError(
                    f"Candle at index {index} has symbol '{candle.symbol}', "
                    f"expected '{symbol}'. All candles must share the same symbol."
                )
            if candle.timeframe != timeframe:
                raise MarketStructureCalculationError(
                    f"Candle at index {index} has timeframe '{candle.timeframe}', "
                    f"expected '{timeframe}'. All candles must share the same timeframe."
                )
            if previous_timestamp is not None and candle.timestamp <= previous_timestamp:
                raise MarketStructureCalculationError(
                    "candles must be in strictly ascending chronological order."
                )
            previous_timestamp = candle.timestamp

    def detect_swings(self, candles: Sequence[Candle]) -> list[SwingPoint]:
        """
        Detect all confirmed swing highs and swing lows in `candles`.

        A candle at index `i` is a confirmed swing high when its high is
        strictly greater than every high in the left-strength window and
        greater than or equal to every high in the right-strength window,
        and the right-side candles exist. Swing lows use the symmetric
        rule on lows.

        Returns:
            Confirmed swing points in ascending timestamp order. A single
            candle may appear as both a swing high and a swing low only
            if it independently satisfies both rules.

        Raises:
            MarketStructureCalculationError: If candles are empty, mixed
                symbol/timeframe, or not in ascending chronological order.
        """
        self._validate_candles(candles)

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        swings: list[SwingPoint] = []

        for index in range(len(candles)):
            left_start = index - self._left_strength
            right_end = index + self._right_strength

            has_left_history = left_start >= 0
            has_right_history = right_end < len(candles)

            if not has_left_history or not has_right_history:
                continue

            left_window = candles[left_start:index]
            right_window = candles[index + 1 : right_end + 1]
            candle = candles[index]

            is_swing_high = all(
                candle.high > c.high for c in left_window
            ) and all(candle.high >= c.high for c in right_window)

            is_swing_low = all(
                candle.low < c.low for c in left_window
            ) and all(candle.low <= c.low for c in right_window)

            if is_swing_high:
                swings.append(
                    SwingPoint(
                        swing_id=_make_swing_id(
                            symbol, timeframe, candle.timestamp, SwingType.HIGH
                        ),
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=candle.timestamp,
                        candle_index=index,
                        swing_type=SwingType.HIGH,
                        price=candle.high,
                        left_strength=self._left_strength,
                        right_strength=self._right_strength,
                        confirmed=True,
                    )
                )

            if is_swing_low:
                swings.append(
                    SwingPoint(
                        swing_id=_make_swing_id(
                            symbol, timeframe, candle.timestamp, SwingType.LOW
                        ),
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=candle.timestamp,
                        candle_index=index,
                        swing_type=SwingType.LOW,
                        price=candle.low,
                        left_strength=self._left_strength,
                        right_strength=self._right_strength,
                        confirmed=True,
                    )
                )

        swings.sort(key=lambda s: (s.timestamp, s.swing_type.value))
        return swings

    def detect_latest_swing_high(self, candles: Sequence[Candle]) -> Optional[SwingPoint]:
        """Return the most recent confirmed swing high, or None if none exist."""
        swings = self.detect_swings(candles)
        highs = [s for s in swings if s.swing_type == SwingType.HIGH]
        return highs[-1] if highs else None

    def detect_latest_swing_low(self, candles: Sequence[Candle]) -> Optional[SwingPoint]:
        """Return the most recent confirmed swing low, or None if none exist."""
        swings = self.detect_swings(candles)
        lows = [s for s in swings if s.swing_type == SwingType.LOW]
        return lows[-1] if lows else None
