"""Knowledge graph browse endpoints."""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.api.v1.deps import CurrentUserDep, EditorOrAdminDep
from app.infrastructure.graph import neo4j_client as graph_db

# Only allow alphanumeric + underscore relation types (must start with a letter).
_SAFE_REL_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,49}$")

router = APIRouter()


class MergeConceptRequest(BaseModel):
    preserve_key: str  # key_a — the concept to keep


def _build_mermaid(canonical_key: str, edges: list[dict]) -> str:
    """Build a Mermaid flowchart spec for a concept and its immediate neighbors."""
    lines = ["graph TD"]
    safe_key = canonical_key.replace(" ", "_")[:20]
    for edge in edges[:15]:  # cap at 15 edges to keep diagram readable
        neighbor_key = (edge.get("neighbor_key") or "unknown").replace(" ", "_")[:20]
        rel_type = edge.get("type", "RELATES_TO")
        neighbor_label = edge.get("neighbor_label") or neighbor_key
        lines.append(
            f'  {safe_key}["{canonical_key}"] -->|{rel_type}| {neighbor_key}["{neighbor_label}"]'
        )
    if not edges:
        lines.append(f'  {safe_key}["{canonical_key}"]')
    return "\n".join(lines)


@router.get("/concepts", summary="Search/list concept nodes")
async def list_concepts(
    user: CurrentUserDep,
    q: str | None = None,
    language: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
):
    """Search or list concepts in the knowledge graph.

    Returns canonical key, all language aliases, and evidence count.
    Filters by label containing q (case-insensitive substring match).
    """
    try:
        if q:
            q_lower = q.lower()
            cypher = """
                MATCH (c:Concept)
                WHERE toLower(coalesce(c.label_en, '')) CONTAINS $q
                   OR toLower(coalesce(c.label_mr, '')) CONTAINS $q
                   OR toLower(coalesce(c.label_hi, '')) CONTAINS $q
                RETURN c.canonical_key AS canonical_key,
                       c.label_en AS label_en,
                       c.label_mr AS label_mr,
                       c.label_hi AS label_hi
                LIMIT $limit
            """
            rows = await graph_db.run_read(cypher, {"q": q_lower, "limit": limit})
        else:
            cypher = """
                MATCH (c:Concept)
                RETURN c.canonical_key AS canonical_key,
                       c.label_en AS label_en,
                       c.label_mr AS label_mr,
                       c.label_hi AS label_hi
                LIMIT $limit
            """
            rows = await graph_db.run_read(cypher, {"limit": limit})
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Neo4j unavailable: {exc}",
        )

    concepts = [
        {
            "canonical_key": r.get("canonical_key"),
            "label_en": r.get("label_en"),
            "label_mr": r.get("label_mr"),
            "label_hi": r.get("label_hi"),
        }
        for r in rows
    ]
    return {"concepts": concepts, "next_cursor": None}


@router.get("/concepts/{canonical_key}", summary="Get concept node detail")
async def get_concept(canonical_key: str, user: CurrentUserDep):
    """Get a concept node with all aliases, edge summary, and Mermaid diagram spec."""
    try:
        rows = await graph_db.run_read(
            """
            MATCH (c:Concept {canonical_key: $key})
            OPTIONAL MATCH (c)-[r]-(n:Concept)
            RETURN
                c.canonical_key AS canonical_key,
                c.label_en AS label_en,
                c.label_mr AS label_mr,
                c.label_hi AS label_hi,
                coalesce(c.book_ids, []) AS book_ids,
                collect({
                    type: type(r),
                    neighbor_key: n.canonical_key,
                    neighbor_label: n.label_en,
                    confidence: r.confidence,
                    evidence_count: r.evidence_count
                }) AS edges
            """,
            {"key": canonical_key},
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Neo4j unavailable: {exc}",
        )

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Concept '{canonical_key}' not found",
        )

    row = rows[0]
    edges = [e for e in (row.get("edges") or []) if e.get("neighbor_key")]
    mermaid_spec = _build_mermaid(canonical_key, edges)

    return {
        "canonical_key": row.get("canonical_key"),
        "label_en": row.get("label_en"),
        "label_mr": row.get("label_mr"),
        "label_hi": row.get("label_hi"),
        "aliases": [
            v for v in [row.get("label_en"), row.get("label_mr"), row.get("label_hi")] if v
        ],
        "book_ids": row.get("book_ids") or [],
        "edge_summary": edges,
        "evidence_count": sum(e.get("evidence_count") or 0 for e in edges),
        "mermaid_spec": mermaid_spec,
    }


@router.get("/concepts/{canonical_key}/neighbors", summary="Traverse concept neighborhood")
async def get_concept_neighbors(
    canonical_key: str,
    user: CurrentUserDep,
    relation_types: list[str] = Query(default=[]),
    max_hops: int = Query(default=2, ge=1, le=4),
):
    """Traverse the graph from a concept node up to max_hops.

    Returns all nodes and edges in the neighborhood as flat lists suitable
    for graph visualization.
    """
    # Build optional relationship type filter — validate to prevent Cypher injection
    rel_filter = ""
    if relation_types:
        for rt in relation_types:
            if not _SAFE_REL_TYPE_RE.match(rt):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid relation type: '{rt}'. Only alphanumeric and underscore characters allowed.",
                )
        types_str = "|".join(relation_types)
        rel_filter = f":{types_str}"

    cypher = f"""
        MATCH p = (c:Concept {{canonical_key: $key}})-[r{rel_filter}*1..{max_hops}]-(n)
        WITH nodes(p) AS ns, relationships(p) AS rels
        UNWIND ns AS node
        WITH DISTINCT node, rels
        RETURN
            collect(DISTINCT {{
                canonical_key: node.canonical_key,
                label_en: node.label_en,
                label_mr: node.label_mr
            }}) AS nodes,
            [rel IN rels | {{
                type: type(rel),
                confidence: rel.confidence
            }}] AS edges
        LIMIT 1
    """

    try:
        rows = await graph_db.run_read(cypher, {"key": canonical_key})
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Neo4j unavailable: {exc}",
        )

    if not rows:
        return {"canonical_key": canonical_key, "nodes": [], "edges": []}

    row = rows[0]
    return {
        "canonical_key": canonical_key,
        "nodes": row.get("nodes") or [],
        "edges": row.get("edges") or [],
    }


@router.post(
    "/concepts/{key_a}/merge/{key_b}",
    status_code=status.HTTP_200_OK,
    summary="Merge two concept nodes",
)
async def merge_concepts(key_a: str, key_b: str, user: EditorOrAdminDep):
    """Merge concept key_b into key_a (Phase 3 — APOC merge not yet implemented).

    When implemented: all aliases from key_b added to key_a, all edges
    re-pointed from key_b to key_a, knowledge units referencing key_b updated.
    """
    return {
        "merged_key": key_b,
        "into_key": key_a,
        "aliases_added": [],
        "edges_repointed": 0,
        "units_updated": 0,
        "detail": "APOC concept merge is not yet implemented",
    }
