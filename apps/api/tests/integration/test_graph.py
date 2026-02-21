"""Integration tests for /v1/graph endpoints (Neo4j knowledge graph browse).

Real services used: PostgreSQL, Redis, Neo4j (skipped if unreachable).

Strategy:
- Tests first check Neo4j connectivity; if Neo4j is not running they are
  skipped automatically (not failed) so CI without Neo4j still passes.
- Tests that read-only (list/get/neighbors) use the actual graph state;
  they work against an empty graph or a populated one.
- The merge concept endpoint is a stub and always returns a fixed response.
"""
from __future__ import annotations

import pytest
import httpx

# ── Neo4j reachability check ──────────────────────────────────────────────────


async def _neo4j_available() -> bool:
    """Return True if the Neo4j bolt endpoint responds."""
    try:
        from app.infrastructure.graph import neo4j_client as graph_db
        return await graph_db.is_reachable()
    except Exception:
        return False


def neo4j_skip():
    """Decorator that marks a test to be skipped if Neo4j is unreachable."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        available = loop.run_until_complete(_neo4j_available())
    except Exception:
        available = False
    return pytest.mark.skipif(not available, reason="Neo4j not reachable — skipping graph tests")


# ── Auth guard tests (no Neo4j needed) ───────────────────────────────────────


async def test_list_concepts_requires_auth(client: httpx.AsyncClient):
    """GET /graph/concepts without token → 401."""
    r = await client.get("/v1/graph/concepts")
    assert r.status_code == 401


async def test_get_concept_requires_auth(client: httpx.AsyncClient):
    """GET /graph/concepts/{key} without token → 401."""
    r = await client.get("/v1/graph/concepts/some_concept")
    assert r.status_code == 401


async def test_neighbors_requires_auth(client: httpx.AsyncClient):
    """GET /graph/concepts/{key}/neighbors without token → 401."""
    r = await client.get("/v1/graph/concepts/some_concept/neighbors")
    assert r.status_code == 401


# ── Read endpoints (require Neo4j) ────────────────────────────────────────────


async def test_list_concepts_empty_graph(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """GET /graph/concepts → 200 with concepts list (may be empty if graph not populated)."""
    r = await client.get("/v1/graph/concepts", headers=auth_headers)
    # If Neo4j is unreachable, router returns 503
    if r.status_code == 503:
        pytest.skip("Neo4j unavailable")
    assert r.status_code == 200
    body = r.json()
    assert "concepts" in body
    assert isinstance(body["concepts"], list)


async def test_list_concepts_with_limit(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """GET /graph/concepts?limit=5 → 200, at most 5 concepts returned."""
    r = await client.get("/v1/graph/concepts?limit=5", headers=auth_headers)
    if r.status_code == 503:
        pytest.skip("Neo4j unavailable")
    assert r.status_code == 200
    body = r.json()
    assert len(body["concepts"]) <= 5


async def test_list_concepts_search_no_match(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """GET /graph/concepts?q=xyzxyzxyz → 200, empty list (no matching concepts)."""
    r = await client.get("/v1/graph/concepts?q=xyzxyzxyz_nomatch", headers=auth_headers)
    if r.status_code == 503:
        pytest.skip("Neo4j unavailable")
    assert r.status_code == 200
    assert r.json()["concepts"] == []


async def test_get_concept_not_found(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """GET /graph/concepts/{nonexistent} → 404."""
    r = await client.get("/v1/graph/concepts/nonexistent_concept_key_xyz", headers=auth_headers)
    if r.status_code == 503:
        pytest.skip("Neo4j unavailable")
    assert r.status_code == 404


async def test_get_concept_neighbors_not_found(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """GET /graph/concepts/{nonexistent}/neighbors → 200, empty nodes/edges."""
    r = await client.get(
        "/v1/graph/concepts/nonexistent_concept_xyz/neighbors",
        headers=auth_headers,
    )
    if r.status_code == 503:
        pytest.skip("Neo4j unavailable")
    assert r.status_code == 200
    body = r.json()
    assert body["nodes"] == []
    assert body["edges"] == []


async def test_get_concept_neighbors_max_hops_validation(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """GET /graph/concepts/{key}/neighbors?max_hops=5 → 422 (max is 4)."""
    r = await client.get(
        "/v1/graph/concepts/some_concept/neighbors?max_hops=5",
        headers=auth_headers,
    )
    if r.status_code == 503:
        pytest.skip("Neo4j unavailable")
    assert r.status_code == 422


# ── Merge stub test (no Neo4j needed — always returns stub response) ──────────


async def test_merge_concepts_stub(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """POST /graph/concepts/{a}/merge/{b} → 200 stub response (APOC not implemented)."""
    r = await client.post(
        "/v1/graph/concepts/concept_a/merge/concept_b",
        headers=auth_headers,
    )
    if r.status_code == 503:
        pytest.skip("Neo4j unavailable")
    assert r.status_code == 200
    body = r.json()
    assert body["merged_key"] == "concept_b"
    assert body["into_key"] == "concept_a"
    assert "detail" in body


# ── Graph-augmented search ────────────────────────────────────────────────────


async def test_search_with_graph_disabled(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """POST /search with graph.enable=false → graph_plan is null (no Neo4j needed)."""
    r = await client.post(
        "/v1/search",
        json={
            "query": "compost soil fertility",
            "top_k": 3,
            "graph": {"enable": False},
        },
        headers=auth_headers,
    )
    # 503 if Ollama is down — skip cleanly
    if r.status_code == 503:
        pytest.skip("Ollama not available")
    assert r.status_code == 200
    assert r.json()["graph_plan"] is None


async def test_search_with_graph_enabled(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """POST /search with graph.enable=true → graph_plan is null or dict (Neo4j optional)."""
    r = await client.post(
        "/v1/search",
        json={
            "query": "compost improves soil",
            "top_k": 3,
            "graph": {"enable": True, "max_hops": 1},
        },
        headers=auth_headers,
    )
    if r.status_code == 503:
        pytest.skip("Ollama not available")
    assert r.status_code == 200
    body = r.json()
    # graph_plan is either None (empty graph) or {"beats": [...]}
    gp = body.get("graph_plan")
    assert gp is None or (isinstance(gp, dict) and "beats" in gp)


async def test_search_graph_plan_structure(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """When graph_plan is returned, it must have valid beats structure."""
    r = await client.post(
        "/v1/search",
        json={
            "query": "natural farming organic",
            "top_k": 5,
            "graph": {"enable": True},
        },
        headers=auth_headers,
    )
    if r.status_code == 503:
        pytest.skip("Ollama not available")
    assert r.status_code == 200
    body = r.json()
    gp = body.get("graph_plan")
    if gp is not None:
        assert "beats" in gp
        for beat in gp["beats"]:
            assert "beat_id" in beat
            assert "title" in beat
            assert "intent" in beat
            assert "related_concepts" in beat
            assert isinstance(beat["related_concepts"], list)
