"""FastAPI application entry point."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.api.v1.routers import (
    api_keys,
    auth,
    books,
    evidence,
    graph,
    health,
    jobs,
    knowledge_units,
    search,
    templates,
    video_packages,
    webhooks,
)
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging

logger = structlog.get_logger(__name__)

# Configure logging EARLY — before any DB/Neo4j imports trigger queries
configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown of infrastructure connections."""

    # Verify DB connectivity on startup (non-fatal: app starts even if DB is temporarily down)
    from sqlalchemy import text as _text
    from app.infrastructure.db.session import engine
    try:
        async with engine.connect() as conn:
            await conn.execute(_text("SELECT 1"))
        logger.info("db_connected")
    except Exception as exc:
        logger.error("db_connect_failed_on_startup", error=str(exc))

    # TODO(#0): initialize Qdrant client and verify collection

    # Initialize Neo4j driver (non-fatal: graph browse works independently of ingestion)
    from app.infrastructure.graph import neo4j_client as graph_neo4j
    try:
        reachable = await graph_neo4j.is_reachable()
        if reachable:
            logger.info("neo4j_connected")
        else:
            logger.warning("neo4j_unreachable_on_startup")
    except Exception as exc:
        logger.error("neo4j_connect_failed_on_startup", error=str(exc))

    yield

    # Teardown
    await engine.dispose()
    logger.info("db_pool_disposed")
    await graph_neo4j.close_driver()
    logger.info("neo4j_driver_closed")


limiter = Limiter(key_func=get_remote_address)


async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return 429 errors in the standard ErrorResponse envelope."""
    retry_after = getattr(exc, "retry_after", 60)
    response = JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "error": {
                "code": "RATE_LIMIT_EXCEEDED",
                "message": f"Rate limit exceeded. Retry after {retry_after} seconds.",
                "details": {"retry_after": retry_after},
                "trace_id": structlog.contextvars.get_contextvars().get(
                    "trace_id", str(uuid.uuid4())
                ),
            }
        },
    )
    response.headers["Retry-After"] = str(retry_after)
    return response


app = FastAPI(
    title="Creator GraphRAG API",
    description="Multilingual Book Knowledge Base → GraphRAG Video Content Generator",
    version=settings.APP_VERSION,
    docs_url="/docs" if settings.APP_ENV != "production" else None,
    redoc_url="/redoc" if settings.APP_ENV != "production" else None,
    lifespan=lifespan,
)

# Rate limiting — custom handler enforces ErrorResponse envelope (STANDARDS.md §3.10)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)


@app.middleware("http")
async def bind_trace_context(request: Request, call_next):
    """Extract W3C traceparent header and bind trace_id to structlog context (STANDARDS.md §9.3)."""
    traceparent = request.headers.get("traceparent", "")
    parts = traceparent.split("-")
    # W3C format: 00-{trace_id}-{parent_id}-{flags}
    trace_id = parts[1] if len(parts) >= 4 else str(uuid.uuid4())

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        service="api",
        trace_id=trace_id,
    )
    response = await call_next(request)
    return response


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Exception handlers
register_exception_handlers(app)

# Routers
app.include_router(health.router, tags=["health"])
app.include_router(auth.router, prefix="/v1/auth", tags=["auth"])
app.include_router(api_keys.router, prefix="/v1/api-keys", tags=["api-keys"])
app.include_router(books.router, prefix="/v1/books", tags=["books"])
app.include_router(jobs.router, prefix="/v1/jobs", tags=["jobs"])
app.include_router(knowledge_units.router, prefix="/v1/knowledge-units", tags=["knowledge-units"])
app.include_router(search.router, prefix="/v1", tags=["search"])
app.include_router(video_packages.router, prefix="/v1/video-packages", tags=["video-packages"])
app.include_router(evidence.router, prefix="/v1/evidence", tags=["evidence"])
app.include_router(graph.router, prefix="/v1/graph", tags=["graph"])
app.include_router(templates.router, prefix="/v1/templates", tags=["templates"])
app.include_router(webhooks.router, prefix="/v1/webhooks", tags=["webhooks"])
