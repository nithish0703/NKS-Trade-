"""
Tests for app.storage.monitor_lease.MonitorLeaseGuard.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.storage.database import DatabaseManager
from app.storage.monitor_lease import MonitorLeaseGuard

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
