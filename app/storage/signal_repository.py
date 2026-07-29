"""
Repository for persisting and retrieving signals.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.models.signal import Direction, MarketRegime, Signal, SignalType
from app.storage.database import DatabaseManager, DatabaseOperationError
from app.storage.models import SignalRecord


def _as_utc(value: datetime) -> datetime:
    """
    SQLite (via aiosqlite) does not natively preserve timezone info, so
    timestamps round-trip as naive; re-attach UTC without shifting the
    wall-clock value, since all timestamps are written in UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class DuplicateSignalStorageError(Exception):
    """Raised when a Signal's trade_id or setup_key already exists in storage."""


class SignalRepository:
    """Persists and retrieves Signal records from local SQLite storage."""

    def __init__(self, database_manager: DatabaseManager) -> None:
        self._database_manager = database_manager

    @property
    def database_manager(self) -> DatabaseManager:
        return self._database_manager

    async def save(self, signal: Signal) -> Signal:
        """
        Insert a PREMIUM/STRONG signal. Raises DuplicateSignalStorageError
        if a record with the same trade_id or setup_key already exists;
        never silently inserts a duplicate.
        """
        if signal.signal_type not in (SignalType.PREMIUM, SignalType.STRONG):
            raise ValueError("Only PREMIUM or STRONG signals may be persisted.")

        record = self._to_record(signal)

        async with self._database_manager.session_scope() as session:
            existing = await session.execute(
                select(SignalRecord).where(
                    (SignalRecord.trade_id == signal.trade_id)
                    | (SignalRecord.setup_key == signal.setup_key)
                )
            )
            if existing.scalars().first() is not None:
                raise DuplicateSignalStorageError(
                    f"A signal with trade_id='{signal.trade_id}' or "
                    f"setup_key='{signal.setup_key}' already exists."
                )

            session.add(record)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
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
        signal_type: Optional[str] = None,
    ) -> list[Signal]:
        if limit <= 0:
            raise ValueError("limit must be positive.")

        query = select(SignalRecord).order_by(desc(SignalRecord.created_at_utc)).limit(limit)
        if symbol is not None:
            query = query.where(SignalRecord.coin == symbol)
        if signal_type is not None:
            query = query.where(SignalRecord.signal_type == signal_type)

        async with self._database_manager.session_scope() as session:
            try:
                result = await session.execute(query)
            except SQLAlchemyError as exc:
                raise DatabaseOperationError(f"Failed to list recent signals: {exc}") from exc
            records = result.scalars().all()
            return [self._to_signal(record) for record in records]

    async def count(self) -> int:
        async with self._database_manager.session_scope() as session:
            try:
                result = await session.execute(select(SignalRecord))
            except SQLAlchemyError as exc:
                raise DatabaseOperationError(f"Failed to count signals: {exc}") from exc
            return len(result.scalars().all())

    @staticmethod
    def _to_record(signal: Signal) -> SignalRecord:
        return SignalRecord(
            trade_id=signal.trade_id,
            setup_key=signal.setup_key,
            coin=signal.coin,
            direction=signal.direction.value,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            risk_reward_ratio=signal.risk_reward_ratio,
            confidence_score=signal.confidence_score,
            signal_type=signal.signal_type.value,
            market_regime=signal.market_regime.value,
            higher_timeframe_bias=signal.higher_timeframe_bias,
            liquidity_type=signal.liquidity_type,
            entry_zone_type=signal.entry_zone_type,
            structure_confirmation=signal.structure_confirmation,
            volume_confirmation=signal.volume_confirmation,
            atr_status=signal.atr_status,
            trading_session=signal.trading_session,
            btc_market_alignment=signal.btc_market_alignment,
            detection_time_utc=signal.detection_time_utc,
            institutional_reason=signal.institutional_reason,
            liquidity_sweep_id=signal.liquidity_sweep_id,
            structure_break_id=signal.structure_break_id,
            entry_zone_id=signal.entry_zone_id,
            retest_id=signal.retest_id,
            created_at_utc=signal.created_at_utc,
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
            confidence_score=record.confidence_score,
            signal_type=SignalType(record.signal_type),
            market_regime=MarketRegime(record.market_regime),
            higher_timeframe_bias=record.higher_timeframe_bias,
            liquidity_type=record.liquidity_type,
            entry_zone_type=record.entry_zone_type,
            structure_confirmation=record.structure_confirmation,
            volume_confirmation=record.volume_confirmation,
            atr_status=record.atr_status,
            trading_session=record.trading_session,
            btc_market_alignment=record.btc_market_alignment,
            detection_time_utc=_as_utc(record.detection_time_utc),
            institutional_reason=record.institutional_reason,
            setup_key=record.setup_key,
            liquidity_sweep_id=record.liquidity_sweep_id,
            structure_break_id=record.structure_break_id,
            entry_zone_id=record.entry_zone_id,
            retest_id=record.retest_id,
            created_at_utc=_as_utc(record.created_at_utc),
        )
