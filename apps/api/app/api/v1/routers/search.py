"""Hybrid search endpoint — vector similarity + optional graph context.

Flow:
    1. Sanitize query (length + basic injection guard).
    2. Embed query via Ollama (Qwen3-Embedding-8B, 4096-dim).
    3. Execute Qdrant ANN search with optional payload filters.
    4. (Future) Traverse Neo4j for graph-augmented outline beats.
    5. Return ranked SearchResult list.

Performance target: p95 < 500 ms (US-SEARCH-01).
"""
from __future__ import annotations

import re
import uuid as _uuid

import httpx
import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.v1.deps import CurrentUserDep
from app.core.config import settings
from app.infrastructure.graph import neo4j_client as graph_db
from app.infrastructure.vector.qdrant import vector_search

logger = structlog.get_logger(__name__)
router = APIRouter()

# Basic prompt-injection guard: strip known LLM instruction prefixes (US-SEC-02)
_INJECTION_RE = re.compile(
    r"^\s*(ignore\s+(previous|all)\s+instructions?|system\s*:)", re.IGNORECASE
)


# ─── Schemas ────────────────────────────────────────────────────────────────

class SearchFilters(BaseModel):
    book_ids: list[str] = []
    chunk_types: list[str] = []
    languages: list[str] = []
    page_min: int | None = None
    page_max: int | None = None
    tags: list[str] = []


class GraphOptions(BaseModel):
    enable: bool = False  # disabled until Phase 2 graph is built
    max_hops: int = Field(default=2, ge=1, le=4)
    relation_types: list[str] = []


class HybridSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    query_language: str | None = None
    top_k: int = Field(default=10, ge=1, le=50)
    filters: SearchFilters = SearchFilters()
    graph: GraphOptions = GraphOptions()


class Citation(BaseModel):
    book_id: str
    book_name: str
    language: str
    page_start: int | None
    page_end: int | None


class SearchResult(BaseModel):
    chunk_id: str
    book_id: str
    book_name: str | None
    chapter_id: str | None
    chunk_type: str
    language_detected: str
    page_start: int | None
    page_end: int | None
    section_title: str | None
    score: float
    text_preview: str   # first 300 chars
    citations: list[Citation]


class HybridSearchResponse(BaseModel):
    query: str
    total: int
    results: list[SearchResult]
    graph_plan: dict | None = None


# ─── Internal helpers ────────────────────────────────────────────────────────

def _sanitize(query: str) -> str:
    """Strip leading injection patterns and control characters."""
    cleaned = _INJECTION_RE.sub("", query).strip()
    # Remove ASCII control characters (keep printable + Devanagari + CJK etc.)
    cleaned = "".join(c for c in cleaned if c >= " " or c in "\t\n")
    return cleaned[:2000]


async def _embed(query: str) -> list[float]:
    """Embed a query string. Uses EMBEDDING_PROVIDER setting (ollama or huggingface)."""
    if settings.EMBEDDING_PROVIDER == "huggingface":
        return await _embed_via_huggingface(query)
    return await _embed_via_ollama(query)


