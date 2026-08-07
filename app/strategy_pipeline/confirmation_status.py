"""
Shared confirmation-outcome enum for Stage 5's sub-checks (Volume
Profile, CVD).
"""

from enum import Enum


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
