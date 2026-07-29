"""
Duplicate institutional-setup suppression for the multi-pair scanner.
"""

import asyncio
import hashlib
from datetime import datetime, timezone

from app.scanner.pipeline_results import PipelineStatus, StrategyPipelineResult


class DuplicateGuardError(Exception):
    """Raised when a pipeline result cannot be used to build a setup key."""


def _require_utc(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise DuplicateGuardError(f"{label} must be timezone-aware UTC.")


class DuplicateSignalGuard:
    """
    Tracks recently seen institutional setup identities to prevent the
    same underlying setup from being treated as a new signal repeatedly.

    The setup identity is built only from structural fields (symbol,
    direction, and the IDs of the selected sweep/zone/break/retest) so
    that confidence-score changes, entry-price fluctuations, or repeated
    detection over successive scan cycles never alter the key.
    """

    def __init__(self, *, retention_seconds: int, maximum_entries: int) -> None:
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive.")
        if maximum_entries <= 0:
            raise ValueError("maximum_entries must be positive.")

        self._retention_seconds = retention_seconds
        self._maximum_entries = maximum_entries
        self._entries: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def build_setup_key(pipeline_result: StrategyPipelineResult) -> str:
        """Build a stable, deterministic key identifying an institutional setup."""
        if pipeline_result.status != PipelineStatus.VALID:
            raise DuplicateGuardError("A setup key requires a VALID pipeline result.")

        sweep = pipeline_result.liquidity_sweep
        zone = pipeline_result.selected_entry_zone
        structure_break = pipeline_result.selected_structure_break
        retest = pipeline_result.retest_result

        if sweep is None or zone is None or structure_break is None or retest is None:
            raise DuplicateGuardError(
                "A setup key requires the selected sweep, zone, structure break, and retest."
            )

        sweep_id = getattr(sweep, "sweep_id", None)
        zone_id = getattr(zone, "zone_id", None)
        break_id = getattr(structure_break, "break_id", None)
        retest_id = getattr(retest, "retest_id", None)

        if not sweep_id or not zone_id or not break_id or not retest_id:
            raise DuplicateGuardError(
                "A setup key requires sweep_id, zone_id, break_id, and retest_id to be present."
            )

        key_parts = (
            pipeline_result.symbol,
            pipeline_result.expected_direction,
            sweep_id,
            zone_id,
            break_id,
            retest_id,
        )
        digest_input = "|".join(str(part) for part in key_parts)
        return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()

    def _is_expired(self, registered_at: datetime, current_time_utc: datetime) -> bool:
        elapsed = (current_time_utc - registered_at).total_seconds()
        return elapsed >= self._retention_seconds

    def _evict_expired_locked(self, current_time_utc: datetime) -> int:
        expired_keys = [
            key
            for key, registered_at in self._entries.items()
            if self._is_expired(registered_at, current_time_utc)
        ]
        for key in expired_keys:
            del self._entries[key]
        return len(expired_keys)

    def _enforce_maximum_entries_locked(self) -> None:
        if len(self._entries) <= self._maximum_entries:
            return
        # Deterministic eviction: remove the oldest-registered entries first.
        overflow = len(self._entries) - self._maximum_entries
        oldest_first = sorted(self._entries.items(), key=lambda item: (item[1], item[0]))
        for key, _ in oldest_first[:overflow]:
            del self._entries[key]

    async def is_duplicate(self, setup_key: str, current_time_utc: datetime) -> bool:
        """Return True if setup_key is registered and still within the retention window."""
        _require_utc(current_time_utc, "current_time_utc")
        async with self._lock:
            self._evict_expired_locked(current_time_utc)
            registered_at = self._entries.get(setup_key)
            if registered_at is None:
                return False
            return not self._is_expired(registered_at, current_time_utc)

    async def register(self, setup_key: str, current_time_utc: datetime) -> None:
        """Register (or refresh) a setup key as seen at current_time_utc."""
        _require_utc(current_time_utc, "current_time_utc")
        async with self._lock:
            self._entries[setup_key] = current_time_utc
            self._enforce_maximum_entries_locked()

    async def check_and_register(
        self, pipeline_result: StrategyPipelineResult, current_time_utc: datetime
    ) -> tuple[bool, str]:
        """
        Build the setup key for pipeline_result, determine whether it is a
        duplicate of a recently seen setup, and register it if it is new
        or expired. Returns (duplicate, setup_key).
        """
        _require_utc(current_time_utc, "current_time_utc")
        setup_key = self.build_setup_key(pipeline_result)

        async with self._lock:
            self._evict_expired_locked(current_time_utc)
            registered_at = self._entries.get(setup_key)
            is_duplicate = registered_at is not None and not self._is_expired(
                registered_at, current_time_utc
            )
            self._entries[setup_key] = current_time_utc
            self._enforce_maximum_entries_locked()

        return is_duplicate, setup_key

    async def cleanup(self, current_time_utc: datetime) -> int:
        """Remove all expired entries and return the number removed."""
        _require_utc(current_time_utc, "current_time_utc")
        async with self._lock:
            return self._evict_expired_locked(current_time_utc)
