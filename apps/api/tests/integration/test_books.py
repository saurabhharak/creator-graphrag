"""Integration tests for /v1/books and /v1/jobs endpoints.

Real services used: PostgreSQL, Redis.

S3 note: presigned-URL signing is CPU-only and uses real boto3 credentials
from .env. The HEAD-object calls (object_exists / get_object_size) are
patched per-test because we do not upload real files during tests.

Celery: enqueue_ingestion may fail if no broker is reachable; the router
catches the exception and returns the job as 'queued' anyway.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

_VALID_BOOK = {
    "title": "Introduction to Natural Farming",
    "author": "Masanobu Fukuoka",
    "year": 1978,
    "language_primary": "en",
    "tags": ["agriculture", "organic"],
}

# Patch targets for S3 HEAD-object calls (only where file presence matters)
_PATCH_EXISTS = "app.api.v1.routers.books.object_exists"
_PATCH_SIZE = "app.api.v1.routers.books.get_object_size"


async def _create_book(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    payload: dict | None = None,
) -> dict:
    """POST /v1/books and return the response body (presigned URL is real)."""
    r = await client.post("/v1/books", json=payload or _VALID_BOOK, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# ── Create book ───────────────────────────────────────────────────────────────


async def test_create_book_success(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """POST /books → 201 with book_id and a presigned upload URL."""
    r = await client.post("/v1/books", json=_VALID_BOOK, headers=auth_headers)

    assert r.status_code == 201
    body = r.json()
    assert "book_id" in body
    upload = body["upload"]
    assert upload["upload_method"] == "presigned_put"
    assert "https://" in upload["url"] or "http://" in upload["url"]
    assert "expires_at" in upload


async def test_create_book_unauthorized(client: httpx.AsyncClient):
    """POST /books without auth → 401."""
    r = await client.post("/v1/books", json=_VALID_BOOK)
    assert r.status_code == 401


async def test_create_book_invalid_language(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """language_primary not in allowed set → 422."""
    r = await client.post(
        "/v1/books", json={**_VALID_BOOK, "language_primary": "french"}, headers=auth_headers
    )
    assert r.status_code == 422


async def test_create_book_missing_title(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """Missing required field 'title' → 422."""
    payload = {k: v for k, v in _VALID_BOOK.items() if k != "title"}
    r = await client.post("/v1/books", json=payload, headers=auth_headers)
    assert r.status_code == 422


# ── Get / List books ──────────────────────────────────────────────────────────


async def test_get_book_not_found(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """GET /books/{random_uuid} → 404."""
    r = await client.get(f"/v1/books/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


async def test_get_book_success(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """GET /books/{book_id} returns full detail of the created book."""
    created = await _create_book(client, auth_headers)
    book_id = created["book_id"]

    r = await client.get(f"/v1/books/{book_id}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["book_id"] == book_id
    assert body["title"] == _VALID_BOOK["title"]
    assert body["language_primary"] == _VALID_BOOK["language_primary"]
    assert body["ingestion_status"] is None  # no job yet


async def test_list_books_shows_created_book(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """After creating a book, GET /books includes it."""
    created = await _create_book(client, auth_headers)
    book_id = created["book_id"]

    r = await client.get("/v1/books", headers=auth_headers)
    assert r.status_code == 200
    ids = [item["book_id"] for item in r.json()["items"]]
    assert book_id in ids


# ── Upload complete ───────────────────────────────────────────────────────────


async def test_upload_complete_when_file_exists(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """POST /{id}/upload-complete — file exists in S3 → 200 verified."""
    created = await _create_book(client, auth_headers)
    book_id = created["book_id"]

    # Patch only the HEAD-object calls; everything else is real
    with (
        patch(_PATCH_EXISTS, new_callable=AsyncMock, return_value=True),
        patch(_PATCH_SIZE, new_callable=AsyncMock, return_value=5_242_880),
    ):
        r = await client.post(
            f"/v1/books/{book_id}/upload-complete",
            json={},
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert r.json()["status"] == "verified"


async def test_upload_complete_file_not_in_s3_returns_400(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """POST /{id}/upload-complete when no object in S3 → 400 naturally."""
    created = await _create_book(client, auth_headers)
    book_id = created["book_id"]

    # object_exists returns False — no file was uploaded
    with patch(_PATCH_EXISTS, new_callable=AsyncMock, return_value=False):
        r = await client.post(
            f"/v1/books/{book_id}/upload-complete",
            json={},
            headers=auth_headers,
        )
    assert r.status_code == 400


# ── Start ingestion ───────────────────────────────────────────────────────────


async def _verified_book(client, headers) -> str:
    """Create a book and mark its upload as verified. Returns book_id."""
    created = await _create_book(client, headers)
    book_id = created["book_id"]
    with (
        patch(_PATCH_EXISTS, new_callable=AsyncMock, return_value=True),
        patch(_PATCH_SIZE, new_callable=AsyncMock, return_value=5_242_880),
    ):
        r = await client.post(
            f"/v1/books/{book_id}/upload-complete", json={}, headers=headers
        )
    assert r.status_code == 200
    return book_id


async def test_start_ingestion_returns_queued_job(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """POST /{id}/ingest after verified upload → 200 with job_id status=queued.

    Celery enqueue may fail (no broker needed); the router handles it gracefully.
    """
    book_id = await _verified_book(client, auth_headers)

    r = await client.post(f"/v1/books/{book_id}/ingest", json={}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "job_id" in body
    assert body["status"] == "queued"


async def test_start_ingestion_before_upload_returns_400(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """POST /{id}/ingest before confirming upload → 400 UPLOAD_NOT_VERIFIED."""
    created = await _create_book(client, auth_headers)

    r = await client.post(
        f"/v1/books/{created['book_id']}/ingest", json={}, headers=auth_headers
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "UPLOAD_NOT_VERIFIED"


# ── Job polling ───────────────────────────────────────────────────────────────


async def test_get_job_status(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """GET /jobs/{job_id} returns status for a queued job."""
    book_id = await _verified_book(client, auth_headers)
    r_ingest = await client.post(
        f"/v1/books/{book_id}/ingest", json={}, headers=auth_headers
    )
    job_id = r_ingest.json()["job_id"]

    r = await client.get(f"/v1/jobs/{job_id}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == job_id
    assert body["status"] == "queued"
    assert "stage" in body
    assert "progress" in body


async def test_get_job_not_found(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """GET /jobs/{random_uuid} → 404."""
    r = await client.get(f"/v1/jobs/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404


# ── Delete book ───────────────────────────────────────────────────────────────


async def test_delete_book_then_get_returns_404(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    """DELETE /books/{id} → 204; subsequent GET → 404."""
    created = await _create_book(client, auth_headers)
    book_id = created["book_id"]

    assert (await client.delete(f"/v1/books/{book_id}", headers=auth_headers)).status_code == 204
    assert (await client.get(f"/v1/books/{book_id}", headers=auth_headers)).status_code == 404


async def test_delete_nonexistent_book_returns_404(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    r = await client.delete(f"/v1/books/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404


# ── Job history ───────────────────────────────────────────────────────────────


async def test_list_book_jobs_empty_before_ingest(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    created = await _create_book(client, auth_headers)
    r = await client.get(f"/v1/books/{created['book_id']}/jobs", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["jobs"] == []


async def test_list_book_jobs_after_ingest(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
):
    book_id = await _verified_book(client, auth_headers)
    await client.post(f"/v1/books/{book_id}/ingest", json={}, headers=auth_headers)

    r = await client.get(f"/v1/books/{book_id}/jobs", headers=auth_headers)
    jobs = r.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["status"] == "queued"
