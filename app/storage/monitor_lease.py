"""
DB-backed mutual-exclusion lease so at most one process's
SignalOutcomeMonitor does outcome-tracking work at a time, even when
`python main.py scan` and the dashboard API (`uvicorn app.api.main:app`)
both point at the same SQLite database.

Coordination happens through a row in the `monitor_leases` table
(MonitorLeaseRecord), not a filesystem lock, so it works identically
regardless of which two processes are racing and needs no extra
dependency beyond the SQLAlchemy engine already in use.

There is a narrow, accepted race: two processes can both read an
expired (or absent) lease in the same instant and both then write,
believing they acquired it for that cycle. Given a >=60s poll interval
and a single fast DB round trip, this is exceedingly rare, and its
worst case is one duplicate polling pass computing the same outcome
from the same price data -- never an incorrect WIN/LOSS/TIMEOUT value.
A true single-writer guarantee would need SQLite-level locking
primitives this project does not otherwise use; this lease is a
best-effort reduction of "two independently drifting schedules" to
"effectively one", not a distributed-systems-grade guarantee.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.storage.database import DatabaseManager, DatabaseOperationError
from app.storage.models import MonitorLeaseRecord


def _as_utc(value: datetime) -> datetime:
    """SQLite round-trips timestamps as naive; re-attach UTC without shifting the wall-clock value."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def release_lease(database_manager: DatabaseManager, *, lease_name: str) -> bool:
    """
    Delete a named lease row if one exists, regardless of its current
    holder or expiry, so the very next `try_acquire()` call by any
    holder succeeds immediately instead of waiting out the remaining
    duration.

    For an operator manually recovering from a crash that left a
    holder-mismatched lease behind (see the `--force-release-lease`
    CLI flag in main.py): a *stable* holder_id (see `MonitorLeaseGuard`)
    already lets the same process identity reclaim its own lease
    instantly on restart, so this function exists for the remaining
    case -- clearing a lease for a genuinely different holder to take
    over right away, on demand, rather than as the routine restart path.

    Returns True if a row was deleted, False if none existed.
    """
    try:
        async with database_manager.session_scope() as session:
            result = await session.execute(
                select(MonitorLeaseRecord).where(MonitorLeaseRecord.lease_name == lease_name)
            )
            record = result.scalars().first()
            if record is None:
                return False
            await session.delete(record)
            await session.commit()
            return True
    except SQLAlchemyError as exc:
        raise DatabaseOperationError(f"Failed to release lease '{lease_name}': {exc}") from exc


class MonitorLeaseGuard:
    """
    Acquires/renews a single named, time-boxed lease. Only the current
    holder (or nobody, if the previous lease expired) can hold it at a
    time.

    `holder_id`, when supplied, must be stable across restarts of the
    same logical process/mode (e.g. "scan-cli" for `python main.py
    scan`, "dashboard-api" for the uvicorn dashboard's background
    scanner) -- `try_acquire()` lets a lease's *existing* holder_id
    renew/take over its own row unconditionally, even before the
    previous lease has expired, so a process killed by Ctrl+C or a
    crash and then restarted reclaims its own lease immediately rather
    than sitting locked out for the full lease duration. Two
    genuinely different processes/modes must use different holder_ids,
    or they would never exclude each other at all. Defaults to a fresh
    random id per instance (the original behaviour) when omitted --
    appropriate only for a caller with no meaningful stable identity
    across restarts.
    """

    def __init__(
        self,
        database_manager: DatabaseManager,
        *,
        lease_name: str,
        lease_duration_seconds: float,
        holder_id: Optional[str] = None,
    ) -> None:
        if lease_duration_seconds <= 0:
            raise ValueError("lease_duration_seconds must be positive.")
        self._database_manager = database_manager
        self._lease_name = lease_name
        self._lease_duration_seconds = lease_duration_seconds
        self._holder_id = holder_id or str(uuid4())

    async def try_acquire(self, now: datetime) -> bool:
        """
        Attempt to acquire or renew the lease for `now`. Returns True if
        this instance now holds it (either newly acquired, taken over
        from an expired holder, or renewed as the existing holder),
        False if another process currently holds an unexpired lease.
        """
        expires_at = now + timedelta(seconds=self._lease_duration_seconds)
        try:
            async with self._database_manager.session_scope() as session:
                result = await session.execute(
                    select(MonitorLeaseRecord).where(MonitorLeaseRecord.lease_name == self._lease_name)
                )
                record = result.scalars().first()

                if record is None:
                    session.add(
                        MonitorLeaseRecord(
                            lease_name=self._lease_name,
                            holder_id=self._holder_id,
                            expires_at_utc=expires_at,
                        )
                    )
                    try:
                        await session.commit()
                    except IntegrityError:
                        # Another process inserted the same lease_name in
                        # the same instant; it won the race, not us.
                        await session.rollback()
                        return False
                    return True

                if record.holder_id != self._holder_id and _as_utc(record.expires_at_utc) > now:
                    return False

                record.holder_id = self._holder_id
                record.expires_at_utc = expires_at
                await session.commit()
                return True
        except SQLAlchemyError as exc:
            raise DatabaseOperationError(f"Failed to acquire lease '{self._lease_name}': {exc}") from exc

    async def release(self) -> bool:
        """Delete this lease's row now, regardless of current holder. See `release_lease`."""
        return await release_lease(self._database_manager, lease_name=self._lease_name)
