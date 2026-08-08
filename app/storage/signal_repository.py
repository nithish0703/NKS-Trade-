"""
Repository for persisting and retrieving signals.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.models.signal import Direction, Signal, SignalStatus
from app.storage.database import DatabaseManager, DatabaseOperationError
from app.storage.models import SignalRecord

# Temporary DEBUG-only logger for duplicate-detection diagnostics.
# Does not affect production behaviour: only emits when DEBUG level
# is enabled, and no logic below reads from or branches on it.
_debug_logger = logging.getLogger(__name__)


def _log_duplicate_debug(
    *,
    symbol: str,
    setup_key: str,
    existing_trade_id: str,
    existing_setup_key: str,
    created_at,
) -> None:
    """DEBUG-only diagnostic print for a duplicate detected by SignalRepository."""
    _debug_logger.debug(
        "\n-----------------------------------------\n"
        "Symbol: %s\n"
        "Generated setup_key: %s\n"
        "Duplicate detected at: SignalRepository\n"
        "existing trade_id: %s\n"
        "existing setup_key: %s\n"
        "created_at: %s\n"
        "-----------------------------------------",
        symbol,
        setup_key,
        existing_trade_id,
        existing_setup_key,
        created_at,
    )


DASHBOARD_STATUS_NEW = "NEW"
DASHBOARD_STATUS_ACTIVE = "ACTIVE"
# Set only by SignalOutcomeMonitor, and only for a signal that is
# dashboard-ACTIVE at the moment it closes (mirrored in the same write
# as the passive_* columns below -- see close_passive). A closed signal
# is excluded from both get_active_signals (dashboard_status != ACTIVE)
# and get_premium/strong_signals (dashboard_status != NEW), so it
# naturally drops out of every "still open" dashboard list once closed.
DASHBOARD_STATUS_CLOSED_WIN = "CLOSED_WIN"
DASHBOARD_STATUS_CLOSED_LOSS = "CLOSED_LOSS"

# Passive-outcome values stored in the `passive_outcome` column (see
# close_passive below). WIN/LOSS deliberately reuse the exact same
# strings as the dashboard_status CLOSED_WIN/CLOSED_LOSS constants
# above, since evaluate_outcome() in app.monitoring.signal_outcome_monitor
# is the single source of truth both the passive_* columns and (when
# mirrored) dashboard_status agree on. TIMEOUT has no dashboard_status
# equivalent -- it is never mirrored (see close_passive).
PASSIVE_OUTCOME_WIN = DASHBOARD_STATUS_CLOSED_WIN
PASSIVE_OUTCOME_LOSS = DASHBOARD_STATUS_CLOSED_LOSS
PASSIVE_OUTCOME_TIMEOUT = "TIMEOUT"
# A signal force-closed after MAX_CONSECUTIVE_MISSING_PRICE_CYCLES with
# no observed price at all -- never a real WIN/LOSS/TIMEOUT observation,
# so passive_exit_price is left NULL and this outcome is excluded from
# the Phase 1 performance report's expectancy math (see
# generate_performance_report), not counted as a fabricated 0R trade.
PASSIVE_OUTCOME_UNRESOLVED = "UNRESOLVED"

# Recorded in `outcome_source` alongside passive_outcome, so the Phase 1
# performance report can distinguish full-background tracking from a
# signal the user also actively watched on the dashboard.
OUTCOME_SOURCE_MANUAL_ACTIVATION = "MANUAL_ACTIVATION"
OUTCOME_SOURCE_PASSIVE_TRACKING = "PASSIVE_TRACKING"


def _as_utc(value: datetime) -> datetime:
    """
    SQLite (via aiosqlite) does not natively preserve timezone info, so
    timestamps round-trip as naive; re-attach UTC without shifting the
    wall-clock value, since all timestamps are written in UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _as_utc_or_none(value: Optional[datetime]) -> Optional[datetime]:
    """Same as _as_utc, but tolerates a NULL column (a nullable timestamp column really can be NULL)."""
    if value is None:
        return None
    return _as_utc(value)


