"""Database engine and session factory for async SQLAlchemy."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.thirdhand.config import settings

# Create async engine
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,  # Set to True for SQL query logging
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

# Create async session factory
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Create sync engine/session factory for Celery workers and other sync contexts
sync_engine = create_engine(
    settings.DATABASE_URL_SYNC,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

sync_session_factory = sessionmaker(
    bind=sync_engine,
    class_=Session,
    expire_on_commit=False,
)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_session() -> AsyncSession:
    """Get a single async session (for non-request contexts)."""
    return async_session_factory()


def get_sync_session() -> Session:
    """Get a sync database session for Celery and other sync workers."""
    return sync_session_factory()


async def init_db() -> None:
    """Initialize database connection."""
    async with engine.begin():
        # This is mainly used for testing; migrations handle production
        pass


async def close_db() -> None:
    """Close database connection."""
    await engine.dispose()
    sync_engine.dispose()
