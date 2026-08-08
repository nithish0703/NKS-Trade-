"""
Tests for app.storage.monitor_lease.MonitorLeaseGuard.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.storage.database import DatabaseManager
from app.storage.monitor_lease import MonitorLeaseGuard, release_lease

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def database_manager(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'lease.db'}"
    manager = DatabaseManager(database_url)
    await manager.initialize()
    yield manager
    await manager.dispose()


class TestMonitorLeaseGuard:
    def test_non_positive_duration_rejected(self, tmp_path):
        manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'x.db'}")
        with pytest.raises(ValueError):
            MonitorLeaseGuard(manager, lease_name="x", lease_duration_seconds=0)

    async def test_first_acquire_on_empty_table_succeeds(self, database_manager):
        guard = MonitorLeaseGuard(database_manager, lease_name="test-lease", lease_duration_seconds=60)
        assert await guard.try_acquire(UTC_NOW) is True

    async def test_same_holder_can_renew(self, database_manager):
        guard = MonitorLeaseGuard(database_manager, lease_name="test-lease", lease_duration_seconds=60)
        assert await guard.try_acquire(UTC_NOW) is True
        assert await guard.try_acquire(UTC_NOW + timedelta(seconds=10)) is True

    async def test_other_holder_blocked_while_unexpired(self, database_manager):
        first = MonitorLeaseGuard(database_manager, lease_name="test-lease", lease_duration_seconds=60)
        second = MonitorLeaseGuard(database_manager, lease_name="test-lease", lease_duration_seconds=60)

        assert await first.try_acquire(UTC_NOW) is True
        assert await second.try_acquire(UTC_NOW + timedelta(seconds=10)) is False

    async def test_other_holder_takes_over_after_expiry(self, database_manager):
        first = MonitorLeaseGuard(database_manager, lease_name="test-lease", lease_duration_seconds=60)
        second = MonitorLeaseGuard(database_manager, lease_name="test-lease", lease_duration_seconds=60)

        assert await first.try_acquire(UTC_NOW) is True
        assert await second.try_acquire(UTC_NOW + timedelta(seconds=61)) is True
        # The original holder is now locked out until it can take the
        # lease back the same way.
        assert await first.try_acquire(UTC_NOW + timedelta(seconds=65)) is False

    async def test_different_lease_names_are_independent(self, database_manager):
        first = MonitorLeaseGuard(database_manager, lease_name="lease-a", lease_duration_seconds=60)
        second = MonitorLeaseGuard(database_manager, lease_name="lease-b", lease_duration_seconds=60)

        assert await first.try_acquire(UTC_NOW) is True
        assert await second.try_acquire(UTC_NOW) is True


class TestStableHolderId:
    """
    Covers the Ctrl+C/crash restart fix: a caller that supplies a
    stable holder_id (rather than the default fresh-random-uuid one)
    must be able to reclaim its own lease immediately after a restart,
    even while its own previous lease is still unexpired -- unlike two
    genuinely different holders, which still exclude each other
    normally.
    """

    async def test_same_stable_holder_id_reclaims_immediately_even_if_unexpired(self, database_manager):
        first_process = MonitorLeaseGuard(
            database_manager, lease_name="scanner_cycle", lease_duration_seconds=900, holder_id="scan-cli"
        )
        assert await first_process.try_acquire(UTC_NOW) is True

        # Simulate a Ctrl+C/crash + restart: a brand-new MonitorLeaseGuard
        # instance, but the same stable holder_id, only 5 seconds later --
        # nowhere near the 900s lease duration expiring.
        restarted_process = MonitorLeaseGuard(
            database_manager, lease_name="scanner_cycle", lease_duration_seconds=900, holder_id="scan-cli"
        )
        assert await restarted_process.try_acquire(UTC_NOW + timedelta(seconds=5)) is True

    async def test_different_stable_holder_ids_still_exclude_each_other(self, database_manager):
        scan_cli = MonitorLeaseGuard(
            database_manager, lease_name="scanner_cycle", lease_duration_seconds=900, holder_id="scan-cli"
        )
        dashboard_api = MonitorLeaseGuard(
            database_manager, lease_name="scanner_cycle", lease_duration_seconds=900, holder_id="dashboard-api"
        )

        assert await scan_cli.try_acquire(UTC_NOW) is True
        assert await dashboard_api.try_acquire(UTC_NOW + timedelta(seconds=5)) is False

    async def test_default_holder_id_is_still_random_per_instance(self, database_manager):
        # Backward compatibility: omitting holder_id preserves the
        # original behaviour (a fresh random id, no stable reclaim).
        first = MonitorLeaseGuard(database_manager, lease_name="scanner_cycle", lease_duration_seconds=900)
        second = MonitorLeaseGuard(database_manager, lease_name="scanner_cycle", lease_duration_seconds=900)

        assert await first.try_acquire(UTC_NOW) is True
        assert await second.try_acquire(UTC_NOW + timedelta(seconds=5)) is False


class TestReleaseLease:
    """
    Covers the `--force-release-lease` CLI escape hatch's underlying
    mechanism: deleting a lease row on demand, regardless of current
    holder or expiry, so the next try_acquire() by anyone succeeds
    immediately.
    """

    async def test_release_deletes_an_existing_lease(self, database_manager):
        guard = MonitorLeaseGuard(database_manager, lease_name="scanner_cycle", lease_duration_seconds=900)
        assert await guard.try_acquire(UTC_NOW) is True

        released = await release_lease(database_manager, lease_name="scanner_cycle")
        assert released is True

        other_holder = MonitorLeaseGuard(database_manager, lease_name="scanner_cycle", lease_duration_seconds=900)
        assert await other_holder.try_acquire(UTC_NOW + timedelta(seconds=1)) is True

    async def test_release_of_nonexistent_lease_returns_false(self, database_manager):
        released = await release_lease(database_manager, lease_name="never-existed")
        assert released is False

    async def test_instance_release_method_delegates(self, database_manager):
        guard = MonitorLeaseGuard(database_manager, lease_name="scanner_cycle", lease_duration_seconds=900)
        assert await guard.try_acquire(UTC_NOW) is True
        assert await guard.release() is True
        assert await release_lease(database_manager, lease_name="scanner_cycle") is False
