"""
Computes position sizing and risk exposure.
"""

import math
from typing import Optional

from app.risk.results import PositionRiskResult


class PositionRiskCalculator:
    """
    Calculates position size from account balance, a configured maximum
    risk percentage, and the stop-loss distance. Never calculates
    leverage and never rounds internally.
    """

    def __init__(self, maximum_risk_percentage: float) -> None:
        self._maximum_risk_percentage = maximum_risk_percentage

    def calculate(
        self,
        account_balance: float,
        entry_price: float,
        stop_loss: float,
        direction: str,
        requested_risk_percentage: Optional[float] = None,
    ) -> PositionRiskResult:
        """
        Calculate position size for a trade.

        Risk percentage defaults to the configured maximum when not
        supplied, and can never exceed it. Position size is
        risk_amount / stop_distance, where stop_distance is
        (entry - stop) for BUY or (stop - entry) for SELL.
        """
        risk_percentage = (
            requested_risk_percentage
            if requested_risk_percentage is not None
            else self._maximum_risk_percentage
        )

        if account_balance is None or account_balance <= 0:
            return self._invalid_result(
                account_balance, risk_percentage, entry_price, stop_loss,
                "Account balance must be positive.", "INVALID_ACCOUNT_BALANCE",
            )

        if risk_percentage is None or risk_percentage <= 0:
            return self._invalid_result(
                account_balance, risk_percentage, entry_price, stop_loss,
                "Risk percentage must be greater than zero.", "INVALID_RISK_PERCENTAGE",
            )

        if risk_percentage > self._maximum_risk_percentage:
            return self._invalid_result(
                account_balance, risk_percentage, entry_price, stop_loss,
                f"Risk percentage {risk_percentage} exceeds the maximum "
                f"{self._maximum_risk_percentage}.",
                "RISK_EXCEEDS_MAXIMUM",
            )

        if entry_price <= 0 or stop_loss <= 0:
            return self._invalid_result(
                account_balance, risk_percentage, entry_price, stop_loss,
                "Entry price and stop loss must be positive.", "INVALID_STOP_DISTANCE",
            )

        if direction == "BUY":
            stop_distance = entry_price - stop_loss
        elif direction == "SELL":
            stop_distance = stop_loss - entry_price
        else:
            return self._invalid_result(
                account_balance, risk_percentage, entry_price, stop_loss,
                f"Unrecognized direction '{direction}'.", "INVALID_STOP_DISTANCE",
            )

        if stop_distance <= 0:
            return self._invalid_result(
                account_balance, risk_percentage, entry_price, stop_loss,
                "Stop distance must be positive.", "INVALID_STOP_DISTANCE",
            )

        risk_amount = account_balance * (risk_percentage / 100)
        position_size = risk_amount / stop_distance

        if not math.isfinite(position_size) or position_size <= 0:
            return PositionRiskResult(
                account_balance=account_balance,
                risk_percentage=risk_percentage,
                risk_amount=risk_amount,
                entry_price=entry_price,
                stop_loss=stop_loss,
                stop_distance=stop_distance,
                position_size=None,
                valid=False,
                reason="Calculated position size is not finite and positive.",
                metadata={"rejection_code": "INVALID_POSITION_SIZE"},
            )

        return PositionRiskResult(
            account_balance=account_balance,
            risk_percentage=risk_percentage,
            risk_amount=risk_amount,
            entry_price=entry_price,
            stop_loss=stop_loss,
            stop_distance=stop_distance,
            position_size=position_size,
            valid=True,
            reason="VALID_POSITION_RISK",
        )

    @staticmethod
    def _invalid_result(
        account_balance: float,
        risk_percentage: float,
        entry_price: float,
        stop_loss: float,
        reason: str,
        rejection_code: str,
    ) -> PositionRiskResult:
        safe_balance = account_balance if account_balance and account_balance > 0 else 1.0
        safe_risk_percentage = risk_percentage if risk_percentage and risk_percentage > 0 else 0.01
        safe_entry = entry_price if entry_price and entry_price > 0 else 1.0
        safe_stop = stop_loss if stop_loss and stop_loss > 0 else 1.0
        return PositionRiskResult(
            account_balance=safe_balance,
            risk_percentage=safe_risk_percentage,
            risk_amount=None,
            entry_price=safe_entry,
            stop_loss=safe_stop,
            stop_distance=None,
            position_size=None,
            valid=False,
            reason=reason,
            metadata={"rejection_code": rejection_code},
        )
