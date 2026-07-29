"""
Validates incoming market data for integrity and completeness.

This module only performs structural and integrity validation of raw
exchange payloads and candle sequences. It does not perform any trading
analysis or strategy evaluation.
"""

from typing import Any

from app.models.candle import Candle

_MIN_CANDLE_ROW_FIELDS = 9
_CONFIRM_COMPLETED = "1"
_CONFIRM_FORMING = "0"


class DataValidationError(ValueError):
    """Raised when raw exchange data fails structural validation."""


def validate_okx_response(payload: object) -> list[list[str]]:
    """
    Validate the structure of a raw OKX REST API response payload.

    Requirements:
        - The payload must be a dictionary.
        - The response "code" field must equal "0" (OKX success code).
        - A "data" field must be present and must be a list.
        - Every row in "data" must be a list with at least nine fields.

    Returns:
        The validated list of raw candle rows.

    Raises:
        DataValidationError: If any structural requirement is not met.
    """
    if not isinstance(payload, dict):
        raise DataValidationError(
            f"OKX response payload must be a dict, got {type(payload).__name__}."
        )

    code = payload.get("code")
    if code != "0":
        message = payload.get("msg", "unknown error")
        raise DataValidationError(
            f"OKX response returned a non-success code '{code}': {message}."
        )

    data = payload.get("data")
    if data is None:
        raise DataValidationError("OKX response is missing the 'data' field.")
    if not isinstance(data, list):
        raise DataValidationError(
            f"OKX response 'data' field must be a list, got {type(data).__name__}."
        )

    validated_rows: list[list[str]] = []
    for index, row in enumerate(data):
        if not isinstance(row, list):
            raise DataValidationError(
                f"OKX candle row at index {index} must be a list, "
                f"got {type(row).__name__}."
            )
        if len(row) < _MIN_CANDLE_ROW_FIELDS:
            raise DataValidationError(
                f"OKX candle row at index {index} has {len(row)} fields; "
                f"expected at least {_MIN_CANDLE_ROW_FIELDS}."
            )
        validated_rows.append(row)

    return validated_rows


def is_completed_candle(raw_candle: list[str]) -> bool:
    """
    Determine whether a raw OKX candle row represents a completed candle.

    The ninth field (index 8) is the OKX "confirm" flag:
        - "1" means the candle is completed.
        - "0" means the candle is still forming.

    Raises:
        DataValidationError: If the confirm field is missing or is not
            one of the recognized values.
    """
    if len(raw_candle) < _MIN_CANDLE_ROW_FIELDS:
        raise DataValidationError(
            f"Candle row has {len(raw_candle)} fields; "
            f"expected at least {_MIN_CANDLE_ROW_FIELDS}."
        )

    confirm = raw_candle[8]
    if confirm == _CONFIRM_COMPLETED:
        return True
    if confirm == _CONFIRM_FORMING:
        return False

    raise DataValidationError(
        f"Invalid candle 'confirm' field value: '{confirm}'. "
        f"Expected '{_CONFIRM_COMPLETED}' or '{_CONFIRM_FORMING}'."
    )


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
