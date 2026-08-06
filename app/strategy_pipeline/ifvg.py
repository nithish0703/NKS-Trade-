"""
Stage 4: IFVG (Good entry location?).

An Inverted Fair Value Gap (IFVG) is a Fair Value Gap that price later
closes fully through (invalidating it as a same-direction zone), which
then flips polarity and acts as an opposite-direction zone: a bullish
FVG (support gap) that gets closed below becomes bearish resistance; a
bearish FVG (resistance gap) that gets closed above becomes bullish
support. This is a distinct concept from a plain, never-broken FVG
(app.zones.fair_value_gap.FairValueGapDetector produces only the
latter) -- an IFVG requires detecting the later invalidation-and-flip,
which this module adds.

Good entry location requires, in order:
  1. A confirmed FVG zone exists (from the existing
     FairValueGapDetector) opposite the expected trade direction (a
     bearish FVG for a BUY, a bullish FVG for a SELL) -- the gap
     stands as resistance/support that must first be broken.
  2. Price later closes fully through that FVG in the expected
     direction (invalidating it), flipping it into an IFVG that now
     supports/resists in the expected direction.
  3. Price retests the flipped IFVG zone (trades back into its price
     range) after the flip candle, confirming it as a live entry
     location rather than a one-off close-through.

Never fabricates a flip or a retest from a zone that was never
detected, never invalidated, or never retested.
"""

from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict

from app.models.candle import Candle
from app.models.trade_zone import TradeZone


class IfvgResult(BaseModel):
    """
    Result of the IFVG (entry location) stage.

    `source_fvg` is the original Fair Value Gap that was later broken
    and flipped; `ifvg_zone` is the resulting inverted zone (same price
    range, opposite direction) once a flip has occurred. Both are only
    present when this stage passes.
    """

    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: str
    source_fvg: Optional[TradeZone] = None
    ifvg_zone: Optional[TradeZone] = None
    flip_candle_index: Optional[int] = None
    retest_candle_index: Optional[int] = None


def _opposite_direction(expected_direction: str) -> str:
    return "SELL" if expected_direction == "BUY" else "BUY"


def _closes_fully_through(candle: Candle, zone: TradeZone, *, expected_direction: str) -> bool:
    """
    A close-through invalidates the zone: for a bearish FVG being
    flipped bullish (BUY), price must close strictly above the zone's
    upper_price; for a bullish FVG being flipped bearish (SELL), price
    must close strictly below the zone's lower_price.
    """
    if expected_direction == "BUY":
        return candle.close > zone.upper_price
    return candle.close < zone.lower_price


def _retests_zone(candle: Candle, zone: TradeZone) -> bool:
    """A retest is any later candle whose range overlaps the zone's price band."""
    return candle.low <= zone.upper_price and candle.high >= zone.lower_price


def _find_flip_candle_index(
    candles: Sequence[Candle], zone: TradeZone, *, expected_direction: str
) -> Optional[int]:
    """
    First candle strictly after the zone's source candle that closes
    fully through it, invalidating/flipping it. Returns None if no such
    candle exists yet.
    """
    for index, candle in enumerate(candles):
        if candle.timestamp <= zone.source_candle_timestamp:
            continue
        if _closes_fully_through(candle, zone, expected_direction=expected_direction):
            return index
    return None


def _find_retest_candle_index(candles: Sequence[Candle], zone: TradeZone, *, after_index: int) -> Optional[int]:
    """First candle strictly after `after_index` whose range retests the zone."""
    for index in range(after_index + 1, len(candles)):
        if _retests_zone(candles[index], zone):
            return index
    return None


def evaluate_ifvg(
    candles: Sequence[Candle],
    fair_value_gaps: Sequence[TradeZone],
    *,
    expected_direction: str,
) -> IfvgResult:
    """
    Evaluate whether a valid IFVG entry location exists for
    `expected_direction`.

    Considers only FVG zones whose original `direction` is opposite
    `expected_direction` (the gap must stand as an obstacle before it
    can flip into support/resistance for the trade). Among those,
    selects the most recently created zone that has both flipped and
    been retested; a zone that has flipped but not yet been retested
    fails ("not confirmed as an entry location yet"), never fabricating
    a retest that hasn't happened.
    """
    if not candles:
        return IfvgResult(passed=False, reason="No candles available.")

    opposing_direction = _opposite_direction(expected_direction)
    candidate_zones = [
        zone for zone in fair_value_gaps if zone.direction == opposing_direction
    ]
    if not candidate_zones:
        return IfvgResult(
            passed=False,
            reason=(
                f"No {opposing_direction} Fair Value Gap exists to invert into a "
                f"{expected_direction} entry location."
            ),
        )

    # Most recently created candidate first: the freshest opposing FVG
    # is the most relevant obstacle/flip candidate for the current setup.
    candidate_zones = sorted(candidate_zones, key=lambda z: z.created_at, reverse=True)

    # Tracks the best partial progress seen across all candidate zones
    # (a flipped-but-unretested zone ranks above a never-flipped one),
    # so a failing result still surfaces the flip/zone diagnostics a
    # caller would want to display, not just a bare reason string.
    best_partial: Optional[IfvgResult] = None

    for zone in candidate_zones:
        flip_index = _find_flip_candle_index(candles, zone, expected_direction=expected_direction)
        if flip_index is None:
            if best_partial is None:
                best_partial = IfvgResult(
                    passed=False,
                    reason=(
                        f"A {opposing_direction} Fair Value Gap exists but has not yet been "
                        f"closed through to flip into a {expected_direction} IFVG."
                    ),
                    source_fvg=zone,
                )
            continue

        flip_candle = candles[flip_index]
        ifvg_zone = zone.model_copy(update={"direction": expected_direction})

        retest_index = _find_retest_candle_index(candles, ifvg_zone, after_index=flip_index)
        if retest_index is None:
            if best_partial is None or best_partial.flip_candle_index is None:
                best_partial = IfvgResult(
                    passed=False,
                    reason=(
                        f"A {opposing_direction} Fair Value Gap flipped into a "
                        f"{expected_direction} IFVG at candle {flip_candle.timestamp.isoformat()}, "
                        "but price has not retested it yet."
                    ),
                    source_fvg=zone,
                    ifvg_zone=ifvg_zone,
                    flip_candle_index=flip_index,
                )
            continue

        return IfvgResult(
            passed=True,
            reason=(
                f"A {opposing_direction} Fair Value Gap flipped into a {expected_direction} "
                "IFVG and was retested: a good entry location exists."
            ),
            source_fvg=zone,
            ifvg_zone=ifvg_zone,
            flip_candle_index=flip_index,
            retest_candle_index=retest_index,
        )

    return best_partial or IfvgResult(
        passed=False,
        reason=(
            f"No {opposing_direction} Fair Value Gap has flipped into a "
            f"{expected_direction} IFVG and been retested yet."
        ),
    )
