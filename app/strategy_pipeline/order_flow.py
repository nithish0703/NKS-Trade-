"""
Stage 5: Volume Profile + CVD (order flow confidence).

A soft confidence layer, not a gate: combines the Volume Profile and
CVD sub-checks into a HIGH/MEDIUM/LOW confidence tier for the trade
idea, but never rejects an otherwise-valid setup. This mirrors how
discretionary/institutional-style traders typically use these two
tools -- as confluence that grades an already-structurally-valid setup
(Market Structure, Liquidity Sweep, BOS, IFVG), not as a mandatory
veto on top of it. Neither sub-check's own algorithm is affected by
this module; only how their `passed` outcomes are combined changes.
"""

from enum import Enum
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
from app.strategy_pipeline.cvd import CvdConfirmationResult, evaluate_cvd_confirmation
from app.strategy_pipeline.volume_profile import (
    VolumeProfileConfirmationResult,
    evaluate_volume_profile_confirmation,
)


class OrderFlowConfidence(str, Enum):
    """
    Confidence tier for how strongly Volume Profile and CVD support the
    trade idea. Never blocks a trade by itself -- Stages 1-4 (HTF Bias,
    Liquidity Sweep, BOS, IFVG) remain the hard-mandatory gate; this is
    confluence information carried alongside an already-valid setup.
    """

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class OrderFlowResult(BaseModel):
    """
    Result of the combined Volume Profile + CVD confidence check.

    `volume_profile` and `cvd` are always populated (each sub-check
    always runs and always returns a result, unaffected by this
    module), so a caller can inspect exactly which specific sub-check
    agreed even when overall confidence is not HIGH.

    `confidence` is the headline output:
      - HIGH: both Volume Profile and CVD confirm the trade idea.
      - MEDIUM: exactly one of the two confirms it.
      - LOW: neither confirms it (a genuine disagreement or a data
        problem for both -- distinguishable via `volume_profile.status`
        / `cvd.status` if a caller needs that detail).

    `passed` is kept, with its historical meaning unchanged (True only
    when both sub-checks agree), purely for any caller still reading it
    -- it is no longer used by the pipeline engine to reject a setup.
    """

    model_config = ConfigDict(frozen=True)

    passed: bool
    confidence: OrderFlowConfidence
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
    Evaluate order-flow confidence for `expected_direction`: run the
    Volume Profile and CVD sub-checks (both always run, neither
    short-circuits the other) and combine their agreement into a
    HIGH/MEDIUM/LOW confidence tier. Never returns a "rejected" outcome
    -- the caller decides what, if anything, to do with a LOW tier.
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

    agreeing = _agreeing_names(volume_profile_result, cvd_result)
    not_agreeing = _not_agreeing_names(volume_profile_result, cvd_result)
    passed = len(agreeing) == 2

    if len(agreeing) == 2:
        confidence = OrderFlowConfidence.HIGH
        reason = (
            f"HIGH_CONFIDENCE: Volume Profile and CVD both confirm the "
            f"{expected_direction} trade idea."
        )
    elif len(agreeing) == 1:
        confidence = OrderFlowConfidence.MEDIUM
        reason = (
            f"MEDIUM_CONFIDENCE: {agreeing[0]} confirms the {expected_direction} "
            f"trade idea; {not_agreeing[0]} does not "
            f"({_sub_result(not_agreeing[0], volume_profile_result, cvd_result).reason})"
        )
    else:
        confidence = OrderFlowConfidence.LOW
        reason = (
            f"LOW_CONFIDENCE: neither Volume Profile nor CVD confirms the "
            f"{expected_direction} trade idea."
        )

    return OrderFlowResult(
        passed=passed,
        confidence=confidence,
        reason=reason,
        volume_profile=volume_profile_result,
        cvd=cvd_result,
    )


def _agreeing_names(
    volume_profile_result: VolumeProfileConfirmationResult, cvd_result: CvdConfirmationResult
) -> list[str]:
    names = []
    if volume_profile_result.passed:
        names.append("Volume Profile")
    if cvd_result.passed:
        names.append("CVD")
    return names


def _not_agreeing_names(
    volume_profile_result: VolumeProfileConfirmationResult, cvd_result: CvdConfirmationResult
) -> list[str]:
    names = []
    if not volume_profile_result.passed:
        names.append("Volume Profile")
    if not cvd_result.passed:
        names.append("CVD")
    return names


def _sub_result(
    name: str,
    volume_profile_result: VolumeProfileConfirmationResult,
    cvd_result: CvdConfirmationResult,
):
    return volume_profile_result if name == "Volume Profile" else cvd_result
