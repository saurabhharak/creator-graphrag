"""Qdrant vector search client for the API service.

Uses the synchronous QdrantClient wrapped in asyncio.to_thread so FastAPI's
event loop is never blocked. A module-level singleton is cached via lru_cache.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache

import structlog
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchAny, Range

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def _client() -> QdrantClient:
    from app.core.config import settings
    return QdrantClient(
        host=settings.QDRANT_HOST,
        port=settings.QDRANT_PORT,
        # Treat empty string as None to prevent qdrant-client from auto-enabling HTTPS
        api_key=settings.QDRANT_API_KEY or None,
        https=bool(settings.QDRANT_API_KEY),  # only HTTPS when a real API key is configured
        check_compatibility=False,
    )


async def vector_search(
    collection: str,
    query_vector: list[float],
    top_k: int,
    *,
    book_ids: list[str] | None = None,
    chunk_types: list[str] | None = None,
    languages: list[str] | None = None,
    page_min: int | None = None,
    page_max: int | None = None,
) -> list[dict]:
    """Async vector search against Qdrant.

    Builds payload filter from non-empty filter arguments, then executes a
    cosine similarity search and returns results as plain dicts.

    Args:
        collection: Qdrant collection name.
        query_vector: Embedded query vector (must match collection dimension).
        top_k: Maximum number of results to return.
        book_ids: Restrict to these book_id values (OR logic within the list).
        chunk_types: Restrict to these chunk_type values.
        languages: Restrict to these language_detected values.
        page_min: Minimum page_start value (inclusive).
        page_max: Maximum page_end value (inclusive).

    Returns:
        List of dicts with keys: ``id`` (str), ``score`` (float), ``payload`` (dict).
    """
    must: list = []
    if book_ids:
        must.append(FieldCondition(key="book_id", match=MatchAny(any=book_ids)))
    if chunk_types:
        must.append(FieldCondition(key="chunk_type", match=MatchAny(any=chunk_types)))
    if languages:
        must.append(FieldCondition(key="language_detected", match=MatchAny(any=languages)))
    if page_min is not None:
        must.append(FieldCondition(key="page_start", range=Range(gte=page_min)))
    if page_max is not None:
        must.append(FieldCondition(key="page_end", range=Range(lte=page_max)))

    search_filter = Filter(must=must) if must else None

    def _search():
        return _client().query_points(
            collection_name=collection,
            query=query_vector,
            query_filter=search_filter,
            limit=top_k,
            with_payload=True,
        ).points

    hits = await asyncio.to_thread(_search)
    logger.debug("qdrant_search_done", hits=len(hits), collection=collection)
    return [
        {"id": str(hit.id), "score": hit.score, "payload": hit.payload or {}}
        for hit in hits
    ]
