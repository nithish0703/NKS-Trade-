"""
Stage 5: OI + CVD (Order flow agrees?).

Combines the Open Interest and CVD sub-checks into a single stage:
order flow only "agrees" with the trade idea when BOTH confirm it
independently. Either one disagreeing rejects the setup -- this is
never an either/or or a weighted vote between the two.
"""

from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict

from app.data.open_interest_point import OpenInterestPoint
from app.models.candle import Candle
from app.strategy_pipeline.cvd import CvdConfirmationResult, evaluate_cvd_confirmation
from app.strategy_pipeline.open_interest import (
    OpenInterestConfirmationResult,
    evaluate_open_interest_confirmation,
)


class OrderFlowResult(BaseModel):
    """
    Result of the combined OI + CVD (order flow) stage.

    Both `open_interest` and `cvd` are always populated (each sub-check
    always runs and always returns a result), so a caller can inspect
    which specific sub-check disagreed even when `passed` is False.
    """

    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: str
    open_interest: OpenInterestConfirmationResult
    cvd: CvdConfirmationResult


def evaluate_order_flow(
    candles: Sequence[Candle],
    open_interest_history: Sequence[OpenInterestPoint],
    *,
    expected_direction: str,
    cvd_left_strength: int = 3,
    cvd_right_strength: int = 3,
) -> OrderFlowResult:
    """
    Evaluate whether order flow agrees with `expected_direction`: both
    the Open Interest and CVD sub-checks must independently pass.

    Both sub-checks always run (neither short-circuits the other), so
    the returned result always reports both outcomes regardless of
    which one (or both) disagreed.
    """
    open_interest_result = evaluate_open_interest_confirmation(
        candles, open_interest_history, expected_direction=expected_direction
    )
    cvd_result = evaluate_cvd_confirmation(
        candles,
        expected_direction=expected_direction,
        left_strength=cvd_left_strength,
        right_strength=cvd_right_strength,
    )

    if open_interest_result.passed and cvd_result.passed:
        return OrderFlowResult(
            passed=True,
            reason="Open Interest and CVD both confirm order flow agrees with the trade idea.",
            open_interest=open_interest_result,
            cvd=cvd_result,
        )

    disagreeing = []
    if not open_interest_result.passed:
        disagreeing.append("Open Interest")
    if not cvd_result.passed:
        disagreeing.append("CVD")

    return OrderFlowResult(
        passed=False,
        reason=(
            f"Order flow disagrees: {' and '.join(disagreeing)} did not confirm "
            f"the {expected_direction} trade idea."
        ),
        open_interest=open_interest_result,
        cvd=cvd_result,
    )
