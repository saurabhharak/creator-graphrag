"""Celery task: cross-lingual entity resolution for Neo4j Concept nodes.

Fix 7 — Cross-lingual entity resolution:
  After graph build, concept labels are embedded and stored in a dedicated
  Qdrant collection (`concept_labels`). Pairs with cosine similarity > 0.92
  and different canonical keys are linked with :SAME_AS relationships in
  Neo4j, enabling cross-lingual retrieval so that equivalent concepts from
  different languages cluster together:

      cow dung ←—SAME_AS—→ शेण ←—SAME_AS—→ gomaya

Algorithm:
  1. Query Neo4j for all Concept nodes that include this book's ID.
  2. Extract the primary label text (label_en → label_mr → label_hi → label_sa).
  3. Batch embed all labels using Ollama /api/embed (same model as chunks).
  4. Upsert concept embeddings into the `concept_labels` Qdrant collection.
     Point IDs are deterministic: UUID5(CONCEPT_NAMESPACE, canonical_key).
  5. For each newly ingested concept, run ANN query in `concept_labels`.
  6. For hits with score > SAME_AS_THRESHOLD and different canonical_key,
     MERGE a :SAME_AS relationship in Neo4j.

The `concept_labels` collection uses the same model and dimension as
`chunks_multilingual` (qwen3-embedding:8b, 4096-dim).

Usage:
  Triggered automatically by ingest.py after a successful graph build:
    canonicalize_concepts.apply_async(args=[book_id], countdown=10, queue="graph")

  Can also be run manually for a book:
    from app.tasks.canonicalize_graph import canonicalize_concepts
    canonicalize_concepts.delay(book_id="<uuid>")
"""
from __future__ import annotations

import uuid

import structlog
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

import app.infrastructure.neo4j_client as worker_neo4j
from app.core.config import worker_settings
from app.infrastructure.qdrant_client import get_client, upsert_points
from app.pipelines.embedder import embed_batch
from app.worker import app

logger = structlog.get_logger(__name__)

# Cosine similarity threshold for declaring two concepts the same entity.
# 0.92 is conservative — avoids false positives while catching clear aliases.
# Lower to 0.88 if too few links are created; raise to 0.95 if too many.
SAME_AS_THRESHOLD: float = 0.92

# Qdrant collection for concept label embeddings (separate from chunk collection).
# Smaller collection: O(unique concepts) vs O(chunks). No HNSW tuning needed.
CONCEPT_LABELS_COLLECTION: str = "concept_labels"

# Fixed UUID namespace for deterministic concept point IDs.
# RFC 4122 URL namespace — ensures stable IDs across ingestion runs.
_CONCEPT_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

# Label properties to check in priority order when selecting the primary label.
_LABEL_PRIORITY = ("label_en", "label_mr", "label_hi", "label_sa")


def _concept_point_id(canonical_key: str) -> str:
    """Return a deterministic UUID for a concept, derived from its canonical key."""
    return str(uuid.uuid5(_CONCEPT_NAMESPACE, canonical_key))