class SignalWithStatus(BaseModel):
    """
    A stored Signal paired with its dashboard-only lifecycle status.

    `signal` is the exact, unmodified Signal as persisted -- this
    wrapper never mutates or recalculates any of its trading fields.
    `dashboard_status` ("NEW" or "ACTIVE") is a pure UI state transition
    set by the dashboard "Trade" action; it carries no meaning to
    strategy, scoring, risk, or notification code.
    """

    model_config = ConfigDict(frozen=True)

    signal: Signal
    dashboard_status: str


class ClosedTradeRecord(BaseModel):
    """
    A passively closed signal, with the analytics-only fields the Phase
    1 performance report slices by. `signal` is the exact, unmodified
    Signal as persisted.

    Every field below except `signal` and `passive_outcome` mirrors a
    NULLable DB column and MUST stay Optional here even though every
    *current* code path always writes them together in one commit --
    `passive_outcome` alone is guaranteed non-NULL (list_passively_closed
    filters on it), but `outcome_source` did not exist before this
    session's migration, so any row closed by an older build of
    close_passive has `outcome_source IS NULL`. The migration is only
    as safe as every reader downstream of it: a NULL here must parse
    into a real Optional value, never raise, and never be silently
    miscounted into a bucket it doesn't belong to (see
    app.analytics.performance_report, which slices a None
    outcome_source into its own "UNKNOWN" category and excludes any
    record missing passive_exit_price/passive_closed_at_utc from
    expectancy math entirely rather than crashing or fabricating a
    value).
    """

    model_config = ConfigDict(frozen=True)

    signal: Signal
    order_flow_confidence: Optional[str]
    entry_grade: Optional[str]
    stop_loss_source: Optional[str]
    passive_outcome: str
    passive_exit_price: Optional[float]
    passive_closed_at_utc: Optional[datetime]
    outcome_source: Optional[str]


class SignalOutcomeResult(BaseModel):
    """
    Result of close_passive(): the closed signal, its resulting
    dashboard_status, and which path produced the outcome. `signal` is
    the exact, unmodified Signal as persisted.
    """

    model_config = ConfigDict(frozen=True)

    signal: Signal
    dashboard_status: str
    outcome_source: str
    passive_outcome: str


class DuplicateSignalStorageError(Exception):
    """Raised when a Signal's trade_id or setup_key already exists in storage."""


class SignalNotFoundError(Exception):
    """Raised when an operation targets a trade_id that does not exist in storage."""


