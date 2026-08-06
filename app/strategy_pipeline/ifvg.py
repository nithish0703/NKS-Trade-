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

Good entry location requires, in order (Grade A -- the primary path):
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

The Grade A path is only looked for within a validity window of
IFVG_VALIDITY_WINDOW_CANDLES candles after the confirming BOS's
break_candle_index (when a `structure_break` is supplied): if price
never retraces into a flipped IFVG within that window, an unbounded
wait would mean a structurally valid trade idea (Stage 3 already
passed) is missed entirely just because entry-location confirmation
never arrived. Rather than rejecting outright when the window expires,
this falls through to a second, coarser path:

  Grade B (BOS-zone retest fallback): a retest of the BOS's own broken
  structure level, treated as a wider support/resistance zone spanning
  from the broken swing's price (the structural boundary that was
  broken) to the break candle's close (where price conviction
  settled) -- for a bullish break the swing price is the floor and the
  close is the ceiling; for a bearish break the ordering is reversed.
  This is a coarser, less precise zone than a genuine IFVG (worse
  expected R:R, since stops sit further from entry), so it is only
  looked for once the tighter Grade A path has exhausted its own
  window, and gets its own independent window
  (BOS_ZONE_RETEST_VALIDITY_WINDOW_CANDLES) since a coarser zone can
  reasonably be granted a different retest allowance.

