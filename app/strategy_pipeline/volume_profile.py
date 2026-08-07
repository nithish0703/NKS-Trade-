"""
Volume Profile sub-check, part of Stage 5 (Volume Profile + CVD).

Builds a rolling Volume Profile from the entry-timeframe candle window
and confirms the expected direction against its structure:

  BUY:  entry is near a strong HVN support, or price rejected an LVN
        and closed back into the value area.
  SELL: entry is near a strong HVN resistance, or price rejected an
        LVN from above and closed back into the value area.
  Else: reject.

This sub-check needs no external data feed: the entry-timeframe
candles already carry OHLCV volume, so the profile is built entirely
from data the pipeline has already fetched for earlier stages.
"""

from enum import Enum
from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict

from app.config.thresholds import (
    VOLUME_PROFILE_BINS,
    VOLUME_PROFILE_ENABLED,
    VOLUME_PROFILE_HVN_THRESHOLD_RATIO,
    VOLUME_PROFILE_LOOKBACK,
    VOLUME_PROFILE_LVN_THRESHOLD_RATIO,
    VOLUME_PROFILE_PROXIMITY_RATIO,
    VOLUME_PROFILE_VALUE_AREA_PERCENT,
)
from app.models.candle import Candle
from app.strategy_pipeline.confirmation_status import ConfirmationStatus


class VolumeProfileDecision(str, Enum):
    """Structured outcome of the Volume Profile sub-check."""

    PASS_HVN_SUPPORT = "PASS_HVN_SUPPORT"
    PASS_HVN_RESISTANCE = "PASS_HVN_RESISTANCE"
    PASS_LVN_REJECTION = "PASS_LVN_REJECTION"
    FAIL_HVN_RESISTANCE = "FAIL_HVN_RESISTANCE"
    FAIL_HVN_SUPPORT = "FAIL_HVN_SUPPORT"
    FAIL_NO_CONFIRMATION = "FAIL_NO_CONFIRMATION"
    UNAVAILABLE = "UNAVAILABLE"


class VolumeNode(BaseModel):
    """A single High/Low Volume Node: a price level and its bin volume."""

    model_config = ConfigDict(frozen=True)

    price: float
    volume: float


class VolumeProfile(BaseModel):
    """Rolling Volume Profile computed over a candle lookback window."""

    model_config = ConfigDict(frozen=True)

    poc: float
    vah: float
    val: float
    hvn_nodes: list[VolumeNode]
    lvn_nodes: list[VolumeNode]


class VolumeProfileConfirmationResult(BaseModel):
    """Result of the Volume Profile sub-check."""

    model_config = ConfigDict(frozen=True)

    passed: bool
    status: ConfirmationStatus
    decision: VolumeProfileDecision
    reason: str
    nearest_hvn: Optional[float] = None
    nearest_lvn: Optional[float] = None
    distance_to_poc: Optional[float] = None
    distance_to_vah: Optional[float] = None
    distance_to_val: Optional[float] = None


