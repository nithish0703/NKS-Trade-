"""
Computes risk-reward ratio for a trade setup.
"""

import math

from app.models.validation_result import ValidationResult

from app.risk.stop_loss import RiskCalculationError

_LAYER_NAME = "RISK_REWARD"


def calculate_risk_reward(
    direction: str, entry_price: float, stop_loss: float, take_profit: float
) -> float:
    """
    Calculate the risk-reward ratio for a trade setup.

    BUY: (take_profit - entry_price) / (entry_price - stop_loss)
    SELL: (entry_price - take_profit) / (stop_loss - entry_price)

    Raises:
        RiskCalculationError: If any price is non-positive, the stop or
            target is on the wrong side of entry for the direction, or
            the resulting risk distance is not strictly positive.
    """
    for name, value in (("entry_price", entry_price), ("stop_loss", stop_loss), ("take_profit", take_profit)):
        if value <= 0:
            raise RiskCalculationError(f"{name} must be positive, got {value}.")

    if direction == "BUY":
        if stop_loss >= entry_price:
            raise RiskCalculationError("BUY stop_loss must be below entry_price.")
        if take_profit <= entry_price:
            raise RiskCalculationError("BUY take_profit must be above entry_price.")
        risk_distance = entry_price - stop_loss
        reward_distance = take_profit - entry_price
    elif direction == "SELL":
        if stop_loss <= entry_price:
            raise RiskCalculationError("SELL stop_loss must be above entry_price.")
        if take_profit >= entry_price:
            raise RiskCalculationError("SELL take_profit must be below entry_price.")
        risk_distance = stop_loss - entry_price
        reward_distance = entry_price - take_profit
    else:
        raise RiskCalculationError(f"Unrecognized direction '{direction}'.")

    if risk_distance <= 0:
        raise RiskCalculationError("Risk distance must be greater than zero.")

    ratio = reward_distance / risk_distance

    if not math.isfinite(ratio) or ratio <= 0:
        raise RiskCalculationError("Calculated risk-reward ratio is not a finite positive value.")

    return ratio


def validate_minimum_risk_reward(
    risk_reward_ratio: float, minimum_ratio: float = 1.80
) -> ValidationResult:
    """
    Validate that `risk_reward_ratio` meets or exceeds `minimum_ratio`.

    Failure codes:
        - INVALID_RISK_REWARD
        - RISK_REWARD_BELOW_MINIMUM
    """
    if risk_reward_ratio is None or not math.isfinite(risk_reward_ratio) or risk_reward_ratio <= 0:
        return ValidationResult.failure(
            layer_name=_LAYER_NAME,
            reason="Risk-reward ratio is invalid.",
            rejection_code="INVALID_RISK_REWARD",
            score=0.0,
        )

    if risk_reward_ratio < minimum_ratio:
        return ValidationResult.failure(
            layer_name=_LAYER_NAME,
            reason=(
                "RISK_REWARD_BELOW_MINIMUM\n"
                f"Actual RR : {risk_reward_ratio:.2f}\n"
                f"Required RR : {minimum_ratio:.2f}"
            ),
            rejection_code="RISK_REWARD_BELOW_MINIMUM",
            score=0.0,
        )

    return ValidationResult.success(
        layer_name=_LAYER_NAME,
        reason="RISK_REWARD_VALID",
        score=0.0,
    )
