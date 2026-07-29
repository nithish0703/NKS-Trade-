"""
Helpers for generating unique identifiers (signal IDs, zone IDs, etc.).
"""

import hashlib
from datetime import datetime


def make_setup_key(
    symbol: str,
    direction: str,
    sweep_id: str,
    zone_id: str,
    break_id: str,
    retest_id: str,
) -> str:
    """
    Generate a stable, deterministic institutional-setup identity key
    from structural fields only (no confidence score, no detection time,
    no entry-price fluctuation, no scan-cycle ID).
    """
    raw = "|".join((symbol, direction, sweep_id, zone_id, break_id, retest_id))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_trade_id(
    symbol: str,
    direction: str,
    setup_key: str,
    detection_time_utc: datetime,
) -> str:
    """
    Generate a deterministic, human-readable trade ID with an "SMC"
    prefix from the symbol, direction, setup identity, and detection
    timestamp. Deterministic (not a random UUID) so identical setups at
    the same detection time always resolve to the same trade_id.
    """
    raw = "|".join((symbol, direction, setup_key, detection_time_utc.isoformat()))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"SMC-{symbol}-{direction}-{digest}"
