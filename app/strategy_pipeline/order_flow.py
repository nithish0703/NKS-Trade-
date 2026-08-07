"""
Stage 5: Volume Profile + CVD (Order flow agrees?).

Combines the Volume Profile and CVD sub-checks into a single stage:
order flow only "agrees" with the trade idea when BOTH confirm it
independently. Either one disagreeing rejects the setup -- this is
never an either/or or a weighted vote between the two.
"""

from typing import Sequence

from pydantic import BaseModel, ConfigDict

from app.config.thresholds import (
    VOLUME_PROFILE_BINS,
    VOLUME_PROFILE_ENABLED,
    VOLUME_PROFILE_LOOKBACK,
    VOLUME_PROFILE_PROXIMITY_RATIO,
    VOLUME_PROFILE_VALUE_AREA_PERCENT,
)
from app.models.candle import Candle
from app.strategy_pipeline.confirmation_status import ConfirmationStatus
from app.strategy_pipeline.cvd import CvdConfirmationResult, evaluate_cvd_confirmation
from app.strategy_pipeline.volume_profile import (
    VolumeProfileConfirmationResult,
    evaluate_volume_profile_confirmation,
)


class OrderFlowResult(BaseModel):
    """
    Result of the combined Volume Profile + CVD (order flow) stage.

    Both `volume_profile` and `cvd` are always populated (each
    sub-check always runs and always returns a result), so a caller
    can inspect which specific sub-check disagreed even when `passed`
    is False.

    `status` is the authoritative combined outcome:
      - CONFIRMED: both sub-checks confirmed.
      - DISAGREED: at least one sub-check genuinely disagreed (a real
        directional conflict, e.g. entry sitting below HVN resistance,
        or a wrong CVD swing pattern). This must still reject the
        setup exactly as before -- a real disagreement is never
        downgraded because the other sub-check was also unavailable.
      - UNAVAILABLE: neither sub-check disagreed, but at least one
        could not reach a conclusion for lack of data (a profile that
        couldn't be built, a thin history, or too few CVD swing
        points). Also rejects the setup (missing data is never treated
        as confirmation), but with a reason that is clearly distinct
        from DISAGREED so infra/data gaps and genuinely weak setups
        don't collapse into identical logs/notifications/stored reasons.
    """

    model_config = ConfigDict(frozen=True)

    passed: bool
    status: ConfirmationStatus
    reason: str
    volume_profile: VolumeProfileConfirmationResult
    cvd: CvdConfirmationResult


def evaluate_order_flow(
    candles: Sequence[Candle],
    *,
    expected_direction: str,
    cvd_left_strength: int = 3,
    cvd_right_strength: int = 3,
    volume_profile_enabled: bool = VOLUME_PROFILE_ENABLED,
    volume_profile_lookback: int = VOLUME_PROFILE_LOOKBACK,
    volume_profile_bins: int = VOLUME_PROFILE_BINS,
    volume_profile_value_area_percent: float = VOLUME_PROFILE_VALUE_AREA_PERCENT,
    volume_profile_proximity_ratio: float = VOLUME_PROFILE_PROXIMITY_RATIO,
) -> OrderFlowResult:
    """
    Evaluate whether order flow agrees with `expected_direction`: both
    the Volume Profile and CVD sub-checks must independently pass.

    Both sub-checks always run (neither short-circuits the other), so
    the returned result always reports both outcomes regardless of
    which one (or both) disagreed.
    """
    volume_profile_result = evaluate_volume_profile_confirmation(
        candles,
        expected_direction=expected_direction,
        enabled=volume_profile_enabled,
        lookback=volume_profile_lookback,
        bins=volume_profile_bins,
        value_area_percent=volume_profile_value_area_percent,
        proximity_ratio=volume_profile_proximity_ratio,
    )
    cvd_result = evaluate_cvd_confirmation(
        candles,
        expected_direction=expected_direction,
        left_strength=cvd_left_strength,
        right_strength=cvd_right_strength,
    )

    if volume_profile_result.passed and cvd_result.passed:
        return OrderFlowResult(
            passed=True,
            status=ConfirmationStatus.CONFIRMED,
            reason="Volume Profile and CVD both confirm order flow agrees with the trade idea.",
            volume_profile=volume_profile_result,
            cvd=cvd_result,
        )

    disagreeing = _sub_checks_with_status(volume_profile_result, cvd_result, ConfirmationStatus.DISAGREED)
    if disagreeing:
        return OrderFlowResult(
            passed=False,
            status=ConfirmationStatus.DISAGREED,
            reason=(
                f"ORDER_FLOW_DISAGREED: {' and '.join(disagreeing)} did not confirm "
                f"the {expected_direction} trade idea."
            ),
            volume_profile=volume_profile_result,
            cvd=cvd_result,
        )

    # Neither sub-check disagreed, so every non-passing sub-check here
    # is UNAVAILABLE -- a data problem, not a market disagreement.
    unavailable = _sub_checks_with_status(volume_profile_result, cvd_result, ConfirmationStatus.UNAVAILABLE)
    return OrderFlowResult(
        passed=False,
        status=ConfirmationStatus.UNAVAILABLE,
        reason=(
            f"ORDER_FLOW_DATA_UNAVAILABLE: {' and '.join(unavailable)} could not be evaluated "
            "(insufficient data); order flow is unconfirmed, not disagreeing."
        ),
        volume_profile=volume_profile_result,
        cvd=cvd_result,
    )


def _sub_checks_with_status(
    volume_profile_result: VolumeProfileConfirmationResult,
    cvd_result: CvdConfirmationResult,
    status: ConfirmationStatus,
) -> list[str]:
    names = []
    if volume_profile_result.status == status:
        names.append("Volume Profile")
    if cvd_result.status == status:
        names.append("CVD")
    return names
