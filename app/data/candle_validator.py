"""
Exchange-agnostic structural validation of Candle sequences.

This module performs only structural/integrity validation; it does not
perform any trading analysis or strategy evaluation, and has no
dependency on any specific exchange's API response shape.
"""

from app.models.candle import Candle


class DataValidationError(ValueError):
    """Raised when a candle sequence fails structural validation."""


def validate_candle_sequence(candles: list[Candle]) -> None:
    """
    Validate structural integrity of a sequence of Candle objects.

    Requirements:
        - The list must not be empty.
        - All candles must share the same symbol.
        - All candles must share the same timeframe.
        - Timestamps must be unique.
        - Timestamps must be in strictly ascending chronological order.

    Raises:
        DataValidationError: If any requirement is not met.
    """
    if not candles:
        raise DataValidationError("Candle sequence cannot be empty.")

    symbol = candles[0].symbol
    timeframe = candles[0].timeframe
    seen_timestamps: set = set()
    previous_timestamp = None

    for index, candle in enumerate(candles):
        if candle.symbol != symbol:
            raise DataValidationError(
                f"Candle at index {index} has symbol '{candle.symbol}', "
                f"expected '{symbol}'. All candles must share the same symbol."
            )
        if candle.timeframe != timeframe:
            raise DataValidationError(
                f"Candle at index {index} has timeframe '{candle.timeframe}', "
                f"expected '{timeframe}'. All candles must share the same timeframe."
            )
        if candle.timestamp in seen_timestamps:
            raise DataValidationError(
                f"Duplicate candle timestamp detected: {candle.timestamp!r}."
            )
        seen_timestamps.add(candle.timestamp)

        if previous_timestamp is not None and candle.timestamp <= previous_timestamp:
            raise DataValidationError(
                "Candle timestamps must be in ascending chronological order: "
                f"{candle.timestamp!r} does not follow {previous_timestamp!r}."
            )
        previous_timestamp = candle.timestamp
