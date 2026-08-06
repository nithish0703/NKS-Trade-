"""
Tests for app.storage.database.DatabaseManager, using a temporary SQLite file.
"""

import os

import pytest
from sqlalchemy import inspect, text

from app.storage.database import DatabaseManager, DatabaseOperationError

pytestmark = pytest.mark.asyncio


@pytest.fixture
def database_url(tmp_path):
    db_path = tmp_path / "test_signals.db"
    return f"sqlite+aiosqlite:///{db_path}"


class TestDatabaseManager:
    async def test_initialization_creates_tables(self, database_url):
        manager = DatabaseManager(database_url)
        await manager.initialize()
        try:
            async with manager._engine.connect() as connection:
                table_names = await connection.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_table_names()
                )
            assert "signals" in table_names
            assert "rejected_analytics" in table_names
        finally:
            await manager.dispose()

    async def test_idempotent_initialization(self, database_url):
        manager = DatabaseManager(database_url)
        await manager.initialize()
        await manager.initialize()  # must not raise or drop existing tables
        try:
            async with manager._engine.connect() as connection:
                table_names = await connection.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_table_names()
                )
            assert "signals" in table_names
        finally:
            await manager.dispose()

    async def test_session_creation(self, database_url):
        manager = DatabaseManager(database_url)
        await manager.initialize()
        try:
            session = manager.create_session()
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1
            await session.close()
        finally:
            await manager.dispose()

    async def test_disposal(self, database_url):
        manager = DatabaseManager(database_url)
        await manager.initialize()
        await manager.dispose()
        # Disposal must not raise even though no further use follows.

    async def test_no_destructive_drop_on_reinitialize(self, database_url):
        manager = DatabaseManager(database_url)
        await manager.initialize()
        try:
            async with manager.session_scope() as session:
                await session.execute(
                    text(
                        "INSERT INTO signals (trade_id, setup_key, coin, direction, entry_price, "
                        "stop_loss, take_profit, risk_reward_ratio, status, "
                        "liquidity_type, entry_zone_type, structure_confirmation, "
                        "detection_time_utc, institutional_reason, "
                        "liquidity_sweep_id, structure_break_id, entry_zone_id, created_at_utc) "
                        "VALUES ('t1', 's1', 'BTC-USDT', 'BUY', 100.0, 95.0, 110.0, 3.0, 'CONFIRMED', "
                        "'EQUAL_HIGH', 'ORDER_BLOCK', 'BOS', "
                        "'2026-01-01T10:00:00+00:00', 'reason', 'sw1', 'br1', 'zo1', "
                        "'2026-01-01T10:00:00+00:00')"
                    )
                )
                await session.commit()

            await manager.initialize()  # re-initialize must not drop existing rows

            async with manager.session_scope() as session:
                result = await session.execute(text("SELECT COUNT(*) FROM signals"))
                assert result.scalar() == 1
        finally:
            await manager.dispose()

    async def test_database_operation_error_on_dispose_failure(self, database_url):
        from unittest.mock import AsyncMock

        manager = DatabaseManager(database_url)
        await manager.initialize()

        manager._engine = AsyncMock(dispose=AsyncMock(side_effect=RuntimeError("engine already closed")))
        with pytest.raises(DatabaseOperationError):
            await manager.dispose()

    async def test_separate_managers_are_independent(self, tmp_path):
        url_one = f"sqlite+aiosqlite:///{tmp_path / 'db_one.db'}"
        url_two = f"sqlite+aiosqlite:///{tmp_path / 'db_two.db'}"
        manager_one = DatabaseManager(url_one)
        manager_two = DatabaseManager(url_two)
        await manager_one.initialize()
        await manager_two.initialize()
        try:
            assert manager_one._engine is not manager_two._engine
            async with manager_one.session_scope() as session:
                await session.execute(
                    text(
                        "INSERT INTO rejected_analytics (symbol, failed_layer, rejection_reason, "
                        "detection_time_utc, created_at_utc) VALUES ('BTC-USDT', 'MARKET_REGIME', "
                        "'not trending', '2026-01-01T10:00:00+00:00', '2026-01-01T10:00:00+00:00')"
                    )
                )
                await session.commit()
            async with manager_two.session_scope() as session:
                result = await session.execute(text("SELECT COUNT(*) FROM rejected_analytics"))
                assert result.scalar() == 0
        finally:
            await manager_one.dispose()
            await manager_two.dispose()

    async def test_async_context_manager_support(self, database_url):
        async with DatabaseManager(database_url) as manager:
            async with manager.session_scope() as session:
                result = await session.execute(text("SELECT 1"))
                assert result.scalar() == 1

    async def test_no_api_call_during_construction(self, database_url):
        # Construction alone must not perform any I/O.
        manager = DatabaseManager(database_url)
        assert manager is not None


