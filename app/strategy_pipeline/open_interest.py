"""
Open Interest sub-check, part of Stage 5 (OI + CVD).

  BUY:  price up AND OI up
  SELL: price down AND OI up
  Else: reject.

This removes moves caused only by short covering (price up, OI falling
-- shorts closing, not new longs opening) or long liquidation (price
down, OI falling -- longs being forced out, not new shorts opening).
"""

from enum import Enum
from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict

from app.data.open_interest_point import OpenInterestPoint
from app.models.candle import Candle


class ConfirmationStatus(str, Enum):
    """
    Outcome of a Stage 5 sub-check, distinguishing a genuine directional
    disagreement from a data-availability problem.

    `passed` alone cannot tell these apart, which collapses "the market
    genuinely disagreed" and "we couldn't get enough data to know" into
    the same rejection -- indistinguishable in logs, notifications, and
    stored signals. `status` is the authoritative field; `passed` is
    kept only so existing callers that read it (e.g. combining sub-check
    outcomes) keep working unchanged.
    """

    CONFIRMED = "CONFIRMED"
    DISAGREED = "DISAGREED"
    UNAVAILABLE = "UNAVAILABLE"


class OpenInterestConfirmationResult(BaseModel):
    """Result of the Open Interest sub-check."""

    model_config = ConfigDict(frozen=True)

    passed: bool
    status: ConfirmationStatus
    reason: str
    price_change: Optional[float] = None
    open_interest_change: Optional[float] = None


def evaluate_open_interest_confirmation(
    candles: Sequence[Candle],
    open_interest_history: Sequence[OpenInterestPoint],
    *,
    expected_direction: str,
) -> OpenInterestConfirmationResult:
    """
    Confirm that the expected direction is supported by genuinely new
    positioning, not short covering or long liquidation:

      - BUY requires both price and Open Interest to have risen.
      - SELL requires price to have fallen while Open Interest rose.

    Compares the latest available price/OI against the earliest point
    in the supplied history (i.e. over the whole window the caller
    provides). Never fabricates a direction from missing/insufficient
    data: fewer than 2 candles or fewer than 2 OI points is reported as
    status=UNAVAILABLE (data problem), never as status=DISAGREED
    (market-outcome problem) -- a caller must be able to tell an infra
    gap apart from a genuine short-covering/long-liquidation move.
    """
    if len(candles) < 2:
        return OpenInterestConfirmationResult(
            passed=False,
            status=ConfirmationStatus.UNAVAILABLE,
            reason="Insufficient candle history to determine price change.",
        )
    if len(open_interest_history) < 2:
        return OpenInterestConfirmationResult(
            passed=False,
            status=ConfirmationStatus.UNAVAILABLE,
            reason="Insufficient Open Interest history to determine OI change.",
        )

    price_change = candles[-1].close - candles[0].close
    oi_change = open_interest_history[-1].open_interest - open_interest_history[0].open_interest

    if expected_direction == "BUY":
        if price_change > 0 and oi_change > 0:
            return OpenInterestConfirmationResult(
                passed=True,
                status=ConfirmationStatus.CONFIRMED,
                reason="Price and Open Interest both rose: genuinely new long positioning.",
                price_change=price_change,
                open_interest_change=oi_change,
            )
        return OpenInterestConfirmationResult(
            passed=False,
            status=ConfirmationStatus.DISAGREED,
            reason=(
                "BUY requires price up and Open Interest up; got "
                f"price_change={price_change:.6g}, oi_change={oi_change:.6g} "
                "(move may be short covering, not new buying)."
            ),
            price_change=price_change,
            open_interest_change=oi_change,
        )

    # SELL
    if price_change < 0 and oi_change > 0:
        return OpenInterestConfirmationResult(
            passed=True,
            status=ConfirmationStatus.CONFIRMED,
            reason="Price fell while Open Interest rose: genuinely new short positioning.",
            price_change=price_change,
            open_interest_change=oi_change,
        )
    return OpenInterestConfirmationResult(
        passed=False,
        status=ConfirmationStatus.DISAGREED,
        reason=(
            "SELL requires price down and Open Interest up; got "
            f"price_change={price_change:.6g}, oi_change={oi_change:.6g} "
            "(move may be long liquidation, not new selling)."
        ),
        price_change=price_change,
        open_interest_change=oi_change,
    )