def build_volume_profile(
    candles: Sequence[Candle],
    *,
    bins: int = VOLUME_PROFILE_BINS,
    value_area_percent: float = VOLUME_PROFILE_VALUE_AREA_PERCENT,
    hvn_threshold_ratio: float = VOLUME_PROFILE_HVN_THRESHOLD_RATIO,
    lvn_threshold_ratio: float = VOLUME_PROFILE_LVN_THRESHOLD_RATIO,
) -> Optional[VolumeProfile]:
    """
    Build a rolling Volume Profile over `candles`: a per-price-bin
    volume histogram, its Point of Control (POC), Value Area High/Low
    (VAH/VAL), and its High/Low Volume Nodes (HVN/LVN).

    Each candle's volume is spread evenly across every bin its
    high/low range overlaps, rather than assigned only to its close --
    a candle trades across its whole range, not just at one price.
    Returns None when a profile cannot be built at all: fewer than 2
    candles, a zero-width price range, or zero total volume.
    """
    if len(candles) < 2:
        return None

    price_min = min(candle.low for candle in candles)
    price_max = max(candle.high for candle in candles)
    if price_max <= price_min:
        return None

    bin_size = (price_max - price_min) / bins
    bin_volume = [0.0] * bins

    for candle in candles:
        low_bin = max(0, min(int((candle.low - price_min) / bin_size), bins - 1))
        high_bin = max(0, min(int((candle.high - price_min) / bin_size), bins - 1))
        span = high_bin - low_bin + 1
        share = candle.volume / span
        for bin_index in range(low_bin, high_bin + 1):
            bin_volume[bin_index] += share

    total_volume = sum(bin_volume)
    if total_volume <= 0:
        return None

    def bin_price(index: int) -> float:
        return price_min + (index + 0.5) * bin_size

    poc_index = max(range(bins), key=lambda i: bin_volume[i])
    poc = bin_price(poc_index)

    # Value area: expand outward from the POC bin, each step adding
    # whichever adjacent bin (low or high side) holds more volume,
    # until the accumulated volume reaches `value_area_percent` of the
    # total -- the standard Market Profile value-area construction.
    target_volume = total_volume * (value_area_percent / 100.0)
    included_low, included_high = poc_index, poc_index
    accumulated = bin_volume[poc_index]
    low_edge, high_edge = poc_index - 1, poc_index + 1
    while accumulated < target_volume and (low_edge >= 0 or high_edge < bins):
        low_volume = bin_volume[low_edge] if low_edge >= 0 else -1.0
        high_volume = bin_volume[high_edge] if high_edge < bins else -1.0
        if high_volume >= low_volume:
            included_high = high_edge
            accumulated += high_volume
            high_edge += 1
        else:
            included_low = low_edge
            accumulated += low_volume
            low_edge -= 1
    val = bin_price(included_low) - bin_size / 2.0
    vah = bin_price(included_high) + bin_size / 2.0

    max_bin_volume = bin_volume[poc_index]
    hvn_nodes = [
        VolumeNode(price=bin_price(index), volume=volume)
        for index, volume in enumerate(bin_volume)
        if volume > 0 and volume >= hvn_threshold_ratio * max_bin_volume
    ]
    lvn_nodes = [
        VolumeNode(price=bin_price(index), volume=volume)
        for index, volume in enumerate(bin_volume)
        if volume > 0 and volume <= lvn_threshold_ratio * max_bin_volume
    ]

    return VolumeProfile(poc=poc, vah=vah, val=val, hvn_nodes=hvn_nodes, lvn_nodes=lvn_nodes)


def _nearest_node(nodes: Sequence[VolumeNode], price: float) -> Optional[VolumeNode]:
    if not nodes:
        return None
    return min(nodes, key=lambda node: abs(node.price - price))


