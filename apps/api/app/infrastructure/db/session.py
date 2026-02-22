"""Async SQLAlchemy engine and session factory for the Creator GraphRAG API."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# Single engine instance shared for the process lifetime.
# Pool settings from STANDARDS.md §4.6.
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=10,
    max_overflow=5,
    pool_timeout=5,
    pool_recycle=1800,
    pool_pre_ping=True,
    echo=False,  # SQL logging controlled via logging.getLogger("sqlalchemy.engine") level
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async SQLAlchemy session as a FastAPI dependency.

    Commits automatically on clean exit; rolls back and re-raises on
    any unhandled exception so the caller never sees a partial write.

    Yields:
        AsyncSession: An open database session bound to the request lifecycle.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
