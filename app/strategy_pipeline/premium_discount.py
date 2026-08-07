"""
Premium/Discount dealing-range filter: an additional condition folded
into Stage 4 (IFVG) entry-location gating.

Reuses the existing swing range already available on
`MarketStructureResult` (`latest_swing_high`/`latest_swing_low`, see
app/market_structure/results.py) -- this module performs no new swing
detection. The dealing range's equilibrium (50%) midpoint splits it
into a discount half (below equilibrium) and a premium half (above
equilibrium); BUY is only permitted from the discount half, SELL only
from the premium half. Uses the existing
`DEALING_RANGE_EQUILIBRIUM_TOLERANCE_RATIO` threshold (already defined
in app/config/thresholds.py for this exact purpose but previously
unused) as a small tolerance band around the exact midpoint, so a price
sitting right at equilibrium is not arbitrarily forced into either
zone.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.config.thresholds import DEALING_RANGE_EQUILIBRIUM_TOLERANCE_RATIO
from app.market_structure.results import MarketStructureResult


class PremiumDiscountResult(BaseModel):
    """Result of the Premium/Discount dealing-range filter check."""

    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: str


def evaluate_premium_discount_zone(
    structure: Optional[MarketStructureResult],
    entry_price: float,
    expected_direction: str,
) -> PremiumDiscountResult:
    """
    Check that `entry_price` sits in the dealing-range half required by
    `expected_direction`: BUY requires the discount half (below
    equilibrium), SELL requires the premium half (above equilibrium).

    The dealing range is the span between the latest confirmed swing
    high and swing low on `structure`. Passes only when both swings are
    available, span a positive range, and `entry_price` falls strictly
    on the required side of the equilibrium midpoint (outside the
    configured tolerance band around it).
    """
    if structure is None or structure.latest_swing_high is None or structure.latest_swing_low is None:
        return PremiumDiscountResult(
            passed=False,
            reason="Swing range is unavailable to determine the dealing range.",
        )

    swing_high = structure.latest_swing_high.price
    swing_low = structure.latest_swing_low.price
    dealing_range = swing_high - swing_low
    if dealing_range <= 0:
        return PremiumDiscountResult(
            passed=False,
            reason="Swing high/low do not form a valid positive dealing range.",
        )

    equilibrium = swing_low + (dealing_range / 2.0)
    tolerance = dealing_range * DEALING_RANGE_EQUILIBRIUM_TOLERANCE_RATIO
    discount_boundary = equilibrium - tolerance
    premium_boundary = equilibrium + tolerance

    if expected_direction == "BUY":
        if entry_price < discount_boundary:
            return PremiumDiscountResult(
                passed=True,
                reason="Entry price is in the discount zone, agreeing with the BUY direction.",
            )
        return PremiumDiscountResult(
            passed=False,
            reason="Entry price is not in the discount zone of the dealing range.",
        )

    if entry_price > premium_boundary:
        return PremiumDiscountResult(
            passed=True,
            reason="Entry price is in the premium zone, agreeing with the SELL direction.",
        )
    return PremiumDiscountResult(
        passed=False,
        reason="Entry price is not in the premium zone of the dealing range.",
    )
