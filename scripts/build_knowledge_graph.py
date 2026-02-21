"""Build Neo4j graph from PostgreSQL knowledge units.

Loads all extracted knowledge units from PostgreSQL and uses the GraphBuilder
to insert them into Neo4j using MERGE operations.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Add project root to sys.path so we can import apps.*
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from apps.worker.app.pipelines.graph_builder import build_graph_for_units


async def fetch_all_units(pg_url: str) -> list[dict]:
    import asyncpg

    raw_url = pg_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_url)
    try:
        rows = await conn.fetch(
            """
            SELECT
                unit_id::text,
                source_book_id::text,
                type,
                language_detected,
                subject,
                predicate,
                object,
                confidence,
                status,
                canonical_key,
                evidence_jsonb::text
            FROM knowledge_units
            WHERE deleted_at IS NULL AND status != 'rejected'
            """
        )
        # Convert to dict format expected by graph_builder
        units = []
        for r in rows:
            ev = json.loads(r["evidence_jsonb"]) if r["evidence_jsonb"] else []
            units.append(
                {
                    "unit_id": r["unit_id"],
                    "source_book_id": r["source_book_id"],
                    "type": r["type"],
                    "language_detected": r["language_detected"],
                    "subject": r["subject"],
                    "predicate": r["predicate"],
                    "object": r["object"],
                    "confidence": r["confidence"],
                    "status": r["status"],
                    "canonical_key": r["canonical_key"],
                    "evidence_jsonb": ev,
                }
            )
        return units
    finally:
        await conn.close()


def sync_build_graph(neo4j_uri: str, neo4j_auth: tuple, units: list[dict]) -> None:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(neo4j_uri, auth=neo4j_auth)
    try:
        print(f"Building graph for {len(units)} units...")
        # Since it takes the full list and does it iteratively, we can pass it all.
        # But to have a progress output, let's chunk it into batches of 500
        batch_size = 500
        total_merged = 0
        for i in range(0, len(units), batch_size):
            batch = units[i : i + batch_size]
            merged = build_graph_for_units(driver, batch)
            total_merged += merged
            print(f"  Processed {min(i + batch_size, len(units))}/{len(units)} units, merged {merged} new nodes in this batch")
            
        print(f"\nDone! Sent {len(units)} units to Neo4j. Total nodes merged: {total_merged}")
    finally:
        driver.close()


async def main():
    import os
    from dotenv import load_dotenv

    # Load .env file
    env_path = project_root / ".env"
    load_dotenv(env_path)

    # Database URLs
    pg_url = os.environ.get("DATABASE_URL", "postgresql://cgr_user:changeme_required@localhost:5432/creator_graphrag")
    neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    
    # Check for the dev password if not in env or if it's the required one
    neo4j_pass = os.environ.get("NEO4J_PASSWORD", "changeme_dev")
    neo4j_auth = (neo4j_user, neo4j_pass)

    print(f"PostgreSQL URL: {pg_url.replace('postgresql+asyncpg', 'postgresql')}")
    print(f"Neo4j URI: {neo4j_uri} (User: {neo4j_user})")

    print(f"\nFetching units from PostgreSQL...")
    units = await fetch_all_units(pg_url)
    print(f"Found {len(units)} knowledge units.")

    if units:
        # Run synchronous neo4j task in a thread
        await asyncio.to_thread(sync_build_graph, neo4j_uri, neo4j_auth, units)


if __name__ == "__main__":
    asyncio.run(main())