class SignalRepository:
    """Persists and retrieves Signal records from local SQLite storage."""

    def __init__(self, database_manager: DatabaseManager) -> None:
        self._database_manager = database_manager

    @property
    def database_manager(self) -> DatabaseManager:
        return self._database_manager

    async def save(
        self,
        signal: Signal,
        *,
        order_flow_confidence: Optional[str] = None,
        entry_grade: Optional[str] = None,
        stop_loss_source: Optional[str] = None,
    ) -> Signal:
        """
        Insert a CONFIRMED signal. Raises DuplicateSignalStorageError
        if a record with the same trade_id or setup_key already exists;
        never silently inserts a duplicate.

        `order_flow_confidence`, `entry_grade`, and `stop_loss_source`
        are analytics-only fields (see SignalRecord), never part of the
        Signal model itself and never shown on the Telegram/dashboard
        signal -- purely for the Phase 1 performance-report slices.
        """
        if signal.status != SignalStatus.CONFIRMED:
            raise ValueError("Only CONFIRMED signals may be persisted.")

        record = self._to_record(
            signal,
            order_flow_confidence=order_flow_confidence,
            entry_grade=entry_grade,
            stop_loss_source=stop_loss_source,
        )

        async with self._database_manager.session_scope() as session:
            existing = await session.execute(
                select(SignalRecord).where(
                    (SignalRecord.trade_id == signal.trade_id)
                    | (SignalRecord.setup_key == signal.setup_key)
                )
            )
            existing_record = existing.scalars().first()
            if existing_record is not None:
                _log_duplicate_debug(
                    symbol=signal.coin,
                    setup_key=signal.setup_key,
                    existing_trade_id=existing_record.trade_id,
                    existing_setup_key=existing_record.setup_key,
                    created_at=existing_record.created_at_utc,
                )
                raise DuplicateSignalStorageError(
                    f"A signal with trade_id='{signal.trade_id}' or "
                    f"setup_key='{signal.setup_key}' already exists."
                )

            session.add(record)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                try:
                    conflict = await session.execute(
                        select(SignalRecord).where(
                            (SignalRecord.trade_id == signal.trade_id)
                            | (SignalRecord.setup_key == signal.setup_key)
                        )
                    )
                    conflict_record = conflict.scalars().first()
                    if conflict_record is not None:
                        _log_duplicate_debug(
                            symbol=signal.coin,
                            setup_key=signal.setup_key,
                            existing_trade_id=conflict_record.trade_id,
                            existing_setup_key=conflict_record.setup_key,
                            created_at=conflict_record.created_at_utc,
                        )
                except SQLAlchemyError:
                    pass  # DEBUG-only lookup; never affects the raised error below.
                raise DuplicateSignalStorageError(
                    f"A signal with trade_id='{signal.trade_id}' or "
                    f"setup_key='{signal.setup_key}' already exists."
                ) from exc
            except SQLAlchemyError as exc:
                await session.rollback()
                raise DatabaseOperationError(f"Failed to save signal: {exc}") from exc

        return signal

    async def get_by_trade_id(self, trade_id: str) -> Optional[Signal]:
        async with self._database_manager.session_scope() as session:
            try:
                result = await session.execute(
                    select(SignalRecord).where(SignalRecord.trade_id == trade_id)
                )
            except SQLAlchemyError as exc:
                raise DatabaseOperationError(f"Failed to query signal by trade_id: {exc}") from exc
            record = result.scalars().first()
            return self._to_signal(record) if record is not None else None

    async def get_by_trade_id_with_status(self, trade_id: str) -> Optional[SignalWithStatus]:
        async with self._database_manager.session_scope() as session:
            try:
                result = await session.execute(
                    select(SignalRecord).where(SignalRecord.trade_id == trade_id)
                )
            except SQLAlchemyError as exc:
                raise DatabaseOperationError(f"Failed to query signal by trade_id: {exc}") from exc
            record = result.scalars().first()
            if record is None:
                return None
            return SignalWithStatus(signal=self._to_signal(record), dashboard_status=record.dashboard_status)

    async def get_by_setup_key(self, setup_key: str) -> Optional[Signal]:
        async with self._database_manager.session_scope() as session:
            try:
                result = await session.execute(
                    select(SignalRecord).where(SignalRecord.setup_key == setup_key)
                )
            except SQLAlchemyError as exc:
                raise DatabaseOperationError(f"Failed to query signal by setup_key: {exc}") from exc
            record = result.scalars().first()
            return self._to_signal(record) if record is not None else None

    async def list_recent(
        self,
        limit: int = 100,
        symbol: Optional[str] = None,
    ) -> list[Signal]:
        if limit <= 0:
            raise ValueError("limit must be positive.")

        query = select(SignalRecord).order_by(desc(SignalRecord.created_at_utc)).limit(limit)
        if symbol is not None:
            query = query.where(SignalRecord.coin == symbol)

        async with self._database_manager.session_scope() as session:
            try:
                result = await session.execute(query)
            except SQLAlchemyError as exc:
                raise DatabaseOperationError(f"Failed to list recent signals: {exc}") from exc
            records = result.scalars().all()
            return [self._to_signal(record) for record in records]

    async def list_recent_with_status(
        self,
        limit: int = 100,
        symbol: Optional[str] = None,
        dashboard_status: Optional[str] = None,
    ) -> list[SignalWithStatus]:
        """
        Same as list_recent, but also returns each signal's dashboard-only
        lifecycle status, and optionally filters by it. Never recalculates
        or modifies any Signal field.
        """
        if limit <= 0:
            raise ValueError("limit must be positive.")

        query = select(SignalRecord).order_by(desc(SignalRecord.created_at_utc)).limit(limit)
        if symbol is not None:
            query = query.where(SignalRecord.coin == symbol)
        if dashboard_status is not None:
            query = query.where(SignalRecord.dashboard_status == dashboard_status)

        async with self._database_manager.session_scope() as session:
            try:
                result = await session.execute(query)
            except SQLAlchemyError as exc:
                raise DatabaseOperationError(f"Failed to list recent signals: {exc}") from exc
            records = result.scalars().all()
            return [
                SignalWithStatus(signal=self._to_signal(record), dashboard_status=record.dashboard_status)
                for record in records
            ]

    async def mark_active(self, trade_id: str) -> SignalWithStatus:
        """
        Set a stored signal's dashboard_status to ACTIVE. Purely a
        dashboard state transition: never modifies any trading field on
        the signal, never touches strategy, scoring, risk, or
        notification logic.

        Raises:
            SignalNotFoundError: If no signal with `trade_id` exists.
        """
        async with self._database_manager.session_scope() as session:
            try:
                result = await session.execute(
                    select(SignalRecord).where(SignalRecord.trade_id == trade_id)
                )
            except SQLAlchemyError as exc:
                raise DatabaseOperationError(f"Failed to look up signal for activation: {exc}") from exc

            record = result.scalars().first()
            if record is None:
                raise SignalNotFoundError(f"No signal found with trade_id='{trade_id}'.")

            record.dashboard_status = DASHBOARD_STATUS_ACTIVE
            try:
                await session.commit()
            except SQLAlchemyError as exc:
                await session.rollback()
                raise DatabaseOperationError(f"Failed to mark signal active: {exc}") from exc

            return SignalWithStatus(signal=self._to_signal(record), dashboard_status=record.dashboard_status)

    async def list_not_passively_closed(self, limit: int = 10_000) -> list[Signal]:
        """
        Return every CONFIRMED signal not yet closed (`passive_outcome
        IS NULL`), regardless of `dashboard_status`. Used only by
        SignalOutcomeMonitor. Unlike the dashboard "Trade" workflow,
        this is never filtered to ACTIVE signals -- every signal the
        pipeline ever confirmed is tracked here, and this is the single
        query SignalOutcomeMonitor polls (there is no second,
        ACTIVE-only query running on its own schedule).

        Ordered oldest-detected-first: if the number of open signals
        ever exceeds `limit`, the oldest (closest to timing out) are the
        ones guaranteed to be checked this cycle, not an arbitrary
        DB-order subset -- the newest, still-far-from-timeout excess
        simply waits for a future cycle rather than risking the
        timeout logic silently starving on old signals.
        """
        if limit <= 0:
            raise ValueError("limit must be positive.")

        query = (
            select(SignalRecord)
            .where(SignalRecord.passive_outcome.is_(None))
            .order_by(SignalRecord.detection_time_utc.asc())
            .limit(limit)
        )
        async with self._database_manager.session_scope() as session:
            try:
                result = await session.execute(query)
            except SQLAlchemyError as exc:
                raise DatabaseOperationError(f"Failed to list not-passively-closed signals: {exc}") from exc
            records = result.scalars().all()
            return [self._to_signal(record) for record in records]

    async def close_passive(
        self,
        trade_id: str,
        *,
        outcome: str,
        exit_price: Optional[float],
        closed_at_utc: datetime,
    ) -> SignalOutcomeResult:
        """
        Record the outcome for `trade_id`: always sets
        passive_outcome/passive_exit_price/passive_closed_at_utc and
        outcome_source (the full-sample analytics ground truth used by
        the Phase 1 performance report). Called only by
        SignalOutcomeMonitor -- the single monitor and single ticker-
        polling schedule that resolves every CONFIRMED signal.

        When the signal is dashboard-ACTIVE (the "Trade" button
        workflow) at the moment this is called AND `outcome` is WIN or
        LOSS, the SAME write also mirrors onto dashboard_status/
        outcome/exit_price/closed_at_utc (dashboard_status becomes
        CLOSED_WIN/CLOSED_LOSS) and outcome_source is recorded as
        MANUAL_ACTIVATION; otherwise outcome_source is PASSIVE_TRACKING
        and the dashboard columns are left untouched. TIMEOUT and
        UNRESOLVED are never mirrored to the dashboard (dashboard_status
        has no equivalent concept for either, so an ACTIVE signal that
        times out or goes unresolved simply stays ACTIVE there while
        still being resolved for analytics purposes).

        `exit_price` must be a real observed price for WIN/LOSS/TIMEOUT
        (never fabricated). Only UNRESOLVED -- a signal for which no
        price was ever observed across MAX_CONSECUTIVE_MISSING_PRICE_CYCLES
        -- may pass `exit_price=None`, recorded as SQL NULL rather than
        a fabricated value.

        Never modifies any of the signal's original trading fields
        (entry_price, stop_loss, take_profit, etc.).

        Raises:
            SignalNotFoundError: If no signal with `trade_id` exists.
            ValueError: If `outcome` is not a recognized outcome value,
                or if `exit_price` is None for an outcome other than
                UNRESOLVED.
        """
        if outcome not in (
            PASSIVE_OUTCOME_WIN,
            PASSIVE_OUTCOME_LOSS,
            PASSIVE_OUTCOME_TIMEOUT,
            PASSIVE_OUTCOME_UNRESOLVED,
        ):
            raise ValueError(
                f"outcome must be one of '{PASSIVE_OUTCOME_WIN}', '{PASSIVE_OUTCOME_LOSS}', "
                f"'{PASSIVE_OUTCOME_TIMEOUT}', or '{PASSIVE_OUTCOME_UNRESOLVED}', got '{outcome}'."
            )
        if exit_price is None and outcome != PASSIVE_OUTCOME_UNRESOLVED:
            raise ValueError(f"exit_price is required for outcome '{outcome}'.")

        async with self._database_manager.session_scope() as session:
            try:
                result = await session.execute(
                    select(SignalRecord).where(SignalRecord.trade_id == trade_id)
                )
            except SQLAlchemyError as exc:
                raise DatabaseOperationError(f"Failed to look up signal for closing: {exc}") from exc

            record = result.scalars().first()
            if record is None:
                raise SignalNotFoundError(f"No signal found with trade_id='{trade_id}'.")

            was_active = record.dashboard_status == DASHBOARD_STATUS_ACTIVE
            source = OUTCOME_SOURCE_MANUAL_ACTIVATION if was_active else OUTCOME_SOURCE_PASSIVE_TRACKING

            record.passive_outcome = outcome
            record.passive_exit_price = exit_price
            record.passive_closed_at_utc = closed_at_utc
            record.outcome_source = source

            if was_active and outcome in (PASSIVE_OUTCOME_WIN, PASSIVE_OUTCOME_LOSS):
                record.dashboard_status = outcome  # aliases DASHBOARD_STATUS_CLOSED_WIN/LOSS
                record.outcome = outcome
                record.exit_price = exit_price
                record.closed_at_utc = closed_at_utc

            try:
                await session.commit()
            except SQLAlchemyError as exc:
                await session.rollback()
                raise DatabaseOperationError(f"Failed to record signal outcome: {exc}") from exc

            return SignalOutcomeResult(
                signal=self._to_signal(record),
                dashboard_status=record.dashboard_status,
                outcome_source=source,
                passive_outcome=outcome,
            )

    async def list_passively_closed(self, since: Optional[datetime] = None) -> list[ClosedTradeRecord]:
        """
        Return every signal that has been passively closed (WIN, LOSS,
        TIMEOUT, or UNRESOLVED), optionally restricted to those closed
        at or after `since`. Used by the Phase 1 performance report --
        this is the full-sample source (every CONFIRMED signal), unlike
        the dashboard-only ACTIVE/CLOSED_WIN/CLOSED_LOSS workflow.
        UNRESOLVED records have `passive_exit_price=None`; the caller
        must exclude them from R-multiple/expectancy math rather than
        treat a missing price as a fabricated value. A row closed before
        the `outcome_source` column existed has `outcome_source=None`
        (a pre-existing-data case, not a fabricated bucket); the caller
        must surface that distinctly rather than mis-slice it into
        MANUAL_ACTIVATION or PASSIVE_TRACKING.
        """
        query = select(SignalRecord).where(SignalRecord.passive_outcome.is_not(None))
        if since is not None:
            # NULL-safe: a NULL passive_closed_at_utc (never a real write
            # path today, but the column is nullable) must still surface
            # here rather than silently fail the `>= since` SQL
            # comparison (NULL compares as unknown, so a plain `>=`
            # would drop the row from every windowed report with no
            # trace at all) -- the caller decides whether to exclude it
            # from stats, but it must never simply vanish.
            query = query.where(
                or_(SignalRecord.passive_closed_at_utc.is_(None), SignalRecord.passive_closed_at_utc >= since)
            )

        async with self._database_manager.session_scope() as session:
            try:
                result = await session.execute(query)
            except SQLAlchemyError as exc:
                raise DatabaseOperationError(f"Failed to list passively closed signals: {exc}") from exc
            records = result.scalars().all()
            return [
                ClosedTradeRecord(
                    signal=self._to_signal(record),
                    order_flow_confidence=record.order_flow_confidence,
                    entry_grade=record.entry_grade,
                    stop_loss_source=record.stop_loss_source,
                    passive_outcome=record.passive_outcome,
                    passive_exit_price=record.passive_exit_price,
                    passive_closed_at_utc=_as_utc_or_none(record.passive_closed_at_utc),
                    outcome_source=record.outcome_source,
                )
                for record in records
            ]

    async def count_by_dashboard_status(self, dashboard_status: str) -> int:
        """Count signals currently in a given dashboard_status (e.g. ACTIVE, CLOSED_WIN)."""
        async with self._database_manager.session_scope() as session:
            try:
                result = await session.execute(
                    select(SignalRecord).where(SignalRecord.dashboard_status == dashboard_status)
                )
            except SQLAlchemyError as exc:
                raise DatabaseOperationError(f"Failed to count signals by dashboard_status: {exc}") from exc
            return len(result.scalars().all())

    async def count(self) -> int:
        async with self._database_manager.session_scope() as session:
            try:
                result = await session.execute(select(SignalRecord))
            except SQLAlchemyError as exc:
                raise DatabaseOperationError(f"Failed to count signals: {exc}") from exc
            return len(result.scalars().all())

    @staticmethod
    def _to_record(
        signal: Signal,
        *,
        order_flow_confidence: Optional[str] = None,
        entry_grade: Optional[str] = None,
        stop_loss_source: Optional[str] = None,
    ) -> SignalRecord:
        return SignalRecord(
            trade_id=signal.trade_id,
            setup_key=signal.setup_key,
            coin=signal.coin,
            direction=signal.direction.value,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            risk_reward_ratio=signal.risk_reward_ratio,
            status=signal.status.value,
            liquidity_type=signal.liquidity_type,
            entry_zone_type=signal.entry_zone_type,
            structure_confirmation=signal.structure_confirmation,
            detection_time_utc=signal.detection_time_utc,
            institutional_reason=signal.institutional_reason,
            liquidity_sweep_id=signal.liquidity_sweep_id,
            structure_break_id=signal.structure_break_id,
            entry_zone_id=signal.entry_zone_id,
            created_at_utc=signal.created_at_utc,
            order_flow_confidence=order_flow_confidence,
            entry_grade=entry_grade,
            stop_loss_source=stop_loss_source,
        )

    @staticmethod
    def _to_signal(record: SignalRecord) -> Signal:
        return Signal(
            trade_id=record.trade_id,
            coin=record.coin,
            direction=Direction(record.direction),
            entry_price=record.entry_price,
            stop_loss=record.stop_loss,
            take_profit=record.take_profit,
            risk_reward_ratio=record.risk_reward_ratio,
            status=SignalStatus(record.status),
            liquidity_type=record.liquidity_type,
            entry_zone_type=record.entry_zone_type,
            structure_confirmation=record.structure_confirmation,
            detection_time_utc=_as_utc(record.detection_time_utc),
            institutional_reason=record.institutional_reason,
            setup_key=record.setup_key,
            liquidity_sweep_id=record.liquidity_sweep_id,
            structure_break_id=record.structure_break_id,
            entry_zone_id=record.entry_zone_id,
            created_at_utc=_as_utc(record.created_at_utc),
        )
