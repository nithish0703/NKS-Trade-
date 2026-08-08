"""
Repository for persisting and retrieving analytics data.
"""

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, select
from sqlalchemy.exc import SQLAlchemyError

from app.scanner.pipeline_results import PipelineStatus, StrategyPipelineResult
from app.storage.database import DatabaseManager, DatabaseOperationError
from app.storage.models import RejectedAnalyticsRecord, ScanStageAnalyticsRecord


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class RejectionRecord(BaseModel):
    """A single stored rejection-analytics record, safe for external display."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    failed_layer: str
    rejection_reason: str
    detection_time_utc: datetime
    created_at_utc: datetime


class StageAnalyticsRecord(BaseModel):
    """A single stored per-stage scan-analytics record."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    stage_order: int
    layer_name: str
    passed: bool
    duration_ms: float
    scan_time_utc: datetime


class AnalyticsRepository:
    """
    Optionally persists REJECTED strategy pipeline results and every
    scan's per-stage outcomes as internal analytics records. Never
    stores full candle payloads or secrets, and never turns a REJECTED
    result into a valid signal.
    """

    def __init__(self, database_manager: DatabaseManager, *, enabled: bool) -> None:
        self._database_manager = database_manager
        self._enabled = enabled

    async def save_rejection(self, pipeline_result: StrategyPipelineResult) -> None:
        """
        Store `pipeline_result` as a rejection-analytics record when
        rejection analytics are enabled and the result status is
        REJECTED. A no-op otherwise; a database failure here must not
        be interpreted as, or converted into, a valid trading signal.
        """
        if not self._enabled:
            return
        if pipeline_result.status != PipelineStatus.REJECTED:
            return

        record = RejectedAnalyticsRecord(
            symbol=pipeline_result.symbol,
            failed_layer=pipeline_result.failed_layer or "UNKNOWN",
            rejection_reason=pipeline_result.rejection_reason or "No rejection reason recorded.",
            detection_time_utc=pipeline_result.detection_time_utc,
            created_at_utc=datetime.now(timezone.utc),
        )

        async with self._database_manager.session_scope() as session:
            session.add(record)
            try:
                await session.commit()
            except SQLAlchemyError as exc:
                await session.rollback()
                raise DatabaseOperationError(f"Failed to save rejection analytics: {exc}") from exc

    async def list_recent(
        self, limit: int = 50, symbol: Optional[str] = None
    ) -> list[RejectionRecord]:
        """
        Return the most recently stored rejection-analytics records,
        newest first. Never includes candle data or secrets, since the
        underlying record never stores them.
        """
        if limit <= 0:
            raise ValueError("limit must be positive.")

        query = select(RejectedAnalyticsRecord).order_by(
            desc(RejectedAnalyticsRecord.created_at_utc)
        ).limit(limit)
        if symbol is not None:
            query = query.where(RejectedAnalyticsRecord.symbol == symbol)

        async with self._database_manager.session_scope() as session:
            try:
                result = await session.execute(query)
            except SQLAlchemyError as exc:
                raise DatabaseOperationError(f"Failed to list rejection analytics: {exc}") from exc
            records = result.scalars().all()
            return [
                RejectionRecord(
                    symbol=record.symbol,
                    failed_layer=record.failed_layer,
                    rejection_reason=record.rejection_reason,
                    detection_time_utc=_as_utc(record.detection_time_utc),
                    created_at_utc=_as_utc(record.created_at_utc),
                )
                for record in records
            ]

    async def list_rejections_since(self, since: datetime) -> list[RejectionRecord]:
        """
        Return every rejection-analytics record with `detection_time_utc`
        at or after `since`, in no particular order. Used by the Phase 1
        funnel report's top-rejection-reasons breakdown.
        """
        query = select(RejectedAnalyticsRecord).where(
            RejectedAnalyticsRecord.detection_time_utc >= since
        )
        async with self._database_manager.session_scope() as session:
            try:
                result = await session.execute(query)
            except SQLAlchemyError as exc:
                raise DatabaseOperationError(f"Failed to list rejection analytics since {since}: {exc}") from exc
            records = result.scalars().all()
            return [
                RejectionRecord(
                    symbol=record.symbol,
                    failed_layer=record.failed_layer,
                    rejection_reason=record.rejection_reason,
                    detection_time_utc=_as_utc(record.detection_time_utc),
                    created_at_utc=_as_utc(record.created_at_utc),
                )
                for record in records
            ]

    async def save_stage_results(self, pipeline_result: StrategyPipelineResult) -> None:
        """
        Store one stage-analytics record per *executed* stage in
        `pipeline_result.stages`, for the Phase 1 rejection-funnel
        report. A no-op when stage analytics are disabled or the result
        has no executed stages (e.g. an ERROR result with no pipeline
        run at all). Called for every scan that actually ran the
        pipeline (VALID, REJECTED, or DUPLICATE), never for ERROR or
        SKIPPED results.
        """
        if not self._enabled:
            return

        executed_stages = [stage for stage in pipeline_result.stages if stage.executed]
        if not executed_stages:
            return

        now = datetime.now(timezone.utc)
        async with self._database_manager.session_scope() as session:
            for stage in executed_stages:
                session.add(
                    ScanStageAnalyticsRecord(
                        symbol=pipeline_result.symbol,
                        stage_order=stage.stage_order,
                        layer_name=stage.layer_name,
                        passed=stage.passed,
                        duration_ms=stage.duration_ms,
                        scan_time_utc=pipeline_result.detection_time_utc,
                        created_at_utc=now,
                    )
                )
            try:
                await session.commit()
            except SQLAlchemyError as exc:
                await session.rollback()
                raise DatabaseOperationError(f"Failed to save stage analytics: {exc}") from exc

    async def list_stage_results_since(self, since: datetime) -> list[StageAnalyticsRecord]:
        """
        Return every stage-analytics record with `scan_time_utc` at or
        after `since`, in no particular order. Used by the Phase 1
        funnel report.
        """
        query = select(ScanStageAnalyticsRecord).where(ScanStageAnalyticsRecord.scan_time_utc >= since)
        async with self._database_manager.session_scope() as session:
            try:
                result = await session.execute(query)
            except SQLAlchemyError as exc:
                raise DatabaseOperationError(f"Failed to list stage analytics since {since}: {exc}") from exc
            records = result.scalars().all()
            return [
                StageAnalyticsRecord(
                    symbol=record.symbol,
                    stage_order=record.stage_order,
                    layer_name=record.layer_name,
                    passed=record.passed,
                    duration_ms=record.duration_ms,
                    scan_time_utc=_as_utc(record.scan_time_utc),
                )
                for record in records
            ]
