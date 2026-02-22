"""
Rebuild Neo4j graph from existing knowledge_units in PostgreSQL.

This is a one-time script that reads all knowledge units from Postgres
and MERGEs them into Neo4j as Concept/Process nodes + relationships.
It does NOT re-ingest books or call GPT — it only uses data already in Postgres.

Usage (from project root):
    cd apps/api
    python -m scripts.rebuild_neo4j_graph

The script uses the same .env config as the API server.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# Add project paths so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import neo4j
import psycopg2

# Load config from .env
from app.core.config import settings


def get_pg_connection():
    """Create a sync psycopg2 connection from the async DATABASE_URL."""
    # Convert async URL to sync: postgresql+asyncpg:// → postgresql://
    sync_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    return psycopg2.connect(sync_url)


def fetch_all_knowledge_units(conn) -> list[dict]:
    """Fetch all active knowledge units from PostgreSQL."""
    cur = conn.cursor()
    cur.execute("""
        SELECT unit_id, source_book_id, source_chunk_id, type,
               language_detected, language_confidence,
               subject, predicate, object,
               confidence, status, canonical_key,
               evidence_jsonb
        FROM knowledge_units
        WHERE deleted_at IS NULL
          AND status != 'rejected'
        ORDER BY created_at
    """)
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    cur.close()
    return [dict(zip(columns, row)) for row in rows]


def build_graph_for_units(driver: neo4j.Driver, units: list[dict]) -> int:
    """MERGE Concept nodes and relationships — same logic as worker/graph_builder.py"""
    import re
    import unicodedata

    RELATION_TYPE_MAP = {
        "is": "IS_A", "is a": "IS_A", "defines": "IS_A", "defined as": "IS_A",
        "improves": "IMPROVES", "improve": "IMPROVES",
        "causes": "CAUSES", "cause": "CAUSES", "leads to": "CAUSES",
        "requires": "REQUIRES", "require": "REQUIRES", "needs": "REQUIRES",
        "produces": "PRODUCES", "produce": "PRODUCES", "results in": "PRODUCES",
        "inhibits": "INHIBITS", "inhibit": "INHIBITS", "reduces": "INHIBITS",
        "contains": "CONTAINS", "contain": "CONTAINS", "includes": "CONTAINS",
        "supports": "SUPPORTS", "support": "SUPPORTS", "promotes": "SUPPORTS",
        "compares": "COMPARED_TO", "compared to": "COMPARED_TO",
    }
    LANG_PROP = {"en": "label_en", "mr": "label_mr", "hi": "label_hi"}

    def to_rel_type(predicate):
        if not predicate:
            return "RELATES_TO"
        key = predicate.strip().lower()
        if key in RELATION_TYPE_MAP:
            return RELATION_TYPE_MAP[key]
        first_word = key.split()[0] if key.split() else key
        if first_word in RELATION_TYPE_MAP:
            return RELATION_TYPE_MAP[first_word]
        normalized = re.sub(r"[\s\-]+", "_", key.upper())
        normalized = re.sub(r"[^\w]", "", normalized)
        return normalized[:50] or "RELATES_TO"

    def norm_key(text):
        text = unicodedata.normalize("NFC", text)
        text = text.lower()
        text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
        return " ".join(text.split())

    merged_count = 0
    errors = 0

    with driver.session() as session:
        for i, unit in enumerate(units):
            try:
                unit_type = unit.get("type", "")
                lang = unit.get("language_detected", "en") or "en"
                lang_prop = LANG_PROP.get(lang.lower(), "label_en")
                book_id = str(unit.get("source_book_id", ""))
                unit_id = str(unit.get("unit_id", ""))
                confidence = float(unit.get("confidence", 0.5) or 0.5)
                evidence = unit.get("evidence_jsonb") or []
                evidence_count = len(evidence) if isinstance(evidence, list) else 0

                subject = unit.get("subject")
                subject_key = unit.get("canonical_key")
                obj = unit.get("object")
                predicate = unit.get("predicate")

                if unit_type == "process" and subject and subject_key:
                    obj_key = norm_key(subject + "_process")
                    session.run(
                        f"""
                        MERGE (s:Concept {{canonical_key: $s_key}})
                        ON CREATE SET s.created_at = datetime()
                        SET s.{lang_prop} = $subject,
                            s.book_ids = CASE WHEN $book_id IN coalesce(s.book_ids, [])
                                         THEN s.book_ids
                                         ELSE coalesce(s.book_ids, []) + [$book_id] END
                        MERGE (p:Process {{id: $p_id}})
                        ON CREATE SET p.created_at = datetime(), p.book_id = $book_id
                        MERGE (s)-[r:DESCRIBES_PROCESS {{unit_id: $unit_id}}]->(p)
                        SET r.confidence = $confidence
                        """,
                        s_key=subject_key, subject=subject, p_id=obj_key,
                        book_id=book_id, unit_id=unit_id, confidence=confidence,
                    )
                    merged_count += 1

                elif unit_type == "definition" and subject and subject_key:
                    session.run(
                        f"""
                        MERGE (s:Concept {{canonical_key: $s_key}})
                        ON CREATE SET s.created_at = datetime()
                        SET s.{lang_prop} = $subject,
                            s.book_ids = CASE WHEN $book_id IN coalesce(s.book_ids, [])
                                         THEN s.book_ids
                                         ELSE coalesce(s.book_ids, []) + [$book_id] END
                        """,
                        s_key=subject_key, subject=subject, book_id=book_id,
                    )
                    merged_count += 1

                elif unit_type in ("claim", "comparison") and subject and subject_key and obj:
                    obj_key = norm_key(obj)
                    rel_type = to_rel_type(predicate)
                    session.run(
                        f"""
                        MERGE (s:Concept {{canonical_key: $s_key}})
                        ON CREATE SET s.created_at = datetime()
                        SET s.{lang_prop} = $subject,
                            s.book_ids = CASE WHEN $book_id IN coalesce(s.book_ids, [])
                                         THEN s.book_ids
                                         ELSE coalesce(s.book_ids, []) + [$book_id] END
                        MERGE (o:Concept {{canonical_key: $o_key}})
                        ON CREATE SET o.created_at = datetime()
                        SET o.{lang_prop} = $obj,
                            o.book_ids = CASE WHEN $book_id IN coalesce(o.book_ids, [])
                                         THEN o.book_ids
                                         ELSE coalesce(o.book_ids, []) + [$book_id] END
                        MERGE (s)-[r:{rel_type} {{unit_id: $unit_id}}]->(o)
                        SET r.confidence = $confidence, r.evidence_count = $ev_count
                        """,
                        s_key=subject_key, subject=subject, o_key=obj_key, obj=obj,
                        book_id=book_id, unit_id=unit_id, confidence=confidence,
                        ev_count=evidence_count,
                    )
                    merged_count += 2

                # Progress indicator every 100 units
                if (i + 1) % 100 == 0:
                    print(f"  Processed {i + 1}/{len(units)} units... ({merged_count} nodes merged)")

            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  WARNING: Error on unit {unit_id}: {e}")

    return merged_count


def main():
    print("=" * 60)
    print("  Neo4j Graph Rebuild from PostgreSQL Knowledge Units")
    print("=" * 60)
    print()

    # 1. Connect to PostgreSQL
    print("[1/4] Connecting to PostgreSQL...")
    try:
        conn = get_pg_connection()
        print(f"  Connected: {settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}")
    except Exception as e:
        print(f"  ERROR: Could not connect to PostgreSQL: {e}")
        sys.exit(1)

    # 2. Fetch knowledge units
    print("[2/4] Fetching knowledge units...")
    units = fetch_all_knowledge_units(conn)
    conn.close()
    print(f"  Found {len(units)} active knowledge units")

    if not units:
        print("  No units to process. Exiting.")
        sys.exit(0)

    # Show breakdown by type
    from collections import Counter
    type_counts = Counter(u.get("type", "unknown") for u in units)
    for t, c in type_counts.most_common():
        print(f"    {t}: {c}")

    # 3. Connect to Neo4j
    print("[3/4] Connecting to Neo4j...")
    try:
        driver = neo4j.GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        )
        # Verify connectivity
        driver.verify_connectivity()
        print(f"  Connected: {settings.NEO4J_URI}")
    except Exception as e:
        print(f"  ERROR: Could not connect to Neo4j: {e}")
        sys.exit(1)

    # 4. Build graph
    print(f"[4/4] Building graph from {len(units)} units...")
    start = time.time()
    merged = build_graph_for_units(driver, units)
    elapsed = time.time() - start

    # Final stats
    with driver.session() as session:
        result = session.run("MATCH (n) RETURN count(n) AS nodes")
        node_count = result.single()["nodes"]
        result = session.run("MATCH ()-[r]->() RETURN count(r) AS rels")
        rel_count = result.single()["rels"]

    driver.close()

    print()
    print("=" * 60)
    print(f"  DONE in {elapsed:.1f}s")
    print(f"  Nodes merged:         {merged}")
    print(f"  Total nodes in Neo4j: {node_count}")
    print(f"  Total relationships:  {rel_count}")
    print("=" * 60)


if __name__ == "__main__":
    main()
