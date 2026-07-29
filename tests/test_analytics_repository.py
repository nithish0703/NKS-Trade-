"""
Tests for app.storage.analytics_repository.AnalyticsRepository.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.scanner.pipeline_results import PipelineStatus, StrategyPipelineResult
from app.storage.analytics_repository import AnalyticsRepository
from app.storage.database import DatabaseManager
from app.storage.models import RejectedAnalyticsRecord

pytestmark = pytest.mark.asyncio

UTC_NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _rejected_pipeline_result(symbol="BTC-USDT") -> StrategyPipelineResult:
    return StrategyPipelineResult(
        symbol=symbol,
        expected_direction=None,
        detection_time_utc=UTC_NOW,
        status=PipelineStatus.REJECTED,
        passed=False,
        failed_layer="MARKET_REGIME",
        rejection_reason="ADX below trending threshold.",
        stages=[],
    )


def _error_pipeline_result(symbol="BTC-USDT") -> StrategyPipelineResult:
    return StrategyPipelineResult(
        symbol=symbol,
        expected_direction=None,
        detection_time_utc=UTC_NOW,
        status=PipelineStatus.ERROR,
        passed=False,
        stages=[],
    )


@pytest.fixture
def database_manager_factory(tmp_path):
    def _make():
        database_url = f"sqlite+aiosqlite:///{tmp_path / 'analytics.db'}"
        return DatabaseManager(database_url)

    return _make


class TestAnalyticsRepository:
    async def test_rejected_result_stored_when_enabled(self, tmp_path):
        manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}")
        await manager.initialize()
        try:
            repository = AnalyticsRepository(manager, enabled=True)
            await repository.save_rejection(_rejected_pipeline_result())
            async with manager.session_scope() as session:
                result = await session.execute(select(RejectedAnalyticsRecord))
                records = result.scalars().all()
            assert len(records) == 1
            assert records[0].failed_layer == "MARKET_REGIME"
        finally:
            await manager.dispose()

    async def test_not_stored_when_disabled(self, tmp_path):
        manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'b.db'}")
        await manager.initialize()
        try:
            repository = AnalyticsRepository(manager, enabled=False)
            await repository.save_rejection(_rejected_pipeline_result())
            async with manager.session_scope() as session:
                result = await session.execute(select(RejectedAnalyticsRecord))
                records = result.scalars().all()
            assert len(records) == 0
        finally:
            await manager.dispose()

    async def test_valid_signal_not_stored_as_rejection(self, tmp_path):
        # StrategyPipelineResult itself guarantees a VALID result carries a
        # valid RiskPlan and publishable confidence, so status=VALID never
        # reaches save_rejection's storage branch; confirm it's a no-op for
        # any non-REJECTED status using model_construct to bypass VALID's
        # own construction-time invariants.
        manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'c.db'}")
        await manager.initialize()
        try:
            repository = AnalyticsRepository(manager, enabled=True)
            valid_result = StrategyPipelineResult.model_construct(
                symbol="BTC-USDT",
                expected_direction="BUY",
                detection_time_utc=UTC_NOW,
                status=PipelineStatus.VALID,
                passed=True,
                failed_layer=None,
                rejection_reason=None,
                market_context=None,
                stages=[],
            )
            await repository.save_rejection(valid_result)
            async with manager.session_scope() as session:
                result = await session.execute(select(RejectedAnalyticsRecord))
                records = result.scalars().all()
            assert len(records) == 0
        finally:
            await manager.dispose()

    async def test_error_result_not_stored_as_strategy_rejection(self, tmp_path):
        manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'd.db'}")
        await manager.initialize()
        try:
            repository = AnalyticsRepository(manager, enabled=True)
            await repository.save_rejection(_error_pipeline_result())
            async with manager.session_scope() as session:
                result = await session.execute(select(RejectedAnalyticsRecord))
                records = result.scalars().all()
            assert len(records) == 0
        finally:
            await manager.dispose()

    async def test_full_candle_data_not_stored(self, tmp_path):
        manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'e.db'}")
        await manager.initialize()
        try:
            repository = AnalyticsRepository(manager, enabled=True)
            await repository.save_rejection(_rejected_pipeline_result())
            column_names = {column.name for column in RejectedAnalyticsRecord.__table__.columns}
            assert "candles" not in column_names
            assert "candle_data" not in column_names
            assert "market_context" not in column_names
        finally:
            await manager.dispose()

    async def test_failed_layer_and_reason_preserved(self, tmp_path):
        manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'f.db'}")
        await manager.initialize()
        try:
            repository = AnalyticsRepository(manager, enabled=True)
            pipeline_result = _rejected_pipeline_result()
            await repository.save_rejection(pipeline_result)
            async with manager.session_scope() as session:
                result = await session.execute(select(RejectedAnalyticsRecord))
                record = result.scalars().first()
            assert record.failed_layer == pipeline_result.failed_layer
            assert record.rejection_reason == pipeline_result.rejection_reason
            assert record.symbol == pipeline_result.symbol
        finally:
            await manager.dispose()

    async def test_list_recent_returns_newest_first(self, tmp_path):
        manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'g.db'}")
        await manager.initialize()
        try:
            repository = AnalyticsRepository(manager, enabled=True)
            await repository.save_rejection(_rejected_pipeline_result(symbol="BTC-USDT"))
            await repository.save_rejection(_rejected_pipeline_result(symbol="ETH-USDT"))
            records = await repository.list_recent(limit=10)
            assert len(records) == 2
            assert records[0].symbol == "ETH-USDT"
            assert records[1].symbol == "BTC-USDT"
        finally:
            await manager.dispose()

    async def test_list_recent_filters_by_symbol(self, tmp_path):
        manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'h.db'}")
        await manager.initialize()
        try:
            repository = AnalyticsRepository(manager, enabled=True)
            await repository.save_rejection(_rejected_pipeline_result(symbol="BTC-USDT"))
            await repository.save_rejection(_rejected_pipeline_result(symbol="ETH-USDT"))
            records = await repository.list_recent(limit=10, symbol="ETH-USDT")
            assert len(records) == 1
            assert records[0].symbol == "ETH-USDT"
        finally:
            await manager.dispose()

    async def test_list_recent_rejects_non_positive_limit(self, tmp_path):
        manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'i.db'}")
        await manager.initialize()
        try:
            repository = AnalyticsRepository(manager, enabled=True)
            with pytest.raises(ValueError):
                await repository.list_recent(limit=0)
        finally:
            await manager.dispose()

    async def test_list_recent_never_includes_candle_data(self, tmp_path):
        manager = DatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'j.db'}")
        await manager.initialize()
        try:
            repository = AnalyticsRepository(manager, enabled=True)
            await repository.save_rejection(_rejected_pipeline_result())
            records = await repository.list_recent(limit=10)
            record_fields = set(type(records[0]).model_fields.keys())
            assert record_fields.isdisjoint({"candles", "candle_data", "market_context"})
        finally:
            await manager.dispose()
