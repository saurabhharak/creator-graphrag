"""API-side Neo4j async client for graph browse and search endpoints.

Uses the async neo4j driver. Driver is a module-level singleton created
lazily on first call and closed during app lifespan shutdown.

Usage:
    records = await run_read("MATCH (c:Concept) RETURN c LIMIT $n", {"n": 10})
    await run_write("MERGE (c:Concept {canonical_key: $key})", {"key": "compost"})
    await close_driver()  # call in main.py lifespan shutdown
"""
from __future__ import annotations

import structlog
import neo4j
import neo4j.exceptions

from app.core.config import settings

logger = structlog.get_logger(__name__)

_driver: neo4j.AsyncDriver | None = None


def _get_driver() -> neo4j.AsyncDriver:
    """Return the module-level async Neo4j driver, creating it if needed."""
    global _driver
    if _driver is None:
        _driver = neo4j.AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        )
        logger.info("neo4j_driver_created", uri=settings.NEO4J_URI)
    return _driver


async def run_read(cypher: str, params: dict | None = None) -> list[dict]:
    """Execute a read-only Cypher query and return rows as plain dicts.

    Args:
        cypher: Cypher query string with $param placeholders.
        params: Query parameters dict.

    Returns:
        List of result rows as plain Python dicts.

    Raises:
        neo4j.exceptions.ServiceUnavailable: If Neo4j is not reachable.
    """
    driver = _get_driver()
    params = params or {}
    async with driver.session() as session:
        result = await session.run(cypher, params)
        records = await result.data()
        return records


async def run_write(cypher: str, params: dict | None = None) -> None:
    """Execute a write Cypher query (MERGE, SET, etc.).

    Args:
        cypher: Cypher write query with $param placeholders.
        params: Query parameters dict.
    """
    driver = _get_driver()
    params = params or {}
    async with driver.session() as session:
        await session.run(cypher, params)


async def close_driver() -> None:
    """Close the Neo4j driver. Call this during app lifespan shutdown."""
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None
        logger.info("neo4j_driver_closed")


async def is_reachable() -> bool:
    """Return True if Neo4j is reachable (used by health check)."""
    try:
        await run_read("RETURN 1 AS ok")
        return True
    except Exception:
        return False