def _ensure_concept_labels_collection(client: QdrantClient, dim: int = 4096) -> None:
    """Create the concept_labels collection if it does not already exist."""
    existing = {c.name for c in client.get_collections().collections}
    if CONCEPT_LABELS_COLLECTION in existing:
        return
    client.create_collection(
        collection_name=CONCEPT_LABELS_COLLECTION,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    logger.info("concept_labels_collection_created", dim=dim)


def _get_primary_label(node: dict) -> str | None:
    """Extract the first non-empty label from a Concept node dict."""
    for prop in _LABEL_PRIORITY:
        val = node.get(prop)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return None


@app.task(
    bind=True,
    name="app.tasks.canonicalize_graph.canonicalize_concepts",
    max_retries=2,
    default_retry_delay=120,
    queue="graph",
    acks_late=True,
    soft_time_limit=300,
    time_limit=360,
)
def canonicalize_concepts(self, book_id: str) -> dict:
    """Embed concept labels and create :SAME_AS links for near-duplicate concepts.

    Triggered automatically after a successful graph build for a book.
    Safe to call multiple times — MERGE is idempotent.

    Args:
        book_id: UUID string of the book whose newly ingested concepts to process.

    Returns:
        Dict with counts: {"concepts_processed": N, "same_as_links_created": M}.
    """
    logger.info("canonicalize_start", book_id=book_id)

    # ── Step 1: Fetch Concept nodes for this book from Neo4j ──────────────────
    driver = worker_neo4j.get_driver(
        uri=worker_settings.NEO4J_URI,
        user=worker_settings.NEO4J_USER,
        password=worker_settings.NEO4J_PASSWORD,
    )
    try:
        rows = worker_neo4j.run_query(
            driver,
            """
            MATCH (c:Concept)
            WHERE $book_id IN c.book_ids
            RETURN c.canonical_key AS canonical_key,
                   c.label_en      AS label_en,
                   c.label_mr      AS label_mr,
                   c.label_hi      AS label_hi,
                   c.label_sa      AS label_sa
            """,
            {"book_id": book_id},
        )
    finally:
        worker_neo4j.close_driver(driver)

    if not rows:
        logger.info("canonicalize_no_concepts", book_id=book_id)
        return {"concepts_processed": 0, "same_as_links_created": 0}

    # ── Step 2: Build (canonical_key, primary_label) pairs ───────────────────
    concepts: list[dict] = []
    for row in rows:
        label = _get_primary_label(row)
        if label and row.get("canonical_key"):
            concepts.append({
                "canonical_key": row["canonical_key"],
                "label": label,
            })

    if not concepts:
        logger.info("canonicalize_no_labelable_concepts", book_id=book_id)
        return {"concepts_processed": 0, "same_as_links_created": 0}

    logger.info("canonicalize_concepts_found", book_id=book_id, count=len(concepts))

    # ── Step 3: Batch embed all primary labels ────────────────────────────────
    labels = [c["label"] for c in concepts]
    try:
        embed_results = embed_batch(
            labels,
            endpoint=worker_settings.OLLAMA_ENDPOINT,
            model=worker_settings.EMBEDDING_MODEL,
        )
    except Exception as exc:
        logger.warning("canonicalize_embed_failed", book_id=book_id, error=str(exc))
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        raise

    if len(embed_results) != len(concepts):
        logger.warning(
            "canonicalize_embed_count_mismatch",
            expected=len(concepts),
            got=len(embed_results),
        )
        return {"concepts_processed": 0, "same_as_links_created": 0}

    # ── Step 4: Upsert concept embeddings into concept_labels collection ──────
    qdrant = get_client(
        host=worker_settings.QDRANT_HOST,
        port=worker_settings.QDRANT_PORT,
    )
    _ensure_concept_labels_collection(qdrant, dim=worker_settings.EMBEDDING_DIMENSION)

    points = [
        PointStruct(
            id=_concept_point_id(concepts[i]["canonical_key"]),
            vector=embed_results[i].vector,
            payload={
                "canonical_key": concepts[i]["canonical_key"],
                "label": concepts[i]["label"],
            },
        )
        for i in range(len(concepts))
    ]
    upsert_points(qdrant, CONCEPT_LABELS_COLLECTION, points, batch_size=64)
    logger.info("concept_embeddings_upserted", count=len(points))

    # ── Step 5: ANN search — find near-duplicates for each concept ────────────
    same_as_pairs: list[tuple[str, str, float]] = []  # (key_a, key_b, score)
    seen_pairs: set[frozenset] = set()

    for i, concept in enumerate(concepts):
        results = qdrant.query_points(
            collection_name=CONCEPT_LABELS_COLLECTION,
            query=embed_results[i].vector,
            limit=6,         # +1 to always skip self
            with_payload=True,
        ).points

        for hit in results:
            hit_key = (hit.payload or {}).get("canonical_key", "")
            if not hit_key:
                continue
            # Skip self-match
            if hit_key == concept["canonical_key"]:
                continue
            # Skip below threshold
            if hit.score < SAME_AS_THRESHOLD:
                continue
            # Skip already-seen pairs (deduplication)
            pair_key = frozenset({concept["canonical_key"], hit_key})
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            same_as_pairs.append((concept["canonical_key"], hit_key, hit.score))

    logger.info(
        "canonicalize_pairs_found",
        book_id=book_id,
        candidate_pairs=len(same_as_pairs),
    )

    # ── Step 6: Create :SAME_AS relationships in Neo4j ───────────────────────
    same_as_count = 0
    if same_as_pairs:
        driver = worker_neo4j.get_driver(
            uri=worker_settings.NEO4J_URI,
            user=worker_settings.NEO4J_USER,
            password=worker_settings.NEO4J_PASSWORD,
        )
        try:
            for a_key, b_key, score in same_as_pairs:
                worker_neo4j.run_query(
                    driver,
                    """
                    MATCH (a:Concept {canonical_key: $a_key})
                    MATCH (b:Concept {canonical_key: $b_key})
                    MERGE (a)-[r:SAME_AS]->(b)
                    ON CREATE SET r.similarity    = $score,
                                  r.created_at    = datetime(),
                                  r.reason        = "embedding_similarity"
                    """,
                    {"a_key": a_key, "b_key": b_key, "score": round(score, 4)},
                )
                same_as_count += 1
                logger.info(
                    "same_as_link_created",
                    a=a_key[:50],
                    b=b_key[:50],
                    score=round(score, 4),
                )
        finally:
            worker_neo4j.close_driver(driver)

    logger.info(
        "canonicalize_complete",
        book_id=book_id,
        concepts_processed=len(concepts),
        same_as_links_created=same_as_count,
    )
    return {
        "concepts_processed": len(concepts),
        "same_as_links_created": same_as_count,
    }
