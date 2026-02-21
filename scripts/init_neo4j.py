"""
Initialize Neo4j schema for the Creator GraphRAG Knowledge Graph.

Creates:
  - Constraints (unique canonical_key on Concept nodes)
  - Indexes for fast traversal
  - Validates APOC plugin is available (required for atomic MERGE operations)

Run:
  python scripts/init_neo4j.py
"""
from __future__ import annotations
import os
import sys

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme_dev")


def init_neo4j():
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    with driver.session() as session:
        # Verify APOC is available (required for alias dedup in merge)
        try:
            result = session.run("RETURN apoc.version() AS version")
            apoc_version = result.single()["version"]
            print(f"APOC plugin available: v{apoc_version}")
        except Exception as e:
            print(f"WARNING: APOC plugin not available: {e}")
            print("APOC is required for atomic concept merging. Install: https://neo4j.com/labs/apoc/")

        # Node constraints
        constraints = [
            "CREATE CONSTRAINT concept_canonical_key IF NOT EXISTS FOR (c:Concept) REQUIRE c.canonical_key IS UNIQUE",
            "CREATE CONSTRAINT process_id IF NOT EXISTS FOR (p:Process) REQUIRE p.id IS UNIQUE",
            "CREATE CONSTRAINT material_id IF NOT EXISTS FOR (m:Material) REQUIRE m.id IS UNIQUE",
            "CREATE CONSTRAINT outcome_id IF NOT EXISTS FOR (o:Outcome) REQUIRE o.id IS UNIQUE",
        ]
        for cypher in constraints:
            session.run(cypher)
            print(f"  Constraint: {cypher[:60]}...")

        # Indexes for fast concept search
        indexes = [
            "CREATE INDEX concept_label_en IF NOT EXISTS FOR (c:Concept) ON (c.label_en)",
            "CREATE INDEX concept_label_mr IF NOT EXISTS FOR (c:Concept) ON (c.label_mr)",
            "CREATE INDEX concept_label_hi IF NOT EXISTS FOR (c:Concept) ON (c.label_hi)",
            "CREATE INDEX concept_book_id IF NOT EXISTS FOR (c:Concept) ON (c.book_id)",
        ]
        for cypher in indexes:
            session.run(cypher)
            print(f"  Index: {cypher[:60]}...")

        print("\nNeo4j initialization complete.")

    driver.close()


if __name__ == "__main__":
    init_neo4j()
