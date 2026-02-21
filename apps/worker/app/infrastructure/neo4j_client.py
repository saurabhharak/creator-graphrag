"""Worker-side synchronous Neo4j client for the graph build stage.

Uses the sync neo4j driver. Called via asyncio.to_thread() from the
async ingestion pipeline to avoid blocking the event loop.

Usage (from ingestion_pipeline.py):
    driver = get_driver(uri=..., user=..., password=...)
    try:
        results = run_query(driver, "MATCH (c:Concept) RETURN c", {})
    finally:
        close_driver(driver)
"""
from __future__ import annotations

import structlog
import neo4j
import neo4j.exceptions

logger = structlog.get_logger(__name__)


def get_driver(uri: str, user: str, password: str) -> neo4j.Driver:
    """Create and return a synchronous Neo4j driver.

    A new driver is created per pipeline run and closed afterwards
    (see close_driver). This avoids sharing a driver across threads.

    Args:
        uri: Neo4j bolt URI (e.g. bolt://localhost:7687).
        user: Neo4j username.
        password: Neo4j password.

    Returns:
        A connected neo4j.Driver instance.
    """
    driver = neo4j.GraphDatabase.driver(uri, auth=(user, password))
    logger.info("neo4j_worker_driver_created", uri=uri)
    return driver


def run_query(
    driver: neo4j.Driver,
    cypher: str,
    params: dict | None = None,
) -> list[dict]:
    """Execute a Cypher query (read or write) and return rows as dicts.

    Args:
        driver: An open neo4j.Driver.
        cypher: Cypher query string with $param placeholders.
        params: Query parameters dict.

    Returns:
        List of result rows as plain Python dicts.
    """
    params = params or {}
    with driver.session() as session:
        result = session.run(cypher, params)
        return [dict(record) for record in result]


def close_driver(driver: neo4j.Driver) -> None:
    """Close the driver and release its connection pool."""
    driver.close()
    logger.info("neo4j_worker_driver_closed")
