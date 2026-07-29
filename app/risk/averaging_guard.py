"""
Prevents averaging into an existing losing position.
"""

from typing import Sequence

from app.models.validation_result import ValidationResult

_LAYER_NAME = "AVERAGING_GUARD"
_REQUIRED_FIELDS = ("symbol", "direction", "entry_price", "current_price", "status")


def _get_field(position: object, name: str):
    if isinstance(position, dict):
        return position.get(name)
    return getattr(position, name, None)


def _has_all_required_fields(position: object) -> bool:
    for field in _REQUIRED_FIELDS:
        value = _get_field(position, field)
        if value is None:
            return False
    return True


def _is_losing(position: object) -> bool:
    direction = _get_field(position, "direction")
    entry_price = _get_field(position, "entry_price")
    current_price = _get_field(position, "current_price")

    if direction == "BUY":
        return current_price < entry_price
    if direction == "SELL":
        return current_price > entry_price
    return False


class AveragingGuard:
    """
    Rejects opening a new position for a symbol/direction when an
    active losing position already exists for that same symbol and
    direction, preventing averaging into a losing trade. Uses only
    explicitly supplied active-position state; never infers profit or
    loss from incomplete data.
    """

    def validate(
        self, symbol: str, direction: str, active_positions: Sequence[object]
    ) -> ValidationResult:
        """
        Validate that no losing same-symbol, same-direction position
        already exists.

        Failure codes:
            - AVERAGING_DATA_MISSING
            - LOSING_POSITION_AVERAGING_REJECTED
        """
        matching_positions = []
        for position in active_positions:
            position_symbol = _get_field(position, "symbol")
            position_direction = _get_field(position, "direction")
            if position_symbol == symbol and position_direction == direction:
                matching_positions.append(position)

        if not matching_positions:
            return ValidationResult.success(
                layer_name=_LAYER_NAME,
                reason="NO_LOSING_POSITION_AVERAGING",
                score=0.0,
            )

        for position in matching_positions:
            if not _has_all_required_fields(position):
                return ValidationResult.failure(
                    layer_name=_LAYER_NAME,
                    reason="Required active-position data is missing; cannot safely evaluate averaging.",
                    rejection_code="AVERAGING_DATA_MISSING",
                    score=0.0,
                )

        for position in matching_positions:
            if _is_losing(position):
                return ValidationResult.failure(
                    layer_name=_LAYER_NAME,
                    reason=(
                        f"An active losing {direction} position already exists for {symbol}; "
                        "averaging is rejected."
                    ),
                    rejection_code="LOSING_POSITION_AVERAGING_REJECTED",
                    score=0.0,
                )

        return ValidationResult.success(
            layer_name=_LAYER_NAME,
            reason="NO_LOSING_POSITION_AVERAGING",
            score=0.0,
        )
