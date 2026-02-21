"""Qdrant client helpers — collection management and chunk upsert.

Collection spec (matches init_qdrant.py / Alembic migration 0005):
  name: chunks_multilingual
  vector_size: 4096 (Qwen3-Embedding-8B native output dimension)
  distance: Cosine
  hnsw: m=16, ef_construct=200

Payload indexes (for fast filtered search):
  book_id, chunk_type, language_detected, chapter_id
"""
from __future__ import annotations

import structlog
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    HnswConfigDiff,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

logger = structlog.get_logger(__name__)

_HNSW = HnswConfigDiff(m=16, ef_construct=200)
_PAYLOAD_INDEXES: list[tuple[str, PayloadSchemaType]] = [
    ("book_id",           PayloadSchemaType.KEYWORD),
    ("chunk_type",        PayloadSchemaType.KEYWORD),
    ("language_detected", PayloadSchemaType.KEYWORD),
    ("chapter_id",        PayloadSchemaType.KEYWORD),
]


def get_client(
    host: str = "localhost",
    port: int = 6333,
    api_key: str | None = None,
) -> QdrantClient:
    """Return a Qdrant client (no connection made until first call).

    Note: api_key must be None (not empty string) to avoid qdrant-client
    auto-enabling HTTPS for local deployments without TLS.
    """
    return QdrantClient(
        host=host,
        port=port,
        api_key=api_key or None,
        https=bool(api_key),  # only HTTPS when a real API key is configured
    )


def ensure_collection(
    client: QdrantClient,
    collection_name: str,
    dim: int = 1024,
) -> None:
    """Create the collection with HNSW + payload indexes if it does not exist.

    Idempotent — safe to call on every startup.
    """
    existing = {c.name for c in client.get_collections().collections}
    if collection_name in existing:
        logger.info("qdrant_collection_exists", name=collection_name)
        return

    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        hnsw_config=_HNSW,
    )
    for field_name, schema in _PAYLOAD_INDEXES:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field_name,
            field_schema=schema,
        )
    logger.info("qdrant_collection_created", name=collection_name, dim=dim)


def build_point(
    point_id: str,
    vector: list[float],
    *,
    book_id: str,
    chunk_type: str,
    language_detected: str,
    page_start: int | None,
    page_end: int | None,
    section_title: str | None,
    text_hash: str,
    embedding_model_id: str,
    chapter_id: str | None = None,
) -> PointStruct:
    """Build a Qdrant PointStruct ready for upsert."""
    return PointStruct(
        id=point_id,
        vector=vector,
        payload={
            "book_id": book_id,
            "chapter_id": chapter_id,
            "chunk_type": chunk_type,
            "language_detected": language_detected,
            "page_start": page_start,
            "page_end": page_end,
            "section_title": section_title,
            "text_hash": text_hash,
            "embedding_model_id": embedding_model_id,
        },
    )


def upsert_points(
    client: QdrantClient,
    collection_name: str,
    points: list[PointStruct],
    batch_size: int = 64,
) -> int:
    """Upsert points in batches. Returns total count upserted."""
    total = 0
    for i in range(0, len(points), batch_size):
        batch = points[i : i + batch_size]
        client.upsert(collection_name=collection_name, points=batch)
        total += len(batch)
    logger.info("qdrant_upserted", collection=collection_name, count=total)
    return total