def evaluate_volume_profile_confirmation(
    candles: Sequence[Candle],
    *,
    expected_direction: str,
    enabled: bool = VOLUME_PROFILE_ENABLED,
    lookback: int = VOLUME_PROFILE_LOOKBACK,
    bins: int = VOLUME_PROFILE_BINS,
    value_area_percent: float = VOLUME_PROFILE_VALUE_AREA_PERCENT,
    proximity_ratio: float = VOLUME_PROFILE_PROXIMITY_RATIO,
) -> VolumeProfileConfirmationResult:
    """
    Confirm that `expected_direction` is supported by the rolling
    Volume Profile's structure, built over the most recent `lookback`
    candles:

      - BUY passes when entry sits near a strong HVN support (the
        nearest HVN is at or below current price) or price rejected an
        LVN and closed back into the value area.
      - SELL passes when entry sits near a strong HVN resistance (the
        nearest HVN is at or above current price) or price rejected an
        LVN from above and closed back into the value area.

    Never fabricates a profile from insufficient data: fewer than 2
    candles, or a profile that can't be built at all (flat range or no
    volume), is reported as status=UNAVAILABLE (data problem), never as
    status=DISAGREED (market-outcome problem).
    """
    if not enabled:
        return VolumeProfileConfirmationResult(
            passed=False,
            status=ConfirmationStatus.UNAVAILABLE,
            decision=VolumeProfileDecision.UNAVAILABLE,
            reason="Volume Profile confirmation is disabled by configuration.",
        )

    window = list(candles)[-lookback:]
    if len(window) < 2:
        return VolumeProfileConfirmationResult(
            passed=False,
            status=ConfirmationStatus.UNAVAILABLE,
            decision=VolumeProfileDecision.UNAVAILABLE,
            reason="Insufficient candle history to build a Volume Profile.",
        )

    profile = build_volume_profile(
        window, bins=bins, value_area_percent=value_area_percent
    )
    if profile is None:
        return VolumeProfileConfirmationResult(
            passed=False,
            status=ConfirmationStatus.UNAVAILABLE,
            decision=VolumeProfileDecision.UNAVAILABLE,
            reason="Volume Profile could not be built (flat price range or no volume).",
        )

    current_price = window[-1].close
    previous_price = window[-2].close
    proximity = current_price * proximity_ratio

    nearest_hvn_node = _nearest_node(profile.hvn_nodes, current_price)
    nearest_lvn_node = _nearest_node(profile.lvn_nodes, current_price)
    nearest_hvn = nearest_hvn_node.price if nearest_hvn_node is not None else None
    nearest_lvn = nearest_lvn_node.price if nearest_lvn_node is not None else None
    distance_to_poc = current_price - profile.poc
    distance_to_vah = current_price - profile.vah
    distance_to_val = current_price - profile.val

    def build_result(
        passed: bool, status: ConfirmationStatus, decision: VolumeProfileDecision, reason: str
    ) -> VolumeProfileConfirmationResult:
        return VolumeProfileConfirmationResult(
            passed=passed,
            status=status,
            decision=decision,
            reason=reason,
            nearest_hvn=nearest_hvn,
            nearest_lvn=nearest_lvn,
            distance_to_poc=distance_to_poc,
            distance_to_vah=distance_to_vah,
            distance_to_val=distance_to_val,
        )

    inside_value_area = profile.val <= current_price <= profile.vah
    near_lvn = nearest_lvn_node is not None and abs(current_price - nearest_lvn_node.price) <= proximity
    price_moved_up = current_price > previous_price
    price_moved_down = current_price < previous_price
    rejected_lvn = (
        near_lvn
        and inside_value_area
        and (
            (expected_direction == "BUY" and price_moved_up)
            or (expected_direction == "SELL" and price_moved_down)
        )
    )

    near_hvn_at_or_below = (
        nearest_hvn_node is not None
        and nearest_hvn_node.price <= current_price
        and abs(current_price - nearest_hvn_node.price) <= proximity
    )
    near_hvn_at_or_above = (
        nearest_hvn_node is not None
        and nearest_hvn_node.price >= current_price
        and abs(current_price - nearest_hvn_node.price) <= proximity
    )

    if expected_direction == "BUY":
        if near_hvn_at_or_below:
            return build_result(
                True,
                ConfirmationStatus.CONFIRMED,
                VolumeProfileDecision.PASS_HVN_SUPPORT,
                f"Entry is near a strong HVN support at {nearest_hvn:.6g}, confirming BUY.",
            )
        if rejected_lvn:
            return build_result(
                True,
                ConfirmationStatus.CONFIRMED,
                VolumeProfileDecision.PASS_LVN_REJECTION,
                f"Price rejected a Low Volume Node at {nearest_lvn:.6g} and moved back into "
                "the value area, confirming BUY.",
            )
        if near_hvn_at_or_above:
            return build_result(
                False,
                ConfirmationStatus.DISAGREED,
                VolumeProfileDecision.FAIL_HVN_RESISTANCE,
                f"Price is directly below a major HVN resistance at {nearest_hvn:.6g}; "
                "BUY disagrees with Volume Profile structure.",
            )
        return build_result(
            False,
            ConfirmationStatus.DISAGREED,
            VolumeProfileDecision.FAIL_NO_CONFIRMATION,
            "No significant Volume Profile confirmation exists for BUY "
            + (
                "(entry sits inside poor low-volume structure)."
                if near_lvn
                else "."
            ),
        )

    # SELL
    if near_hvn_at_or_above:
        return build_result(
            True,
            ConfirmationStatus.CONFIRMED,
            VolumeProfileDecision.PASS_HVN_RESISTANCE,
            f"Entry is near a strong HVN resistance at {nearest_hvn:.6g}, confirming SELL.",
        )
    if rejected_lvn:
        return build_result(
            True,
            ConfirmationStatus.CONFIRMED,
            VolumeProfileDecision.PASS_LVN_REJECTION,
            f"Price rejected a Low Volume Node at {nearest_lvn:.6g} from above and moved back "
            "into the value area, confirming SELL.",
        )
    if near_hvn_at_or_below:
        return build_result(
            False,
            ConfirmationStatus.DISAGREED,
            VolumeProfileDecision.FAIL_HVN_SUPPORT,
            f"Price sits on strong HVN support at {nearest_hvn:.6g}; "
            "SELL disagrees with Volume Profile structure.",
        )
    return build_result(
        False,
        ConfirmationStatus.DISAGREED,
        VolumeProfileDecision.FAIL_NO_CONFIRMATION,
        "No meaningful Volume Profile confirmation exists for SELL "
        + (
            "(entry sits inside poor low-volume structure)."
            if near_lvn
            else "."
        ),
    )