`entry_grade` ("A" or "B") is populated only when the stage passes, so
downstream consumers (risk sizing, scoring, stats) can tell a tight
IFVG entry apart from a wider BOS-zone fallback entry instead of both
collapsing into an identical passed=True with no way to distinguish
entry quality. Never fabricates a flip, a retest, or a BOS-zone
retest from a zone/break that was never detected or never actually
retested.
"""

from typing import Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict

from app.config.thresholds import (
    BOS_ZONE_RETEST_VALIDITY_WINDOW_CANDLES,
    IFVG_VALIDITY_WINDOW_CANDLES,
)
from app.market_structure.shift_results import BreakDirection, StructureBreakResult
from app.models.candle import Candle
from app.models.trade_zone import TradeZone, ZoneStatus, ZoneType

EntryGrade = Literal["A", "B"]


class IfvgResult(BaseModel):
    """
    Result of the IFVG (entry location) stage.

    `source_fvg` is the original Fair Value Gap that was later broken
    and flipped; `ifvg_zone` is the resulting inverted zone (same price
    range, opposite direction) once a flip has occurred -- both are
    only present for a passing Grade A result. `bos_zone` is the wider
    zone derived from the confirmed BOS, only present for a passing
    Grade B result. `entry_grade` is populated only when the stage
    passes: "A" for a tighter IFVG flip+retest, "B" for the wider
    BOS-zone retest fallback -- never set on a failing result, so a
    caller can never mistake a rejected setup for a graded entry.
    """

    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: str
    entry_grade: Optional[EntryGrade] = None
    source_fvg: Optional[TradeZone] = None
    ifvg_zone: Optional[TradeZone] = None
    flip_candle_index: Optional[int] = None
    retest_candle_index: Optional[int] = None
    bos_zone: Optional[TradeZone] = None
    bos_zone_retest_candle_index: Optional[int] = None


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
    candles: Sequence[Candle], zone: TradeZone, *, expected_direction: str, window_end_index: Optional[int]
) -> Optional[int]:
    """
    First candle strictly after the zone's source candle (and, when a
    validity window applies, at or before `window_end_index`) that
    closes fully through it, invalidating/flipping it. Returns None if
    no such candle exists yet within the allowed range.
    """
    for index, candle in enumerate(candles):
        if window_end_index is not None and index > window_end_index:
            break
        if candle.timestamp <= zone.source_candle_timestamp:
            continue
        if _closes_fully_through(candle, zone, expected_direction=expected_direction):
            return index
    return None


def _find_retest_candle_index(
    candles: Sequence[Candle], zone: TradeZone, *, after_index: int, window_end_index: Optional[int]
) -> Optional[int]:
    """
    First candle strictly after `after_index` (and, when a validity
    window applies, at or before `window_end_index`) whose range
    retests the zone.
    """
    end = len(candles) if window_end_index is None else min(len(candles), window_end_index + 1)
    for index in range(after_index + 1, end):
        if _retests_zone(candles[index], zone):
            return index
    return None


def _build_bos_zone(structure_break: StructureBreakResult) -> TradeZone:
    """
    Derive a wider support/resistance zone from a confirmed BOS: the
    broken swing's price is the structural boundary that was breached,
    and the break candle's close is where price conviction settled
    beyond it. For a bullish break the swing price is below the close
    (floor = swing, ceiling = close); for a bearish break the ordering
    is reversed (floor = close, ceiling = swing). This is intentionally
    coarser than a genuine IFVG zone -- it exists only as a fallback
    entry location once the tighter IFVG path has expired its own
    window, never as a substitute the primary path would otherwise
    prefer.
    """
    swing_price = structure_break.broken_swing.price
    close_price = structure_break.close_price
    lower_price = min(swing_price, close_price)
    upper_price = max(swing_price, close_price)
    zone_direction = "BUY" if structure_break.direction == BreakDirection.BULLISH else "SELL"

    return TradeZone(
        zone_id=f"bos-zone-{structure_break.break_id}",
        symbol=structure_break.symbol,
        timeframe=structure_break.timeframe,
        zone_type=ZoneType.ORDER_BLOCK,
        direction=zone_direction,
        lower_price=lower_price,
        upper_price=upper_price,
        created_at=structure_break.break_candle_timestamp,
        source_candle_timestamp=structure_break.break_candle_timestamp,
        source_candle_index=structure_break.break_candle_index,
        status=ZoneStatus.FRESH,
        originating_break_id=structure_break.break_id,
    )


def _evaluate_ifvg_flip_and_retest(
    candles: Sequence[Candle],
    fair_value_gaps: Sequence[TradeZone],
    *,
    expected_direction: str,
    window_end_index: Optional[int],
) -> IfvgResult:
    """
    Grade A: evaluate the tighter IFVG flip+retest path within
    `window_end_index` (inclusive), or with no window at all when
    `window_end_index` is None. Never grades a result -- the caller
    attaches entry_grade="A" only once this returns passed=True.
    """
    opposing_direction = _opposite_direction(expected_direction)
    candidate_zones = [zone for zone in fair_value_gaps if zone.direction == opposing_direction]
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
        flip_index = _find_flip_candle_index(
            candles, zone, expected_direction=expected_direction, window_end_index=window_end_index
        )
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

        retest_index = _find_retest_candle_index(
            candles, ifvg_zone, after_index=flip_index, window_end_index=window_end_index
        )
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


def evaluate_ifvg(
    candles: Sequence[Candle],
    fair_value_gaps: Sequence[TradeZone],
    *,
    expected_direction: str,
    structure_break: Optional[StructureBreakResult] = None,
) -> IfvgResult:
    """
    Evaluate whether a valid entry location exists for
    `expected_direction`, via the Grade A IFVG flip+retest path, or (if
    that path's window expires without confirming and `structure_break`
    is supplied) the Grade B BOS-zone retest fallback.

    When `structure_break` is None (no confirmed BOS available -- e.g.
    direct unit testing of this function in isolation), the Grade A
    path runs with no validity window at all, matching this function's
    original unbounded behaviour, and the Grade B fallback never runs
    (there is no BOS to derive a zone from).

    When `structure_break` is supplied, Grade A is only looked for
    within IFVG_VALIDITY_WINDOW_CANDLES candles after
    `structure_break.break_candle_index`. If that window expires
    without a passing result, Grade B is attempted: a retest of the
    BOS-derived zone (see `_build_bos_zone`) within a further
    BOS_ZONE_RETEST_VALIDITY_WINDOW_CANDLES candles after the same
    break_candle_index. If neither path confirms within its window,
    the stage fails with a reason naming both outcomes, never a
    generic "not confirmed."

    Never fabricates a flip, a retest, or a BOS-zone retest that hasn't
    happened, and never rejects a structurally valid trade idea (Stage
    3 already passed) outright just because the tighter Grade A window
    expired -- this fallback only concerns entry *location*, never
    trade *validity*.
    """
    if not candles:
        return IfvgResult(passed=False, reason="No candles available.")

    if structure_break is None:
        return _evaluate_ifvg_flip_and_retest(
            candles, fair_value_gaps, expected_direction=expected_direction, window_end_index=None
        )

    ifvg_window_end = structure_break.break_candle_index + IFVG_VALIDITY_WINDOW_CANDLES
    grade_a_result = _evaluate_ifvg_flip_and_retest(
        candles, fair_value_gaps, expected_direction=expected_direction, window_end_index=ifvg_window_end
    )
    if grade_a_result.passed:
        return grade_a_result.model_copy(update={"entry_grade": "A"})

    bos_zone = _build_bos_zone(structure_break)
    bos_zone_window_end = structure_break.break_candle_index + BOS_ZONE_RETEST_VALIDITY_WINDOW_CANDLES
    bos_zone_retest_index = _find_retest_candle_index(
        candles,
        bos_zone,
        after_index=structure_break.break_candle_index,
        window_end_index=bos_zone_window_end,
    )
    if bos_zone_retest_index is not None:
        return IfvgResult(
            passed=True,
            reason=(
                f"IFVG flip+retest did not confirm within {IFVG_VALIDITY_WINDOW_CANDLES} candles "
                f"of the BOS, but its broken-structure zone was retested within "
                f"{BOS_ZONE_RETEST_VALIDITY_WINDOW_CANDLES} candles: a wider fallback entry "
                "location exists."
            ),
            entry_grade="B",
            bos_zone=bos_zone,
            bos_zone_retest_candle_index=bos_zone_retest_index,
        )

    return IfvgResult(
        passed=False,
        reason=(
            f"IFVG flip+retest window expired ({IFVG_VALIDITY_WINDOW_CANDLES} candles after the "
            f"BOS) with no confirmed retest, and the BOS-zone retest fallback also did not "
            f"confirm within its own {BOS_ZONE_RETEST_VALIDITY_WINDOW_CANDLES}-candle window: "
            "no entry location exists yet."
        ),
        source_fvg=grade_a_result.source_fvg,
        flip_candle_index=grade_a_result.flip_candle_index,
    )
