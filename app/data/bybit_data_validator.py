"""
Validates incoming Bybit v5 market-data API payloads for integrity and
completeness.

This module only performs structural and integrity validation of raw
exchange payloads and candle sequences. It does not perform any trading
analysis or strategy evaluation.
"""

from datetime import datetime, timezone

from app.data.candle_validator import DataValidationError, validate_candle_sequence

_MIN_KLINE_ROW_FIELDS = 7
_SUCCESS_RET_CODE = 0

__all__ = [
    "DataValidationError",
    "validate_bybit_response",
    "is_completed_kline",
    "validate_candle_sequence",
]


def validate_bybit_response(payload: object) -> list[list[str]]:
    """
    Validate the structure of a raw Bybit v5 REST API response payload.

    Requirements:
        - The payload must be a dictionary.
        - The response "retCode" field must equal 0 (Bybit success code).
        - A "result.list" field must be present and must be a list.
        - Every row in "result.list" must be a list with at least seven
          fields (startTime, open, high, low, close, volume, turnover).

    Returns:
        The validated list of raw kline rows.

    Raises:
        DataValidationError: If any structural requirement is not met.
    """
    if not isinstance(payload, dict):
        raise DataValidationError(
            f"Bybit response payload must be a dict, got {type(payload).__name__}."
        )

    ret_code = payload.get("retCode")
    if ret_code != _SUCCESS_RET_CODE:
        message = payload.get("retMsg", "unknown error")
        raise DataValidationError(
            f"Bybit response returned a non-success retCode '{ret_code}': {message}."
        )

    result = payload.get("result")
    if not isinstance(result, dict):
        raise DataValidationError(
            f"Bybit response 'result' field must be a dict, got {type(result).__name__}."
        )

    rows = result.get("list")
    if rows is None:
        raise DataValidationError("Bybit response is missing the 'result.list' field.")
    if not isinstance(rows, list):
        raise DataValidationError(
            f"Bybit response 'result.list' field must be a list, got {type(rows).__name__}."
        )

    validated_rows: list[list[str]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, list):
            raise DataValidationError(
                f"Bybit kline row at index {index} must be a list, got {type(row).__name__}."
            )
        if len(row) < _MIN_KLINE_ROW_FIELDS:
            raise DataValidationError(
                f"Bybit kline row at index {index} has {len(row)} fields; "
                f"expected at least {_MIN_KLINE_ROW_FIELDS}."
            )
        validated_rows.append(row)

    return validated_rows


def is_completed_kline(raw_row: list[str], *, interval_seconds: int, now_utc: datetime) -> bool:
    """
    Determine whether a raw Bybit kline row represents a fully closed
    candle.

    Bybit's kline endpoint carries no explicit "closed" flag (unlike
    OKX's "confirm" field): the most recent row can still be an
    in-progress candle whose closePrice is simply "the last traded price
    so far." Completeness is instead inferred: a candle starting at
    `startTime` for a bar of `interval_seconds` duration is only fully
    closed once `startTime + interval_seconds <= now_utc`.

    Raises:
        DataValidationError: If the row's startTime field is missing or
            not a valid integer millisecond timestamp.
    """
    if len(raw_row) < _MIN_KLINE_ROW_FIELDS:
        raise DataValidationError(
            f"Kline row has {len(raw_row)} fields; expected at least {_MIN_KLINE_ROW_FIELDS}."
        )

    try:
        start_time_ms = int(raw_row[0])
    except (TypeError, ValueError) as exc:
        raise DataValidationError(
            f"Unable to convert kline startTime '{raw_row[0]}' to an integer."
        ) from exc

    start_time = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)
    close_time = start_time.timestamp() + interval_seconds
    return close_time <= now_utc.timestamp()
