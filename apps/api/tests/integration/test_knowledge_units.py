"""Integration tests for /v1/knowledge-units endpoints.

Real services used: PostgreSQL, Redis.

Knowledge units are seeded directly via asyncpg (bypassing the Celery worker
LLM pipeline) so the test suite runs without a Zenmux/OpenAI connection.

A separate live-extraction test (test_live_extraction) calls gpt-4.1 via
Zenmux and is skipped when OPENAI_API_KEY is not set.

Test isolation: each fixture creates a fresh book + KUs and deletes them in
teardown, so tests can run in any order without DB conflicts.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator

import asyncpg
import httpx
import pytest

from tests.conftest import do_register

# ── Book payload ──────────────────────────────────────────────────────────────

_VALID_BOOK = {
    "title": "Integration Test Agriculture Book",
    "author": "Test Author",
    "year": 2020,
    "language_primary": "en",
    "tags": ["test", "agriculture"],
}

# ── DB helpers ────────────────────────────────────────────────────────────────

_INSERT_KU_SQL = """
    INSERT INTO knowledge_units (
        unit_id, source_book_id, source_chunk_id,
        type, language_detected, language_confidence,
        subject, predicate, object,
        payload_jsonb, confidence, status,
        evidence_jsonb, canonical_key
    ) VALUES (
        $1::uuid, $2::uuid, $3,
        $4, $5, $6,
        $7, $8, $9,
        $10::jsonb, $11, $12,
        $13::jsonb, $14
    )
