"""Shared fixtures for Creator GraphRAG API integration tests.

Services expected to be running (from .env):
    - PostgreSQL   — real DB writes per test
    - Redis        — real JTI revocation, auth rate-limiting
    - Qdrant       — real vector search (chunks_multilingual collection)
    - Ollama       — real qwen3-embedding:8b embeddings

S3/MinIO object-existence checks are mocked inline per test because we
cannot upload real files in a unit test. The presigned-URL signing
(CPU-only) and the Celery enqueue (fails gracefully in the router) are
left unmocked.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import AsyncGenerator

import httpx
import pytest
import structlog
from httpx import ASGITransport

# ── Structlog: plain text renderer so Windows cp1252 doesn't crash on box chars ─
# Note: PrintLoggerFactory creates PrintLogger objects that have no .name attribute,
# so we must NOT use structlog.stdlib.add_logger_name here.
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(colors=False),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
)

from app.main import app  # noqa: E402 (must come after structlog config)


# ── Session-scoped event loop ──────────────────────────────────────────────────
# SQLAlchemy's asyncpg pool is a module-level singleton. All tests must share
# the same event loop so the pool's connections remain valid across tests.


@pytest.fixture(scope="session")
def event_loop():
    """Single event loop for the entire test session."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


# ── HTTP test client ───────────────────────────────────────────────────────────


@pytest.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """ASGI test client wired to the FastAPI app (no real network)."""
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


# ── Auth helpers ───────────────────────────────────────────────────────────────


def unique_email() -> str:
    """Generate a collision-free email for test isolation."""
    return f"test_{uuid.uuid4().hex[:10]}@example.com"


async def do_register(
    client: httpx.AsyncClient,
    *,
    email: str | None = None,
    password: str = "IntegrationTest123!",
    display_name: str = "Test User",
) -> dict:
    """Register a user and return the full token JSON payload + ``_email`` key."""
    used_email = email or unique_email()
    r = await client.post(
        "/v1/auth/register",
        json={
            "email": used_email,
            "password": password,
            "display_name": display_name,
        },
    )
    assert r.status_code == 201, r.text
    return {**r.json(), "_email": used_email}


@pytest.fixture
async def auth_data(client: httpx.AsyncClient) -> dict:
    """Register a fresh user per test; return tokens + _email."""
    return await do_register(client)


@pytest.fixture
async def auth_headers(auth_data: dict) -> dict[str, str]:
    """Bearer Authorization header for a freshly-registered test user."""
    return {"Authorization": f"Bearer {auth_data['access_token']}"}
