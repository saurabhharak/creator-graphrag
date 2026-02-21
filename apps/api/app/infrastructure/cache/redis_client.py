"""Async Redis client for JTI revocation, pub/sub, and general caching."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import redis.asyncio as aioredis

from app.core.config import settings

_redis_pool: aioredis.ConnectionPool | None = None


def get_redis_pool() -> aioredis.ConnectionPool:
    """Return (or lazily create) the shared Redis connection pool.

    Returns:
        A singleton ConnectionPool connected to ``settings.REDIS_URL``.
    """
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=20,
            decode_responses=True,
        )
    return _redis_pool


async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:
    """FastAPI dependency: yield a Redis client from the shared connection pool.

    Yields:
        aioredis.Redis: A client bound to the shared pool. Closed after the
        request completes.
    """
    client = aioredis.Redis(connection_pool=get_redis_pool())
    try:
        yield client
    finally:
        await client.aclose()
