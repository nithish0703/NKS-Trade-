"""
Enforces the maximum number of simultaneously active trades.
"""

from app.models.validation_result import ValidationResult

_LAYER_NAME = "ACTIVE_TRADE_GUARD"


class ActiveTradeGuard:
    """
    Validates that the number of currently active trades is below the
    configured maximum before allowing a new trade to be considered.
    Never modifies or closes existing positions.
    """

    def __init__(self, maximum_active_trades: int) -> None:
        self._maximum_active_trades = maximum_active_trades

    @property
    def maximum_active_trades(self) -> int:
        """The configured maximum number of simultaneously active trades."""
        return self._maximum_active_trades

    def validate(self, active_trade_count: int) -> ValidationResult:
        """
        Validate the active-trade count.

        Failure codes:
            - INVALID_ACTIVE_TRADE_COUNT
            - MAXIMUM_ACTIVE_TRADES_REACHED
        """
        if active_trade_count < 0:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason="active_trade_count cannot be negative.",
                rejection_code="INVALID_ACTIVE_TRADE_COUNT",
                score=0.0,
            )

        if active_trade_count >= self._maximum_active_trades:
            return ValidationResult.failure(
                layer_name=_LAYER_NAME,
                reason=(
                    f"Active trade count {active_trade_count} has reached the maximum "
                    f"of {self._maximum_active_trades}."
                ),
                rejection_code="MAXIMUM_ACTIVE_TRADES_REACHED",
                score=0.0,
            )

        return ValidationResult.success(
            layer_name=_LAYER_NAME,
            reason="ACTIVE_TRADE_LIMIT_AVAILABLE",
            score=0.0,
        )
