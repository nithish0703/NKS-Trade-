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
    ConfirmationStatus,
    OpenInterestConfirmationResult,
    evaluate_open_interest_confirmation,
)


class OrderFlowResult(BaseModel):
    """
    Result of the combined OI + CVD (order flow) stage.

    Both `open_interest` and `cvd` are always populated (each sub-check
    always runs and always returns a result), so a caller can inspect
    which specific sub-check disagreed even when `passed` is False.

    `status` is the authoritative combined outcome:
      - CONFIRMED: both sub-checks confirmed.
      - DISAGREED: at least one sub-check genuinely disagreed (a real
        directional conflict, e.g. short covering / long liquidation,
        or a wrong CVD swing pattern). This must still reject the
        setup exactly as before -- a real disagreement is never
        downgraded because the other sub-check was also unavailable.
      - UNAVAILABLE: neither sub-check disagreed, but at least one
        could not reach a conclusion for lack of data (a fetch
        failure, a thin history, or too few CVD swing points). Also
        rejects the setup (missing data is never treated as
        confirmation), but with a reason that is clearly distinct from
        DISAGREED so infra failures and genuinely weak setups don't
        collapse into identical logs/notifications/stored reasons.
    """

    model_config = ConfigDict(frozen=True)

    passed: bool
    status: ConfirmationStatus
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
    open_interest_fetch_failed: bool = False,
) -> OrderFlowResult:
    """
    Evaluate whether order flow agrees with `expected_direction`: both
    the Open Interest and CVD sub-checks must independently pass.

    Both sub-checks always run (neither short-circuits the other), so
    the returned result always reports both outcomes regardless of
    which one (or both) disagreed.

    `open_interest_fetch_failed` lets the caller (the pipeline engine)
    report that `open_interest_history` is empty because fetching it
    failed/timed out after retrying, rather than because the provider
    validly returned no data (e.g. a spot-only symbol). This never
    changes the OI sub-check's own pass/fail logic -- an empty history
    is UNAVAILABLE either way -- it only sharpens the combined reason
    string so a trader can tell "OI fetch failed" apart from "OI
    history is genuinely thin" without reading source code.
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
            status=ConfirmationStatus.CONFIRMED,
            reason="Open Interest and CVD both confirm order flow agrees with the trade idea.",
            open_interest=open_interest_result,
            cvd=cvd_result,
        )

    disagreeing = _sub_checks_with_status(open_interest_result, cvd_result, ConfirmationStatus.DISAGREED)
    if disagreeing:
        return OrderFlowResult(
            passed=False,
            status=ConfirmationStatus.DISAGREED,
            reason=(
                f"ORDER_FLOW_DISAGREED: {' and '.join(disagreeing)} did not confirm "
                f"the {expected_direction} trade idea."
            ),
            open_interest=open_interest_result,
            cvd=cvd_result,
        )

    # Neither sub-check disagreed, so every non-passing sub-check here
    # is UNAVAILABLE -- a data problem, not a market disagreement.
    unavailable = _sub_checks_with_status(open_interest_result, cvd_result, ConfirmationStatus.UNAVAILABLE)
    unavailable_reason = (
        "the fetch failed after retrying"
        if open_interest_fetch_failed and "Open Interest" in unavailable
        else "insufficient data"
    )
    return OrderFlowResult(
        passed=False,
        status=ConfirmationStatus.UNAVAILABLE,
        reason=(
            f"ORDER_FLOW_DATA_UNAVAILABLE: {' and '.join(unavailable)} could not be evaluated "
            f"({unavailable_reason}); order flow is unconfirmed, not disagreeing."
        ),
        open_interest=open_interest_result,
        cvd=cvd_result,
    )


def _sub_checks_with_status(
    open_interest_result: OpenInterestConfirmationResult,
    cvd_result: CvdConfirmationResult,
    status: ConfirmationStatus,
) -> list[str]:
    names = []
    if open_interest_result.status == status:
        names.append("Open Interest")
    if cvd_result.status == status:
        names.append("CVD")
    return names
