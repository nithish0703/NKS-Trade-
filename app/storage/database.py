"""
Database connection and session management.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.storage.models import Base


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
        Create all tables if they do not already exist. Idempotent and
        never drops or recreates existing tables.
        """
        try:
            async with self._engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
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