class TestAdditiveColumnMigration:
    async def test_adds_missing_column_to_pre_existing_table(self, database_url):
        # Simulate a database created by an older version of the code:
        # a "signals" table that exists but predates the dashboard_status
        # column (this is exactly the on-disk state that caused a live
        # 500 error on GET/POST endpoints reading dashboard_status).
        manager = DatabaseManager(database_url)
        try:
            async with manager._engine.begin() as connection:
                await connection.execute(
                    text(
                        "CREATE TABLE signals ("
                        "id INTEGER PRIMARY KEY, trade_id VARCHAR(128) UNIQUE NOT NULL, "
                        "setup_key VARCHAR(128) UNIQUE NOT NULL, coin VARCHAR(32) NOT NULL, "
                        "direction VARCHAR(8) NOT NULL, entry_price FLOAT NOT NULL, "
                        "stop_loss FLOAT NOT NULL, take_profit FLOAT NOT NULL, "
                        "risk_reward_ratio FLOAT NOT NULL, confidence_score FLOAT NOT NULL, "
                        "signal_type VARCHAR(16) NOT NULL, market_regime VARCHAR(32) NOT NULL, "
                        "higher_timeframe_bias VARCHAR(16) NOT NULL, liquidity_type VARCHAR(32) NOT NULL, "
                        "entry_zone_type VARCHAR(32) NOT NULL, structure_confirmation VARCHAR(16) NOT NULL, "
                        "volume_confirmation BOOLEAN NOT NULL, atr_status VARCHAR(16) NOT NULL, "
                        "trading_session VARCHAR(32) NOT NULL, btc_market_alignment BOOLEAN NOT NULL, "
                        "detection_time_utc DATETIME NOT NULL, institutional_reason TEXT NOT NULL, "
                        "liquidity_sweep_id VARCHAR(128) NOT NULL, structure_break_id VARCHAR(128) NOT NULL, "
                        "entry_zone_id VARCHAR(128) NOT NULL, retest_id VARCHAR(128) NOT NULL, "
                        "created_at_utc DATETIME NOT NULL)"
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO signals (trade_id, setup_key, coin, direction, entry_price, "
                        "stop_loss, take_profit, risk_reward_ratio, confidence_score, signal_type, "
                        "market_regime, higher_timeframe_bias, liquidity_type, entry_zone_type, "
                        "structure_confirmation, volume_confirmation, atr_status, trading_session, "
                        "btc_market_alignment, detection_time_utc, institutional_reason, "
                        "liquidity_sweep_id, structure_break_id, entry_zone_id, retest_id, created_at_utc) "
                        "VALUES ('t1', 's1', 'BTC-USDT', 'BUY', 100.0, 95.0, 110.0, 3.0, 90.0, 'PREMIUM', "
                        "'TRENDING', 'BULLISH', 'EQUAL_HIGH', 'ORDER_BLOCK', 'BOS', 1, 'EXPANDING', "
                        "'LONDON', 1, '2026-01-01T10:00:00+00:00', 'reason', 'sw1', 'br1', 'zo1', 're1', "
                        "'2026-01-01T10:00:00+00:00')"
                    )
                )

            # Must not raise: the missing dashboard_status column (and the
            # unrelated rejected_analytics table) are added additively.
            await manager.initialize()

            async with manager.session_scope() as session:
                columns = await session.connection()
                column_names = await columns.run_sync(
                    lambda sync_conn: {col["name"] for col in inspect(sync_conn).get_columns("signals")}
                )
                assert "dashboard_status" in column_names

                # Pre-existing rows are backfilled with the column default.
                result = await session.execute(text("SELECT dashboard_status FROM signals WHERE trade_id = 't1'"))
                assert result.scalar() == "NEW"

                table_names = await columns.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
                assert "rejected_analytics" in table_names
        finally:
            await manager.dispose()

    async def test_migration_is_idempotent(self, database_url):
        manager = DatabaseManager(database_url)
        await manager.initialize()
        await manager.initialize()  # must not raise on a second call
        try:
            async with manager.session_scope() as session:
                result = await session.execute(text("SELECT COUNT(*) FROM signals"))
                assert result.scalar() == 0
        finally:
            await manager.dispose()