"""


def _ku_row(
    book_id: str,
    *,
    unit_type: str = "claim",
    status: str = "extracted",
    subject: str = "compost",
    predicate: str = "improves",
    obj: str = "soil fertility",
    confidence: float = 0.88,
) -> tuple:
    """Build an asyncpg parameter tuple for INSERT INTO knowledge_units."""
    uid = str(uuid.uuid4())
    evidence = json.dumps([{
        "book_id": book_id,
        "chapter_id": "",
        "page_start": 1,
        "page_end": 2,
        "snippet": f"{subject} {predicate} {obj}.",
    }])
    return (
        uid,          # $1 unit_id
        book_id,      # $2 source_book_id
        None,         # $3 source_chunk_id (nullable)
        unit_type,    # $4 type
        "en",         # $5 language_detected
        0.95,         # $6 language_confidence
        subject,      # $7 subject
        predicate,    # $8 predicate
        obj,          # $9 object
        "{}",         # $10 payload_jsonb
        confidence,   # $11 confidence
        status,       # $12 status
        evidence,     # $13 evidence_jsonb
        subject.lower().replace(" ", "_"),  # $14 canonical_key
    )


def _raw_db_url() -> str:
    from app.core.config import settings
    return settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def ku_context(
    client: httpx.AsyncClient,
) -> AsyncGenerator[tuple[dict, str, list[str]], None]:
    """Register a user, create a book, seed 3 knowledge units.

    Yields:
        (auth_headers, book_id, [unit_id_0, unit_id_1, unit_id_2])

    unit_0: claim,      status=extracted,    confidence=0.88
    unit_1: definition, status=needs_review, confidence=0.55
    unit_2: process,    status=extracted,    confidence=0.90
    """
    # 1. Register user (default role: editor)
    data = await do_register(client)
    headers = {"Authorization": f"Bearer {data['access_token']}"}

    # 2. Create book via API
    r = await client.post("/v1/books", json=_VALID_BOOK, headers=headers)
    assert r.status_code == 201, r.text
    book_id = r.json()["book_id"]

    # 3. Seed KUs directly via asyncpg (no LLM call needed)
    rows = [
        _ku_row(book_id, unit_type="claim",      status="extracted",    confidence=0.88),
        _ku_row(book_id, unit_type="definition",  status="needs_review", confidence=0.55),
        _ku_row(book_id, unit_type="process",     status="extracted",    confidence=0.90),
    ]
    unit_ids = [row[0] for row in rows]

    conn: asyncpg.Connection = await asyncpg.connect(_raw_db_url())
    try:
        for row in rows:
            await conn.execute(_INSERT_KU_SQL, *row)
    finally:
        await conn.close()

    yield headers, book_id, unit_ids

    # Teardown — delete units then book (FK order)
    conn2: asyncpg.Connection = await asyncpg.connect(_raw_db_url())
    try:
        for uid in unit_ids:
            await conn2.execute(
                "DELETE FROM knowledge_units WHERE unit_id = $1::uuid", uid
            )
        await conn2.execute(
            "DELETE FROM books WHERE book_id = $1::uuid", book_id
        )
    finally:
        await conn2.close()


# ── List tests ────────────────────────────────────────────────────────────────


async def test_list_knowledge_units_empty(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """GET /knowledge-units with no seeded units → 200, items is an empty list."""
    r = await client.get("/v1/knowledge-units", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert isinstance(body["items"], list)


async def test_list_returns_seeded_units(
    client: httpx.AsyncClient,
    ku_context: tuple,
):
    """GET /knowledge-units?book_id=X → 200, all 3 seeded units returned."""
    headers, book_id, unit_ids = ku_context
    r = await client.get(f"/v1/knowledge-units?book_id={book_id}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    returned_ids = {item["unit_id"] for item in body["items"]}
    for uid in unit_ids:
        assert uid in returned_ids, f"unit {uid} missing from list response"


async def test_list_filter_status_needs_review(
    client: httpx.AsyncClient,
    ku_context: tuple,
):
    """Filter by status=needs_review → exactly 1 unit returned."""
    headers, book_id, _ = ku_context
    r = await client.get(
        f"/v1/knowledge-units?book_id={book_id}&status=needs_review",
        headers=headers,
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "needs_review"


async def test_list_filter_type_definition(
    client: httpx.AsyncClient,
    ku_context: tuple,
):
    """Filter by type=definition → exactly 1 unit returned."""
    headers, book_id, _ = ku_context
    r = await client.get(
        f"/v1/knowledge-units?book_id={book_id}&type=definition",
        headers=headers,
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["type"] == "definition"


async def test_list_pagination_limit(
    client: httpx.AsyncClient,
    ku_context: tuple,
):
    """limit=2 → only 2 items, next_cursor is non-null when more exist."""
    headers, book_id, _ = ku_context
    r = await client.get(
        f"/v1/knowledge-units?book_id={book_id}&limit=2",
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None


async def test_list_requires_auth(client: httpx.AsyncClient):
    """GET /knowledge-units without token → 401."""
    r = await client.get("/v1/knowledge-units")
    assert r.status_code == 401


# ── Get by ID tests ───────────────────────────────────────────────────────────


async def test_get_unit_by_id(
    client: httpx.AsyncClient,
    ku_context: tuple,
):
    """GET /knowledge-units/{id} → 200, unit fields + empty edit_history."""
    headers, book_id, unit_ids = ku_context
    uid = unit_ids[0]
    r = await client.get(f"/v1/knowledge-units/{uid}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["unit_id"] == uid
    assert body["source_book_id"] == book_id
    assert body["type"] == "claim"
    assert "edit_history" in body
    assert isinstance(body["edit_history"], list)


async def test_get_unit_not_found(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """GET /knowledge-units/{random-uuid} → 404."""
    r = await client.get(f"/v1/knowledge-units/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404


async def test_get_unit_requires_auth(
    client: httpx.AsyncClient,
    ku_context: tuple,
):
    """GET /knowledge-units/{id} without token → 401."""
    _, _, unit_ids = ku_context
    r = await client.get(f"/v1/knowledge-units/{unit_ids[0]}")
    assert r.status_code == 401


# ── Patch tests ───────────────────────────────────────────────────────────────


async def test_patch_approve_unit(
    client: httpx.AsyncClient,
    ku_context: tuple,
):
    """PATCH with status=approved → 200, unit.status == approved."""
    headers, _, unit_ids = ku_context
    uid = unit_ids[1]  # needs_review unit
    r = await client.patch(
        f"/v1/knowledge-units/{uid}",
        json={"status": "approved"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"


async def test_patch_reject_unit(
    client: httpx.AsyncClient,
    ku_context: tuple,
):
    """PATCH with status=rejected → 200, unit.status == rejected."""
    headers, _, unit_ids = ku_context
    uid = unit_ids[0]
    r = await client.patch(
        f"/v1/knowledge-units/{uid}",
        json={"status": "rejected", "editor_note": "off topic"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"


async def test_patch_updates_subject(
    client: httpx.AsyncClient,
    ku_context: tuple,
):
    """PATCH with new subject → 200, subject is updated in response."""
    headers, _, unit_ids = ku_context
    uid = unit_ids[0]
    r = await client.patch(
        f"/v1/knowledge-units/{uid}",
        json={"subject": "worm castings", "editor_note": "corrected terminology"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["subject"] == "worm castings"


async def test_patch_creates_audit_record(
    client: httpx.AsyncClient,
    ku_context: tuple,
):
    """PATCH → subsequent GET shows 1+ edit_history entries."""
    headers, _, unit_ids = ku_context
    uid = unit_ids[0]
    await client.patch(
        f"/v1/knowledge-units/{uid}",
        json={"status": "approved"},
        headers=headers,
    )
    r = await client.get(f"/v1/knowledge-units/{uid}", headers=headers)
    assert r.status_code == 200
    assert len(r.json()["edit_history"]) >= 1


async def test_patch_unit_not_found(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """PATCH non-existent unit → 404."""
    r = await client.patch(
        f"/v1/knowledge-units/{uuid.uuid4()}",
        json={"status": "approved"},
        headers=auth_headers,
    )
    assert r.status_code == 404


async def test_patch_invalid_status(
    client: httpx.AsyncClient,
    ku_context: tuple,
):
    """PATCH with invalid status string → 422."""
    headers, _, unit_ids = ku_context
    r = await client.patch(
        f"/v1/knowledge-units/{unit_ids[0]}",
        json={"status": "bogus_status"},
        headers=headers,
    )
    assert r.status_code == 422


async def test_patch_requires_auth(
    client: httpx.AsyncClient,
    ku_context: tuple,
):
    """PATCH without token → 401."""
    _, _, unit_ids = ku_context
    r = await client.patch(
        f"/v1/knowledge-units/{unit_ids[0]}",
        json={"status": "approved"},
    )
    assert r.status_code == 401


# ── Bulk update tests ─────────────────────────────────────────────────────────


async def test_bulk_approve_all(
    client: httpx.AsyncClient,
    ku_context: tuple,
):
    """POST /bulk-update action=approve for all 3 units → succeeded=3, failed=0."""
    headers, _, unit_ids = ku_context
    r = await client.post(
        "/v1/knowledge-units/bulk-update",
        json={
            "unit_ids": unit_ids,
            "action": "approve",
            "editor_note": "bulk approval test",
        },
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["succeeded"] == 3
    assert body["failed"] == 0
    assert body["errors"] == []


async def test_bulk_reject(
    client: httpx.AsyncClient,
    ku_context: tuple,
):
    """POST /bulk-update action=reject → all units rejected."""
    headers, _, unit_ids = ku_context
    r = await client.post(
        "/v1/knowledge-units/bulk-update",
        json={"unit_ids": unit_ids, "action": "reject"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["succeeded"] == 3


async def test_bulk_update_partial_invalid_ids(
    client: httpx.AsyncClient,
    ku_context: tuple,
):
    """Mixing real + nonexistent IDs → partial success, errors list populated."""
    headers, _, unit_ids = ku_context
    fake_id = str(uuid.uuid4())
    r = await client.post(
        "/v1/knowledge-units/bulk-update",
        json={"unit_ids": [unit_ids[0], fake_id], "action": "approve"},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["succeeded"] == 1
    assert body["failed"] == 1
    assert any(e["unit_id"] == fake_id for e in body["errors"])


async def test_bulk_update_requires_auth(client: httpx.AsyncClient):
    """POST /bulk-update without token → 401."""
    r = await client.post(
        "/v1/knowledge-units/bulk-update",
        json={"unit_ids": [str(uuid.uuid4())], "action": "approve"},
    )
    assert r.status_code == 401


async def test_bulk_update_invalid_action(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """POST /bulk-update with invalid action → 422."""
    r = await client.post(
        "/v1/knowledge-units/bulk-update",
        json={"unit_ids": [str(uuid.uuid4())], "action": "delete"},
        headers=auth_headers,
    )
    assert r.status_code == 422


# ── Live LLM extraction smoke test ────────────────────────────────────────────


def _zenmux_key() -> str | None:
    """Return the configured OpenAI/Zenmux key, or None if unset/placeholder."""
    from app.core.config import settings
    k = settings.OPENAI_API_KEY
    return k if k and k != "sk-placeholder" else None


@pytest.mark.skipif(
    _zenmux_key() is None,
    reason="OPENAI_API_KEY not configured or is placeholder — skipping live Zenmux extraction",
)
async def test_live_extraction_via_zenmux():
    """Smoke-test: call Zenmux gpt-4.1 to extract KUs from a short farming passage.

    Validates:
    - Zenmux endpoint (https://zenmux.ai/api/v1) is reachable with the key
    - gpt-4.1 returns valid JSON with a 'units' array
    - At least one unit has the expected schema (type, confidence, evidence)
    """
    import json as _json
    import openai

    from app.core.config import settings

    _FARMING_TEXT = (
        "Compost improves soil fertility by adding organic matter and beneficial microorganisms. "
        "A healthy soil microbiome is defined as a diverse community of bacteria and fungi "
        "that break down organic material into plant-available nutrients. "
        "The composting process requires carbon-rich materials, nitrogen-rich materials, "
        "moisture, and oxygen to decompose organic waste effectively."
    )
    _SYSTEM_PROMPT = (
        "You are a knowledge extraction engine. Given agricultural text, "
        "extract knowledge units as JSON matching this schema exactly: "
        '{"units": [{"type": "claim|definition|process", "language": "en", '
        '"subject": "string", "predicate": "string", "object": "string", '
        '"confidence": 0.0, "evidence": [{"book_id": "test", "chapter_id": "", '
        '"page_start": 1, "page_end": 1, "snippet": "short quote"}]}]}. '
        "Return ONLY valid JSON, no markdown."
    )

    client = openai.AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url="https://zenmux.ai/api/v1",
    )
    response = await client.chat.completions.create(
        model="openai/gpt-4.1",
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _FARMING_TEXT},
        ],
        temperature=0.1,
        max_tokens=800,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content or ""
    data = _json.loads(content)
    assert "units" in data, f"Response missing 'units' key: {content[:300]}"
    units = data["units"]
    assert len(units) >= 1, "Expected at least 1 extracted unit"
    assert response.usage.prompt_tokens > 0
    assert response.usage.completion_tokens > 0

    # Validate first unit structure
    u = units[0]
    assert u.get("type") in ("claim", "definition", "process", "comparison"), \
        f"Unexpected type: {u.get('type')}"
    assert "confidence" in u and 0.0 <= float(u["confidence"]) <= 1.0
    assert "evidence" in u and len(u["evidence"]) >= 1
