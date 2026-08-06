"""
Minimal access-tier gate for the dashboard API.

This is NOT an authentication system: there are no user accounts,
passwords, or sessions anywhere in this codebase. It is a lightweight
field-visibility gate that determines whether the caller may see a
signal's binary CONFIRMED/REJECTED status, based on an explicit
`tier` query parameter or `X-Access-Tier` header (query parameter
takes precedence). Any value other than "premium" (case-insensitive)
is treated as "free".
"""

from enum import Enum
from typing import Optional

from fastapi import Header, Query


class AccessTier(str, Enum):
    FREE = "free"
    PREMIUM = "premium"


def get_access_tier(
    tier: Optional[str] = Query(default=None),
    x_access_tier: Optional[str] = Header(default=None),
) -> AccessTier:
    raw_value = tier if tier is not None else x_access_tier
    if raw_value is not None and raw_value.strip().lower() == AccessTier.PREMIUM.value:
        return AccessTier.PREMIUM
    return AccessTier.FREE