async def _embed_via_ollama(query: str) -> list[float]:
    """Embed via local Ollama instance."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            resp = await client.post(
                f"{settings.OLLAMA_ENDPOINT}/api/embeddings",
                json={"model": settings.EMBEDDING_MODEL, "prompt": query},
            )
            resp.raise_for_status()
            return resp.json()["embedding"]
        except httpx.TimeoutException as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"Ollama timed out (model may be loading): {exc}",
            )
        except httpx.ConnectError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Ollama unavailable. "
                    f"Ensure Ollama is running at {settings.OLLAMA_ENDPOINT} "
                    f"with model '{settings.EMBEDDING_MODEL}' pulled."
                ),
            )
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Ollama error: {exc.response.text[:200]}",
            )


async def _embed_via_huggingface(query: str) -> list[float]:
    """Embed via HuggingFace Inference API (Scaleway endpoint)."""
    if not settings.HF_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="HF_TOKEN not configured. Set HF_TOKEN in .env to use HuggingFace embeddings.",
        )
    # Convert Ollama model name to HF format: "qwen3-embedding:8b" → "qwen3-embedding-8b"
    hf_model = settings.EMBEDDING_MODEL.replace(":", "-")
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                settings.HF_EMBEDDING_URL,
                headers={"Authorization": f"Bearer {settings.HF_TOKEN}"},
                json={"input": query, "model": hf_model},
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "data" in data:
                return data["data"][0]["embedding"]
            if isinstance(data, list) and len(data) > 0:
                return data[0] if isinstance(data[0], list) else data
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Unexpected HF embedding response format: {str(data)[:200]}",
            )
        except httpx.TimeoutException as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"HuggingFace embedding timed out: {exc}",
            )
        except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"HuggingFace embedding error: {exc}",
            )


async def _build_graph_plan(query: str, graph_opts: GraphOptions) -> dict | None:
    """Return graph-augmented outline beats for the query.

    1. Finds Concept nodes whose label contains words from the query.
    2. For each concept (up to 5) fetches its 1-hop neighbors.
    3. Assembles a beats list suitable for frontend outline rendering.

    Returns None silently on any Neo4j error (non-critical path).
    """
    # Lowercase query words longer than 3 chars as search terms
    terms = [w for w in query.lower().split() if len(w) > 3]
    if not terms:
        return None

    q_lower = query[:100].lower()

    try:
        rel_types_filter = ""
        if graph_opts.relation_types:
            rel_types_filter = ":" + "|".join(graph_opts.relation_types)

        # Find matching concept nodes
        concepts = await graph_db.run_read(
            """
            MATCH (c:Concept)
            WHERE toLower(coalesce(c.label_en, '')) CONTAINS $q
               OR toLower(coalesce(c.label_mr, '')) CONTAINS $q
               OR toLower(coalesce(c.label_hi, '')) CONTAINS $q
            RETURN c.canonical_key AS canonical_key,
                   c.label_en AS label_en,
                   c.label_mr AS label_mr
            LIMIT 5
            """,
            {"q": q_lower},
        )

        if not concepts:
            return None

        beats = []
        for concept in concepts:
            canonical_key = concept.get("canonical_key") or ""
            if not canonical_key:
                continue

            # Fetch 1-hop neighbors with relationship types
            neighbors = await graph_db.run_read(
                f"""
                MATCH (c:Concept {{canonical_key: $key}})-[r{rel_types_filter}]-(n:Concept)
                RETURN type(r) AS rel_type, n.canonical_key AS neighbor_key
                LIMIT 10
                """,
                {"key": canonical_key},
            )

            rel_type_set = list({n.get("rel_type", "") for n in neighbors if n.get("rel_type")})
            neighbor_keys = [n["neighbor_key"] for n in neighbors if n.get("neighbor_key")]

            beats.append({
                "beat_id": str(_uuid.uuid4()),
                "title": concept.get("label_en") or canonical_key,
                "intent": ", ".join(rel_type_set[:3]) or "RELATES_TO",
                "related_concepts": neighbor_keys[:8],
            })

        return {"beats": beats} if beats else None

    except Exception as exc:
        logger.warning("graph_plan_failed", error=str(exc))
        return None


# ─── Endpoint ────────────────────────────────────────────────────────────────

@router.post("/search", response_model=HybridSearchResponse, summary="Hybrid vector search")
async def search(body: HybridSearchRequest, user: CurrentUserDep):
    """Search the knowledge base using multilingual vector similarity.

    Embeds the query with Qwen3-Embedding-8B (same model used at ingest time),
    then performs approximate nearest-neighbour search in Qdrant with optional
    payload filters (book, chunk type, language, page range).

    A query in Marathi retrieves matching English and Hindi chunks because all
    languages share the same embedding space.

    When ``graph.enable=true``, also queries Neo4j for Concept nodes matching
    the query text and returns outline beats with related concept neighbors.
    """
    # 1. Sanitize
    clean_query = _sanitize(body.query)
    if not clean_query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Query is empty after sanitization.",
        )

    logger.info(
        "search_request",
        user_id=str(user.user_id),
        query_len=len(clean_query),
        top_k=body.top_k,
        filters={
            "book_ids": body.filters.book_ids,
            "chunk_types": body.filters.chunk_types,
            "languages": body.filters.languages,
        },
    )

    # 2. Embed
    query_vector = await _embed(clean_query)

    # 3. Qdrant ANN search
    f = body.filters
    hits = await vector_search(
        collection=settings.QDRANT_COLLECTION_NAME,
        query_vector=query_vector,
        top_k=body.top_k,
        book_ids=f.book_ids or None,
        chunk_types=f.chunk_types or None,
        languages=f.languages or None,
        page_min=f.page_min,
        page_max=f.page_max,
    )

    # 4. Format results
    results: list[SearchResult] = []
    for hit in hits:
        p = hit["payload"]
        text = p.get("text", "")
        results.append(SearchResult(
            chunk_id=hit["id"],
            book_id=p.get("book_id", ""),
            book_name=p.get("book_name"),
            chapter_id=p.get("chapter_id"),
            chunk_type=p.get("chunk_type", "general"),
            language_detected=p.get("language_detected", "unknown"),
            page_start=p.get("page_start"),
            page_end=p.get("page_end"),
            section_title=p.get("section_title"),
            score=round(hit["score"], 4),
            text_preview=text[:300] + ("\u2026" if len(text) > 300 else ""),
            citations=[Citation(
                book_id=p.get("book_id", ""),
                book_name=p.get("book_name", ""),
                language=p.get("language", ""),
                page_start=p.get("page_start"),
                page_end=p.get("page_end"),
            )],
        ))

    # 5. Graph-augmented outline beats (only when graph.enable=True)
    graph_plan = await _build_graph_plan(clean_query, body.graph) if body.graph.enable else None

    logger.info("search_complete", results=len(results), graph_enabled=body.graph.enable)
    return HybridSearchResponse(
        query=clean_query,
        total=len(results),
        results=results,
        graph_plan=graph_plan,
    )
