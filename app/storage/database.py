"""
Database connection and session management.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.storage.models import Base


def _add_missing_columns(sync_connection) -> None:
    """
    Additive-only lightweight migration: for each declared ORM table that
    already exists in the database, add any column present on the model
    but missing from the on-disk table (e.g. a new nullable/defaulted
    column added after the table was first created). Never drops,
    renames, or alters an existing column, and never touches a table
    that doesn't exist yet (create_all handles brand-new tables).

    This project has no Alembic/migration framework; this keeps
    `create_all`'s "safe to call repeatedly" guarantee while still
    letting additive schema changes reach databases created by an
    older version of the code.
    """
    inspector = inspect(sync_connection)
    existing_tables = set(inspector.get_table_names())

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue
            ddl_type = column.type.compile(dialect=sync_connection.dialect)
            default_clause = ""
            if column.server_default is not None:
                default_arg = column.server_default.arg
                raw_sql_text = getattr(default_arg, "text", None)
                if raw_sql_text is not None:
                    # Already a raw SQL literal/expression (e.g. text("0")).
                    default_clause = f" DEFAULT {raw_sql_text}"
                else:
                    # A plain Python value (e.g. server_default="NEW"); quote
                    # it as a SQL string literal.
                    default_clause = f" DEFAULT '{default_arg}'"
            sync_connection.execute(
                text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}{default_clause}')
            )


class StorageError(Exception):
    """Base exception for all local-storage failures."""


class DatabaseInitializationError(StorageError):
    """Raised when the database engine or schema cannot be initialized."""


class DatabaseOperationError(StorageError):
    """Raised when a database operation (session, query, commit) fails."""


class DatabaseManager:
    """
    Owns a single async SQLAlchemy engine and session factory for local
    SQLite persistence. Construction performs no I/O; `initialize()`
    must be called explicitly before use.
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._engine: AsyncEngine = create_async_engine(database_url, future=True)
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine, expire_on_commit=False
        )
        self._initialized = False

    async def initialize(self) -> None:
        """
        Create all tables if they do not already exist, then add any
        columns declared on the ORM models but missing from existing
        tables (additive-only lightweight migration; see
        _add_missing_columns). Idempotent and never drops or recreates
        existing tables or columns.
        """
        try:
            async with self._engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                await connection.run_sync(_add_missing_columns)
            self._initialized = True
        except Exception as exc:
            raise DatabaseInitializationError(
                f"Failed to initialize the database at '{self._database_url}': {exc}"
            ) from exc

    async def dispose(self) -> None:
        """Dispose of the underlying engine and its connection pool."""
        try:
            await self._engine.dispose()
        except Exception as exc:
            raise DatabaseOperationError(f"Failed to dispose the database engine: {exc}") from exc

    def create_session(self) -> AsyncSession:
        """Create a new AsyncSession bound to this manager's engine."""
        return self._session_factory()

    @asynccontextmanager
    async def session_scope(self) -> AsyncIterator[AsyncSession]:
        """Async context manager yielding a session, rolling back on error."""
        session = self.create_session()
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def __aenter__(self) -> "DatabaseManager":
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.dispose()
